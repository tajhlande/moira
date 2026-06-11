import pytest

from moira.persistence.sqlite.repos import SqliteToolMetricsRepository
from moira.persistence.sqlite.schema import run_migrations
from moira.tools.base import BaseTool, ToolDefinition, ToolResult
from moira.tools.executor import ToolExecutor


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def metrics_repo(db_path):
    run_migrations(db_path)
    return SqliteToolMetricsRepository(db_path)


class _DummyTool(BaseTool):
    tool_name = "dummy"
    tool_description = "dummy tool"
    tool_argument_schema = {"type": "object", "properties": {"x": {"type": "integer"}}}

    async def execute(self, args):
        return ToolResult(tool_name="dummy", output="ok", success=True, duration_ms=50)


class _FailingTool(BaseTool):
    tool_name = "failer"
    tool_description = "always fails"
    tool_argument_schema = {"type": "object"}

    async def execute(self, args):
        return ToolResult(
            tool_name="failer", output="", success=False, error="boom", duration_ms=10
        )


class _CustomCallTypeTool(BaseTool):
    tool_name = "typed_tool"
    tool_description = "reports custom call type"
    tool_argument_schema = {"type": "object", "properties": {"mode": {"type": "string"}}}

    def report_call_type(self, args, result):
        if result.success:
            return args.get("mode", "default_mode")
        return None

    async def execute(self, args):
        return ToolResult(tool_name="typed_tool", output="ok", success=True, duration_ms=100)


class _BrokenReportTypeTool(BaseTool):
    tool_name = "broken_report"
    tool_description = "report_call_type raises"
    tool_argument_schema = {"type": "object"}

    def report_call_type(self, args, result):
        raise RuntimeError("boom in report_call_type")

    async def execute(self, args):
        return ToolResult(tool_name="broken_report", output="ok", success=True, duration_ms=30)


def _make_definition(cls):
    return ToolDefinition(
        name=cls.tool_name,
        description=cls.tool_description,
        argument_schema=cls.tool_argument_schema,
        implementation=f"{cls.__module__}.{cls.__qualname__}",
        built_in=True,
        is_default=True,
        enabled=True,
    )


class TestSqliteToolMetricsRepository:
    @pytest.mark.asyncio
    async def test_record_call_creates_row(self, metrics_repo, db_path):
        await metrics_repo.record_call(
            tool_name="calculator",
            call_type="default",
            period_hour="2026-06-02T14:00",
            success=True,
            duration_ms=42,
        )
        rows = await metrics_repo.get_metrics()
        assert len(rows) == 1
        r = rows[0]
        assert r.tool_name == "calculator"
        assert r.call_type == "default"
        assert r.period_hour == "2026-06-02T14:00"
        assert r.call_count == 1
        assert r.success_count == 1
        assert r.error_count == 0
        assert r.aggregate_duration_ms == 42
        assert r.low_duration_ms == 42
        assert r.high_duration_ms == 42

    @pytest.mark.asyncio
    async def test_record_call_repeated_aggregates(self, metrics_repo):
        await metrics_repo.record_call("calc", "default", "2026-06-02T14:00", True, 100)
        await metrics_repo.record_call("calc", "default", "2026-06-02T14:00", False, 200)
        rows = await metrics_repo.get_metrics()
        assert len(rows) == 1
        r = rows[0]
        assert r.call_count == 2
        assert r.success_count == 1
        assert r.error_count == 1
        assert r.aggregate_duration_ms == 300
        assert r.low_duration_ms == 100
        assert r.high_duration_ms == 200

    @pytest.mark.asyncio
    async def test_hourly_buckets_isolated(self, metrics_repo):
        await metrics_repo.record_call("calc", "default", "2026-06-02T14:00", True, 50)
        await metrics_repo.record_call("calc", "default", "2026-06-02T15:00", True, 75)
        rows = await metrics_repo.get_metrics()
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_call_types_isolated(self, metrics_repo):
        await metrics_repo.record_call("tool", "search", "2026-06-02T14:00", True, 50)
        await metrics_repo.record_call("tool", "summarize", "2026-06-02T14:00", True, 60)
        rows = await metrics_repo.get_metrics()
        assert len(rows) == 2
        types = {r.call_type for r in rows}
        assert types == {"search", "summarize"}

    @pytest.mark.asyncio
    async def test_get_metrics_filter_tool_name(self, metrics_repo):
        await metrics_repo.record_call("alpha", "default", "2026-06-02T14:00", True, 10)
        await metrics_repo.record_call("beta", "default", "2026-06-02T14:00", True, 20)
        rows = await metrics_repo.get_metrics(tool_name="alpha")
        assert len(rows) == 1
        assert rows[0].tool_name == "alpha"

    @pytest.mark.asyncio
    async def test_get_metrics_filter_period_range(self, metrics_repo):
        await metrics_repo.record_call("t", "default", "2026-06-02T13:00", True, 10)
        await metrics_repo.record_call("t", "default", "2026-06-02T14:00", True, 10)
        await metrics_repo.record_call("t", "default", "2026-06-02T15:00", True, 10)
        rows = await metrics_repo.get_metrics(
            period_start="2026-06-02T14:00", period_end="2026-06-02T14:00"
        )
        assert len(rows) == 1
        assert rows[0].period_hour == "2026-06-02T14:00"

    @pytest.mark.asyncio
    async def test_get_metrics_empty(self, metrics_repo):
        rows = await metrics_repo.get_metrics()
        assert rows == []


