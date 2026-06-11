from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable
from typing import Any, Callable

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 0.05
_QUEUE_DEPTH_WARNING = 50


class AsyncWriteQueue:
    """Singleton async queue that serializes all SQLite write operations.

    Ensures FIFO ordering of writes, eliminates SQLITE_BUSY contention,
    and provides per-write retry with backoff on lock errors.

    Usage::

        # Fire-and-forget (metrics, audit writes):
        write_queue.enqueue(repo.save_step, step)

        # Await for critical writes:
        await write_queue.enqueue(repo.save_message, msg)
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[
            tuple[Callable[[], Awaitable[Any]], asyncio.Future[Any]] | None
        ] = asyncio.Queue()
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the consumer task. Must be called within a running event loop."""
        self._running = True
        self._task = asyncio.create_task(self._consumer())
        logger.info("AsyncWriteQueue started")

    async def stop(self, timeout: float = 5.0) -> None:
        """Signal the consumer to drain remaining writes and exit.

        Waits up to *timeout* seconds for the queue to empty and the
        consumer task to finish. Force-cancels the task if the deadline
        is exceeded."""
        if self._task is None:
            return
        self._running = False
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=timeout)
            logger.info("AsyncWriteQueue drained and stopped")
        except asyncio.TimeoutError:
            logger.warning(
                "AsyncWriteQueue did not drain within %.1fs, cancelling",
                timeout,
            )
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def enqueue(self, fn: Callable[[], Awaitable[Any]]) -> asyncio.Future[Any]:
        """Enqueue a write as a zero-arg async callable for sequential execution.

        Accepts a callable (typically a lambda or partial) that returns an
        awaitable. Using a callable instead of a raw coroutine allows the
        consumer to re-invoke on retry without hitting "cannot reuse already
        awaited coroutine" errors.

        Returns a future that resolves to the result (or stores its
        exception). Callers that don't need to wait can simply ignore
        the return value."""
        if not self._running:
            logger.warning("AsyncWriteQueue.enqueue() called while not running")
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._queue.put_nowait((fn, future))
        depth = self._queue.qsize()
        if depth > _QUEUE_DEPTH_WARNING:
            logger.warning("AsyncWriteQueue depth %d exceeds %d", depth, _QUEUE_DEPTH_WARNING)
        return future

    async def _consumer(self) -> None:
        """Drain write callables from the queue, executing them sequentially
        with per-write retry on SQLite lock errors."""
        while self._running or not self._queue.empty():
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            if item is None:
                continue

            fn, future = item
            last_exc: Exception | None = None

            for attempt in range(_MAX_RETRIES):
                try:
                    result = await fn()
                    if not future.done():
                        future.set_result(result)
                    break
                except sqlite3.OperationalError as exc:
                    last_exc = exc
                    msg = str(exc).lower()
                    if ("locked" in msg or "busy" in msg) and attempt < _MAX_RETRIES - 1:
                        delay = _RETRY_BASE_DELAY * (2**attempt)
                        logger.debug(
                            "Write queue retry %d/%d after %.3fs (SQLite lock)",
                            attempt + 1,
                            _MAX_RETRIES,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    if not future.done():
                        future.set_exception(exc)
                    logger.debug("Write failed after %d attempts: %s", attempt + 1, exc)
                    break
                except Exception as exc:
                    if not future.done():
                        future.set_exception(exc)
                    logger.debug("Write failed: %s", exc, exc_info=True)
                    break
            else:
                if last_exc is not None and not future.done():
                    future.set_exception(last_exc)
