"""Tests for the source-content store (retrieval-quality plan Phase 2b).

Covers the repository round-trip against a migrated temp database and the
research-node hydration path: a url_content result carrying the reserved
``full_body`` metadata key must (a) persist the pre-window body to the store,
(b) upgrade the citation's material class to full/clipped, and (c) never leak
the body into stream events or tool_results_log.
"""

import pytest

from moira.persistence.interfaces import SourceContent
from moira.persistence.sqlite.repos import SqliteSourceContentRepository
from moira.persistence.sqlite.repos.source_contents import url_hash
from moira.persistence.sqlite.schema import run_migrations
from moira.service_setup import _services


@pytest.fixture
def repo(tmp_path):
    db_path = str(tmp_path / "test.db")
    run_migrations(db_path)
    return SqliteSourceContentRepository(db_path)


class TestSourceContentRepository:
    async def test_round_trip(self, repo):
        body = "x" * 12_345
        await repo.upsert(
            run_id="run-1",
            url="https://example.com/page",
            content=body,
            material_class="full",
            citation_id="cit001",
            fetched_at="2026-09-17T00:00:00+00:00",
        )
        got = await repo.get("run-1", "https://example.com/page")
        assert got is not None
        assert got.content == body
        assert got.material_class == "full"
        assert got.citation_id == "cit001"
        assert got.byte_size == len(body)
        assert got.truncated is False
        assert got.url_hash == url_hash("https://example.com/page")

    async def test_get_miss_is_run_scoped(self, repo):
        await repo.upsert(
            run_id="run-1",
            url="https://example.com/page",
            content="body",
            material_class="clipped",
        )
        # Same URL under a different run is a miss — the store is per-run.
        assert await repo.get("run-2", "https://example.com/page") is None
        assert await repo.get("run-1", "https://other.com") is None

    async def test_upsert_replaces_same_url(self, repo):
        await repo.upsert(
            run_id="run-1",
            url="https://example.com/page",
            content="old",
            material_class="clipped",
        )
        await repo.upsert(
            run_id="run-1",
            url="https://example.com/page",
            content="newer body",
            material_class="full",
            truncated=False,
        )
        got = await repo.get("run-1", "https://example.com/page")
        assert got.content == "newer body"
        assert got.material_class == "full"

    async def test_cross_run_upsert_same_url_is_independent(self, repo):
        # Regression: the key is the composite (run_id, url_hash). An old
        # schema keyed url_hash alone, so run B's fetch of a URL run A had
        # fetched stole the row (get for run A started missing). Two runs
        # fetching the same URL must keep their own rows.
        await repo.upsert(
            run_id="run-1",
            url="https://example.com/page",
            content="run-1 body",
            material_class="full",
        )
        await repo.upsert(
            run_id="run-2",
            url="https://example.com/page",
            content="run-2 body",
            material_class="clipped",
        )
        assert (await repo.get("run-1", "https://example.com/page")).content == "run-1 body"
        assert (await repo.get("run-2", "https://example.com/page")).content == "run-2 body"

    async def test_evict_run_leaves_other_runs_same_url(self, repo):
        # Eviction must be run-scoped even when other runs stored the same
        # URL — the asymmetry the old url_hash-primary-key schema caused.
        await repo.upsert(
            run_id="run-1",
            url="https://example.com/page",
            content="run-1 body",
            material_class="full",
        )
        await repo.upsert(
            run_id="run-2",
            url="https://example.com/page",
            content="run-2 body",
            material_class="full",
        )
        assert await repo.evict_run("run-1") == 1
        assert await repo.get("run-1", "https://example.com/page") is None
        got = await repo.get("run-2", "https://example.com/page")
        assert got is not None and got.content == "run-2 body"

    async def test_evict_run(self, repo):
        for i in range(3):
            await repo.upsert(
                run_id=f"run-{i}",
                url=f"https://example.com/{i}",
                content="body",
                material_class="full",
            )
        assert await repo.evict_run("run-1") == 1
        assert await repo.get("run-1", "https://example.com/1") is None
        assert await repo.get("run-0", "https://example.com/0") is not None

    async def test_truncated_flag_persists(self, repo):
        await repo.upsert(
            run_id="r",
            url="https://example.com/big",
            content="truncated-body",
            material_class="clipped",
            truncated=True,
        )
        got = await repo.get("r", "https://example.com/big")
        assert got.truncated is True
        assert isinstance(got, SourceContent)