class TestExecutorMetricsIntegration:
    @pytest.fixture
    def executor_with_metrics(self, metrics_repo):
        executor = ToolExecutor(metrics_repo=metrics_repo)
        executor.register_tool(_DummyTool(_make_definition(_DummyTool)))
        executor.register_tool(_FailingTool(_make_definition(_FailingTool)))
        executor.register_tool(_CustomCallTypeTool(_make_definition(_CustomCallTypeTool)))
        executor.register_tool(_BrokenReportTypeTool(_make_definition(_BrokenReportTypeTool)))
        return executor

    @pytest.mark.asyncio
    async def test_success_records_metrics(self, executor_with_metrics, metrics_repo):
        result = await executor_with_metrics.execute("dummy", {"x": 1})
        assert result.success
        rows = await metrics_repo.get_metrics()
        assert len(rows) == 1
        assert rows[0].tool_name == "dummy"
        assert rows[0].success_count == 1
        assert rows[0].error_count == 0

    @pytest.mark.asyncio
    async def test_failure_records_metrics(self, executor_with_metrics, metrics_repo):
        result = await executor_with_metrics.execute("failer", {})
        assert not result.success
        rows = await metrics_repo.get_metrics()
        assert len(rows) == 1
        assert rows[0].error_count == 1
        assert rows[0].success_count == 0

    @pytest.mark.asyncio
    async def test_default_call_type_when_no_override(self, executor_with_metrics, metrics_repo):
        await executor_with_metrics.execute("dummy", {"x": 1})
        rows = await metrics_repo.get_metrics()
        assert rows[0].call_type == "default"

    @pytest.mark.asyncio
    async def test_custom_call_type_from_tool(self, executor_with_metrics, metrics_repo):
        await executor_with_metrics.execute("typed_tool", {"mode": "search"})
        rows = await metrics_repo.get_metrics()
        assert rows[0].call_type == "search"

    @pytest.mark.asyncio
    async def test_report_call_type_exception_swallowed(self, executor_with_metrics, metrics_repo):
        result = await executor_with_metrics.execute("broken_report", {})
        assert result.success
        rows = await metrics_repo.get_metrics()
        assert len(rows) == 1
        assert rows[0].call_type == "default"

    @pytest.mark.asyncio
    async def test_not_found_records_metrics(self, executor_with_metrics, metrics_repo):
        result = await executor_with_metrics.execute("nonexistent", {})
        assert not result.success
        rows = await metrics_repo.get_metrics()
        assert len(rows) == 1
        assert rows[0].tool_name == "nonexistent"
        assert rows[0].error_count == 1

    @pytest.mark.asyncio
    async def test_blocked_tool_records_metrics(self, executor_with_metrics, metrics_repo):
        result = await executor_with_metrics.execute("dummy", {"x": 1}, allowed_tools={"other"})
        assert not result.success
        rows = await metrics_repo.get_metrics()
        assert len(rows) == 1
        assert rows[0].error_count == 1

    @pytest.mark.asyncio
    async def test_repeated_calls_aggregate(self, executor_with_metrics, metrics_repo):
        await executor_with_metrics.execute("dummy", {"x": 1})
        await executor_with_metrics.execute("dummy", {"x": 2})
        rows = await metrics_repo.get_metrics()
        assert len(rows) == 1
        assert rows[0].call_count == 2
        assert rows[0].success_count == 2

    @pytest.mark.asyncio
    async def test_no_metrics_repo_silent_skip(self):
        executor = ToolExecutor(metrics_repo=None)
        executor.register_tool(_DummyTool(_make_definition(_DummyTool)))
        result = await executor.execute("dummy", {"x": 1})
        assert result.success

    @pytest.mark.asyncio
    async def test_timeout_records_metrics(self, metrics_repo):
        class _SlowTool(BaseTool):
            tool_name = "slow"
            tool_description = "slow tool"
            tool_argument_schema = {"type": "object"}

            async def execute(self, args):
                import asyncio

                await asyncio.sleep(10)
                return ToolResult(tool_name="slow", output="ok", success=True)

        executor = ToolExecutor(timeout=0.1, metrics_repo=metrics_repo)
        executor.register_tool(_SlowTool(_make_definition(_SlowTool)))
        result = await executor.execute("slow", {})
        assert not result.success
        assert "Timeout" in result.error
        rows = await metrics_repo.get_metrics()
        assert len(rows) == 1
        assert rows[0].tool_name == "slow"
        assert rows[0].error_count == 1

    @pytest.mark.asyncio
    async def test_exception_records_metrics(self, metrics_repo):
        class _CrashTool(BaseTool):
            tool_name = "crash"
            tool_description = "crashes"
            tool_argument_schema = {"type": "object"}

            async def execute(self, args):
                raise ValueError("unexpected crash")

        executor = ToolExecutor(metrics_repo=metrics_repo)
        executor.register_tool(_CrashTool(_make_definition(_CrashTool)))
        result = await executor.execute("crash", {})
        assert not result.success
        rows = await metrics_repo.get_metrics()
        assert len(rows) == 1
        assert rows[0].error_count == 1


class TestExecutorWithExistingBuiltinTools:
    """Verify metrics are captured for the real built-in tools."""

    @pytest.mark.asyncio
    async def test_calculator_records_metrics(self, metrics_repo):
        from moira.tools.builtin.calculator import CalculatorTool

        executor = ToolExecutor(metrics_repo=metrics_repo)
        executor.register_tool(CalculatorTool(CalculatorTool.make_definition()))
        result = await executor.execute("calculator", {"expression": "2 + 2"})
        assert result.success
        assert result.output == "4"
        rows = await metrics_repo.get_metrics()
        assert len(rows) == 1
        assert rows[0].tool_name == "calculator"
        assert rows[0].call_type == "default"
        assert rows[0].success_count == 1
        assert rows[0].call_count == 1

    @pytest.mark.asyncio
    async def test_calculator_error_records_metrics(self, metrics_repo):
        from moira.tools.builtin.calculator import CalculatorTool

        executor = ToolExecutor(metrics_repo=metrics_repo)
        executor.register_tool(CalculatorTool(CalculatorTool.make_definition()))
        result = await executor.execute("calculator", {"expression": ""})
        assert not result.success
        rows = await metrics_repo.get_metrics()
        assert len(rows) == 1
        assert rows[0].error_count == 1
