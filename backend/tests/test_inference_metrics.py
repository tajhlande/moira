import pytest

from moira.persistence.sqlite.repos import SqliteInferenceMetricsRepository
from moira.persistence.sqlite.schema import run_migrations


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def metrics_repo(db_path):
    run_migrations(db_path)
    return SqliteInferenceMetricsRepository(db_path)


class TestRecordStep:
    async def test_creates_row(self, metrics_repo, db_path):
        await metrics_repo.record_step(
            model="llama3",
            purpose="intelligence",
            period_hour="2026-06-03T14:00",
            call_count=1,
            input_tokens=100,
            output_tokens=50,
            thinking_tokens=10,
            prompt_time_ms=200.0,
            gen_time_ms=300.0,
        )

        rows = await metrics_repo.get_metrics()
        assert len(rows) == 1
        r = rows[0]
        assert r.model == "llama3"
        assert r.purpose == "intelligence"
        assert r.period_hour == "2026-06-03T14:00"
        assert r.call_count == 1
        assert r.input_tokens == 100
        assert r.output_tokens == 50
        assert r.thinking_tokens == 10
        assert r.prompt_time_ms == 200.0
        assert r.gen_time_ms == 300.0

    async def test_upserts_on_conflict(self, metrics_repo):
        await metrics_repo.record_step(
            model="llama3",
            purpose="intelligence",
            period_hour="2026-06-03T14:00",
            call_count=1,
            input_tokens=100,
            output_tokens=50,
            thinking_tokens=10,
            prompt_time_ms=200.0,
            gen_time_ms=300.0,
        )
        await metrics_repo.record_step(
            model="llama3",
            purpose="intelligence",
            period_hour="2026-06-03T14:00",
            call_count=2,
            input_tokens=200,
            output_tokens=80,
            thinking_tokens=20,
            prompt_time_ms=400.0,
            gen_time_ms=600.0,
        )

        rows = await metrics_repo.get_metrics()
        assert len(rows) == 1
        r = rows[0]
        assert r.call_count == 3
        assert r.input_tokens == 300
        assert r.output_tokens == 130
        assert r.thinking_tokens == 30
        assert r.prompt_time_ms == 600.0
        assert r.gen_time_ms == 900.0

    async def test_separates_by_model(self, metrics_repo):
        await metrics_repo.record_step(
            model="llama3",
            purpose="intelligence",
            period_hour="2026-06-03T14:00",
            call_count=1,
            input_tokens=100,
            output_tokens=50,
            thinking_tokens=0,
            prompt_time_ms=0.0,
            gen_time_ms=0.0,
        )
        await metrics_repo.record_step(
            model="qwen2",
            purpose="intelligence",
            period_hour="2026-06-03T14:00",
            call_count=2,
            input_tokens=200,
            output_tokens=100,
            thinking_tokens=0,
            prompt_time_ms=0.0,
            gen_time_ms=0.0,
        )

        rows = await metrics_repo.get_metrics()
        assert len(rows) == 2

    async def test_separates_by_purpose(self, metrics_repo):
        await metrics_repo.record_step(
            model="llama3",
            purpose="intelligence",
            period_hour="2026-06-03T14:00",
            call_count=1,
            input_tokens=100,
            output_tokens=50,
            thinking_tokens=0,
            prompt_time_ms=0.0,
            gen_time_ms=0.0,
        )
        await metrics_repo.record_step(
            model="llama3",
            purpose="task",
            period_hour="2026-06-03T14:00",
            call_count=1,
            input_tokens=50,
            output_tokens=25,
            thinking_tokens=0,
            prompt_time_ms=0.0,
            gen_time_ms=0.0,
        )

        rows = await metrics_repo.get_metrics()
        assert len(rows) == 2


class TestGetMetrics:
    async def test_filter_by_period(self, metrics_repo):
        for hour in ("2026-06-03T13:00", "2026-06-03T14:00", "2026-06-03T15:00"):
            await metrics_repo.record_step(
                model="llama3",
                purpose="intelligence",
                period_hour=hour,
                call_count=1,
                input_tokens=100,
                output_tokens=50,
                thinking_tokens=0,
                prompt_time_ms=0.0,
                gen_time_ms=0.0,
            )

        rows = await metrics_repo.get_metrics(
            period_start="2026-06-03T14:00",
            period_end="2026-06-03T14:59",
        )
        assert len(rows) == 1
        assert rows[0].period_hour == "2026-06-03T14:00"

    async def test_filter_by_model(self, metrics_repo):
        await metrics_repo.record_step(
            model="llama3",
            purpose="intelligence",
            period_hour="2026-06-03T14:00",
            call_count=1,
            input_tokens=100,
            output_tokens=50,
            thinking_tokens=0,
            prompt_time_ms=0.0,
            gen_time_ms=0.0,
        )
        await metrics_repo.record_step(
            model="qwen2",
            purpose="intelligence",
            period_hour="2026-06-03T14:00",
            call_count=1,
            input_tokens=100,
            output_tokens=50,
            thinking_tokens=0,
            prompt_time_ms=0.0,
            gen_time_ms=0.0,
        )

        rows = await metrics_repo.get_metrics(model="llama3")
        assert len(rows) == 1
        assert rows[0].model == "llama3"

    async def test_filter_by_purpose(self, metrics_repo):
        await metrics_repo.record_step(
            model="llama3",
            purpose="intelligence",
            period_hour="2026-06-03T14:00",
            call_count=1,
            input_tokens=100,
            output_tokens=50,
            thinking_tokens=0,
            prompt_time_ms=0.0,
            gen_time_ms=0.0,
        )
        await metrics_repo.record_step(
            model="llama3",
            purpose="task",
            period_hour="2026-06-03T14:00",
            call_count=1,
            input_tokens=100,
            output_tokens=50,
            thinking_tokens=0,
            prompt_time_ms=0.0,
            gen_time_ms=0.0,
        )

        rows = await metrics_repo.get_metrics(purpose="task")
        assert len(rows) == 1
        assert rows[0].purpose == "task"

    async def test_returns_empty_when_no_match(self, metrics_repo):
        rows = await metrics_repo.get_metrics(model="nonexistent")
        assert rows == []
