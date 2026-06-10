"""Date and time tool: provides the current date and time.

Returns the current date and time in the user's timezone if available
(via the TZ environment variable), otherwise in the server's local timezone.
The response indicates which source was used."""

import time
from datetime import datetime, timezone
from typing import Any

from moira.tools.base import BaseTool, ToolResult


class DateTimeTool(BaseTool):
    """Provides the current date and time to the LLM. The agent cannot
    access the clock on its own, so this tool fills that gap. Returns
    the time in the user's timezone if TZ is set, otherwise the server's
    local timezone, and indicates which source was used."""

    tool_name = "date_time"
    tool_description = (
        "Get the current date and time. "
        "Returns the date, time, day of week, and timezone. "
        "Use this tool whenever you need to know the current date or time."
    )
    tool_group = "standard"
    tool_argument_schema = {
        "type": "object",
        "properties": {},
    }

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        import os

        start = time.monotonic()

        tz_env = os.environ.get("TZ", "").strip()
        if tz_env:
            import zoneinfo

            try:
                user_tz = zoneinfo.ZoneInfo(tz_env)
                now = datetime.now(user_tz)
                source = f"user timezone ({tz_env})"
            except Exception:
                now = datetime.now()
                source = f"server local time (TZ={tz_env} was invalid)"
        else:
            now = datetime.now()
            utc_offset_sec = time.timezone if time.daylight == 0 else time.altzone
            offset_hours = -utc_offset_sec // 3600
            offset_sign = "+" if offset_hours >= 0 else "-"
            tz_name = time.tzname[time.daylight] or "local"
            source = (
                f"server local time ({tz_name}, UTC{offset_sign}{abs(offset_hours):02d}00)"
            )

        elapsed = int((time.monotonic() - start) * 1000)

        formatted = now.strftime("%Y-%m-%d %H:%M:%S %Z").strip()
        day_of_week = now.strftime("%A")

        output = (
            f"Current date and time: {formatted}\n"
            f"Day of week: {day_of_week}\n"
            f"Timezone source: {source}"
        )

        return ToolResult(
            tool_name="date_time",
            output=output,
            success=True,
            duration_ms=elapsed,
        )
