import asyncio
import sqlite3

import pytest

from moira.persistence.write_queue import AsyncWriteQueue


@pytest.fixture
async def queue():
    q = AsyncWriteQueue()
    await q.start()
    yield q
    await q.stop()


async def test_write_succeeds(queue):
    async def write():
        return 42

    future = queue.enqueue(write)
    result = await future
    assert result == 42


async def test_fire_and_forget(queue):
    """Ignoring the future still executes the write."""
    results = []

    async def write():
        results.append(1)

    queue.enqueue(write)
    await asyncio.sleep(0.1)
    assert results == [1]


async def test_await_gives_result(queue):
    async def write():
        return "hello"

    future = queue.enqueue(write)
    assert await future == "hello"


async def test_write_failure_isolated(queue):
    """A failing write stores exception on future; consumer keeps running."""
    async def bad_write():
        raise ValueError("boom")

    async def good_write():
        return "ok"

    bad_future = queue.enqueue(bad_write)
    good_future = queue.enqueue(good_write)

    with pytest.raises(ValueError, match="boom"):
        await bad_future

    assert await good_future == "ok"


async def test_sqlite_busy_retries(queue, tmp_path):
    """OperationalError with 'busy' triggers retry."""
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t (v INTEGER)")
    conn.commit()
    conn.close()

    attempts = 0

    async def write():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise sqlite3.OperationalError("database is locked")
        c = sqlite3.connect(db_path)
        c.execute("INSERT INTO t (v) VALUES (1)")
        c.commit()
        c.close()
        return "done"

    future = queue.enqueue(write)
    assert await future == "done"
    assert attempts == 3


async def test_sqlite_busy_exhausts_retries(queue):
    """OperationalError that keeps failing exhausts retries and raises."""
    attempts = 0

    async def write():
        nonlocal attempts
        attempts += 1
        raise sqlite3.OperationalError("database is locked")

    future = queue.enqueue(write)
    with pytest.raises(sqlite3.OperationalError):
        await future
    assert attempts == 3


async def test_non_lock_error_no_retry(queue):
    """OperationalError without lock/busy does not retry."""
    attempts = 0

    async def write():
        nonlocal attempts
        attempts += 1
        raise sqlite3.OperationalError("no such table: foo")

    future = queue.enqueue(write)
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        await future
    assert attempts == 1


async def test_stop_drains_pending():
    """stop() waits for pending writes to complete."""
    q = AsyncWriteQueue()
    await q.start()
    results = []

    async def write():
        await asyncio.sleep(0.05)
        results.append(1)
        return "done"

    future = q.enqueue(write)
    await q.stop()
    assert await future == "done"
    assert results == [1]


async def test_ordering_preserved(queue):
    """Writes execute in FIFO order."""
    results = []

    async def write(val):
        results.append(val)

    for i in range(10):
        val = i
        queue.enqueue(lambda v=val: write(v))

    await asyncio.sleep(0.2)
    assert results == list(range(10))


async def test_start_and_stop_lifecycle():
    q = AsyncWriteQueue()
    await q.start()
    await q.stop()
    await q.stop()