class _FakeWriteQueue:
    """Captures enqueued async callables; tests await them explicitly."""

    def __init__(self):
        self.calls = []

    def enqueue(self, fn):
        self.calls.append(fn)

    async def drain(self):
        while self.calls:
            await self.calls.pop(0)()


class _FakeConfig:
    class source_store:  # noqa: N801 - simple namespace stub
        max_body_chars = 100_000


class _FakeSourceRepo:
    def __init__(self):
        self.rows = {}

    async def upsert(self, **kwargs):
        self.rows[kwargs["url"]] = kwargs


def _url_content_result(url="https://example.com/long", body_len=50_000):
    from moira.tools.base import ToolResult

    body = "z" * body_len
    return ToolResult(
        tool_name="url_content",
        output=f"URL: {url}\n\n{body}",
        success=True,
        duration_ms=100,
        metadata={
            "full_body": body,
            "results": [
                {"url": url, "title": "Example", "snippet": body[:500], "content": body[:5000]}
            ],
        },
    ), body


class TestHydrationThroughProcessExecutionResults:
    def _setup_services(self):
        fake_repo = _FakeSourceRepo()
        fake_queue = _FakeWriteQueue()
        saved = dict(_services)
        _services.clear()
        _services["source_content_repository"] = fake_repo
        _services["write_queue"] = fake_queue
        _services["config"] = _FakeConfig()
        return fake_repo, fake_queue, saved

    def _teardown(self, saved):
        _services.clear()
        _services.update(saved)

    async def test_full_body_stored_and_class_upgraded(self):
        from moira.models.knowledge import Citation
        from moira.tools.base import ToolCall
        from moira.workflow.nodes.research import _process_execution_results

        fake_repo, fake_queue, saved = self._setup_services()
        try:
            result, body = _url_content_result()
            call = ToolCall(
                id="c1", name="url_content", arguments={"url": "https://example.com/long"}
            )
            citations = [
                Citation(id="cit001", source="url_content", url="https://example.com/long")
            ]
            seen_urls = {"https://example.com/long": "cit001"}
            events = []

            _process_execution_results(
                [result],
                [call],
                events.append,
                citations,
                seen_urls,
                [],
                [],
                [],
                {},
                {"url_content": 3.0},
                100.0,
                0.0,
                run_id="run-abc",
            )
            await fake_queue.drain()

            cit = citations[0]
            assert cit["depth"] == "full"  # 50K fits the 100K default cap
            assert cit["byte_size"] == len(body)
            assert len(cit["content"]) <= 5000  # serving window unchanged
            assert fake_repo.rows["https://example.com/long"]["material_class"] == "full"
            assert fake_repo.rows["https://example.com/long"]["run_id"] == "run-abc"
            assert fake_repo.rows["https://example.com/long"]["content"] == body
        finally:
            self._teardown(saved)

    def test_stream_and_log_never_see_full_body(self):
        from moira.models.knowledge import Citation
        from moira.tools.base import ToolCall
        from moira.workflow.nodes.research import _process_execution_results

        fake_repo, fake_queue, saved = self._setup_services()
        try:
            result, body = _url_content_result()
            call = ToolCall(
                id="c1", name="url_content", arguments={"url": "https://example.com/long"}
            )
            citations = [
                Citation(id="cit001", source="url_content", url="https://example.com/long")
            ]
            seen_urls = {"https://example.com/long": "cit001"}
            events = []
            tool_log = []

            _process_execution_results(
                [result],
                [call],
                events.append,
                citations,
                seen_urls,
                [],
                [],
                tool_log,
                {},
                {"url_content": 3.0},
                100.0,
                0.0,
                run_id="r",
            )

            stream_payload = events[0]["payload"]
            assert "full_body" not in stream_payload["metadata"]
            # Display-carriers (output, results.content window) may show the
            # first ~5K chars by design; the full 50K body must not ride along.
            assert len(str(stream_payload)) < 10_000
            assert "full_body" not in tool_log[0]["metadata"]
        finally:
            self._teardown(saved)

    def test_no_store_services_leaves_depth_clipped_not_full(self):
        from moira.models.knowledge import Citation
        from moira.tools.base import ToolCall
        from moira.workflow.nodes.research import _process_execution_results

        saved = dict(_services)
        _services.clear()  # no repo/queue at all
        try:
            result, body = _url_content_result()
            call = ToolCall(
                id="c1", name="url_content", arguments={"url": "https://example.com/long"}
            )
            citations = [
                Citation(id="cit001", source="url_content", url="https://example.com/long")
            ]
            seen_urls = {"https://example.com/long": "cit001"}

            _process_execution_results(
                [result],
                [call],
                lambda e: None,
                citations,
                seen_urls,
                [],
                [],
                [],
                {},
                {"url_content": 3.0},
                100.0,
                0.0,
            )
            cit = citations[0]
            # Truthful without the store: only the serving window exists.
            assert cit["depth"] == "clipped"
            assert cit["byte_size"] == len(body)
        finally:
            _services.clear()
            _services.update(saved)


