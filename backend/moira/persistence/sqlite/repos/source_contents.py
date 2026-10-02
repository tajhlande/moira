"""Source-content store: persistence for full fetched page bodies.

Backs the retrieval-quality plan's source-content foundation (Phase 2b).
``Citation.content`` remains a 5K-char serving window into the model context;
this store keeps the much larger pre-cap body plus an explicit material
class (``clipped`` / ``full`` / ``summary``) so that "what did the agent
actually have" is a queryable fact instead of something inferred from
character counts.
"""

import hashlib
import logging

from moira.persistence.interfaces import SourceContent, SourceContentRepository
from moira.persistence.sqlite.repos._connection import connect

logger = logging.getLogger(__name__)


def url_hash(url: str) -> str:
    """Stable content key for a URL (sha256 hex).

    Deterministic and collision-safe; combined with ``run_id`` it forms
    the store's composite primary key, so re-fetches of the same URL
    within a run upsert one row while different runs keep their own.
    """
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


class SqliteSourceContentRepository(SourceContentRepository):
    """SQLite-backed source-content store.

    Follows the repository conventions elsewhere in this package: a fresh
    connection per call, explicit commit, close in ``finally``. Upserts key
    on the composite ``(run_id, url_hash)`` so re-fetches within a run
    replace (not duplicate) that run's stored body — newest fetch within
    the run wins — while a different run fetching the same URL stores its
    own row. Storage scope therefore matches ``get``/``evict_run`` scope.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path

    def _connect(self):
        return connect(self._db_path)

    async def upsert(
        self,
        run_id: str,
        url: str,
        content: str,
        material_class: str,
        citation_id: str | None = None,
        content_type: str = "text/markdown",
        truncated: bool = False,
        fetched_at: str = "",
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO source_contents "
                "(url_hash, url, run_id, citation_id, material_class, "
                "content, content_type, char_count, truncated, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, url_hash) DO UPDATE SET "
                "url = excluded.url, "
                "citation_id = excluded.citation_id, "
                "material_class = excluded.material_class, "
                "content = excluded.content, "
                "content_type = excluded.content_type, "
                "char_count = excluded.char_count, "
                "truncated = excluded.truncated, "
                "fetched_at = excluded.fetched_at",
                (
                    url_hash(url),
                    url,
                    run_id,
                    citation_id,
                    material_class,
                    content,
                    content_type,
                    len(content),
                    1 if truncated else 0,
                    fetched_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    async def get(self, run_id: str, url: str) -> SourceContent | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT url_hash, url, run_id, citation_id, material_class, "
                "content, content_type, char_count, truncated, fetched_at "
                "FROM source_contents WHERE url_hash = ? AND run_id = ?",
                (url_hash(url), run_id),
            ).fetchone()
            if row is None:
                # The store is per-run scoped; a miss inside the run is the
                # expected signal for fetch-on-miss hydration later.
                return None
            return SourceContent(
                url_hash=row["url_hash"],
                url=row["url"],
                run_id=row["run_id"],
                citation_id=row["citation_id"],
                material_class=row["material_class"],
                content=row["content"],
                content_type=row["content_type"],
                char_count=row["char_count"],
                truncated=bool(row["truncated"]),
                fetched_at=row["fetched_at"],
            )
        finally:
            conn.close()

    async def evict_run(self, run_id: str) -> int:
        conn = self._connect()
        try:
            cur = conn.execute("DELETE FROM source_contents WHERE run_id = ?", (run_id,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    async def delete_older_than(self, cutoff_iso: str) -> int:
        """Retention primitive (amended plan Step 2): delete rows fetched
        strictly before ``cutoff_iso``. ``fetched_at`` holds ISO strings, so
        lexicographic SQL comparison is correct; rows with an empty
        ``fetched_at`` (pre-field or manually written) count as older than
        any cutoff — unknown age is treated as oldest so the age sweep
        still collects them."""
        conn = self._connect()
        try:
            cur = conn.execute(
                "DELETE FROM source_contents WHERE fetched_at = '' OR fetched_at < ?",
                (cutoff_iso,),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    async def total_content_chars(self) -> int:
        """Retention primitive: total stored chars across all runs."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(char_count), 0) FROM source_contents"
            ).fetchone()
            return int(row[0])
        finally:
            conn.close()

    async def evict_to_size(self, max_total_chars: int, protect_run_id: str | None = None) -> int:
        """Retention primitive: delete oldest-fetched rows until the table
        is at or under ``max_total_chars``.

        The victim prefix is computed from (rowid, char_count) pairs only —
        bodies are never loaded into Python. ``protect_run_id`` (the run
        whose write triggered the sweep) is excluded entirely: a run must
        not evict its own forensics. Returns the number of rows deleted;
        0 when already under the cap.
        """
        conn = self._connect()
        try:
            # Empty string can't collide with real run ids (UUIDs), so
            # "no protection" is just a predicate no row matches.
            protect = protect_run_id or ""
            row = conn.execute(
                "SELECT COALESCE(SUM(char_count), 0) FROM source_contents WHERE run_id != ?",
                (protect,),
            ).fetchone()
            excess = int(row[0]) - max_total_chars
            if excess <= 0:
                return 0
            victims: list[int] = []
            freed = 0
            # Oldest first; rowid tiebreak keeps the order deterministic
            # when timestamps collide (same-run upserts share fetched_at
            # only down to microseconds).
            for candidate in conn.execute(
                "SELECT rowid, char_count FROM source_contents WHERE run_id != ? "
                "ORDER BY fetched_at ASC, rowid ASC",
                (protect,),
            ):
                if freed >= excess:
                    break
                victims.append(candidate["rowid"])
                freed += candidate["char_count"]
            if not victims:
                return 0
            conn.executemany(
                "DELETE FROM source_contents WHERE rowid = ?", [(v,) for v in victims]
            )
            conn.commit()
            return len(victims)
        finally:
            conn.close()