class TestHydrationTruncationAndSnapshot:
    async def test_over_cap_body_stored_clipped(self):
        from moira.models.knowledge import Citation
        from moira.tools.base import ToolCall
        from moira.workflow.nodes.research import _process_execution_results

        fake_repo = _FakeSourceRepo()
        fake_queue = _FakeWriteQueue()
        saved = dict(_services)
        _services.clear()
        _services["source_content_repository"] = fake_repo
        _services["write_queue"] = fake_queue
        _services["config"] = _FakeConfig()
        try:
            # 150K body against the 100K cap: stored truncated, class clipped.
            result, body = _url_content_result(body_len=150_000)
            call = ToolCall(
                id="c1", name="url_content", arguments={"url": "https://example.com/long"}
            )
            citations = [
                Citation(id="cit001", source="url_content", url="https://example.com/long")
            ]
            seen_urls = {"https://example.com/long": "cit001"}

            _process_execution_results(
                [result],
                [call],
                lambda e: None,
                citations,
                seen_urls,
                [],
                [],
                [],
                {},
                {"url_content": 3.0},
                100.0,
                0.0,
                run_id="r",
            )
            await fake_queue.drain()

            cit = citations[0]
            assert cit["depth"] == "clipped"
            assert cit["byte_size"] == 150_000  # truthful about the source
            row = fake_repo.rows["https://example.com/long"]
            assert row["material_class"] == "clipped"
            assert row["truncated"] is True
            assert len(row["content"]) == 100_000
        finally:
            _services.clear()
            _services.update(saved)

    def test_knowledge_summary_serializes_byte_size_not_body(self):
        from moira.models.knowledge import knowledge_summary

        knowledge = {
            "question": "q",
            "facts": [],
            "citations": [
                {
                    "id": "cit001",
                    "source": "url_content",
                    "url": "https://example.com/long",
                    "content": "z" * 5000,
                    "depth": "full",
                    "byte_size": 87_654,
                }
            ],
        }
        summary = knowledge_summary(knowledge)  # type: ignore[arg-type]
        cit = summary["citations"][0]
        assert cit["byte_size"] == 87_654
        assert cit["depth"] == "full"
        # The serving window (≤5K) may be serialized by design for post-hoc
        # analysis; byte_size makes clear it is not the whole story.
        assert len(cit["content"]) <= 5000
