"""recall_source tool: re-read previously-fetched source content by citation ID.

This tool is never directly executed by the ToolExecutor. The research loop
intercepts ``recall_source`` calls and synthesizes results from the in-scope
``citations`` list — the content is already stored in ``Citation.content``
(up to 5000 chars) and ``Citation.snippets`` from prior ``url_content`` and
``web_search`` calls.

The stub ``execute()`` exists only for registration and direct unit testing.
"""

from typing import Any

from moira.tools.base import BaseTool, ToolResult


class RecallSourceTool(BaseTool):
    """Lets the research model re-read full stored content of a previously-
    fetched source by citation ID, without re-fetching the URL.

    In normal operation, the research loop intercepts ``recall_source``
    calls before they reach the executor and synthesizes a ``ToolResult``
    from in-scope citations. This avoids re-fetching URLs that may be
    deduped, paywalled, or JS-rendered (returning "Loading...") when the
    content is already available in workflow state.
    """

    tool_name = "recall_source"
    tool_description = (
        "Recall the full stored content of a previously-fetched source by "
        "citation ID. Use this to re-examine evidence from sources already "
        "consulted in prior research rounds, without re-fetching the URL. "
        "Check the 'Sources already consulted' section in your context for "
        "available citation IDs."
    )
    tool_group = "standard"
    tool_argument_schema = {
        "type": "object",
        "properties": {
            "citation_id": {
                "type": "string",
                "description": "The citation ID to recall (e.g., 'cit004')",
            },
        },
        "required": ["citation_id"],
    }

    async def execute(self, args: dict[str, Any]) -> ToolResult:
        """Stub — the research loop intercepts recall_source calls.

        This method exists for registration and direct unit testing. In
        normal operation, :func:`_build_recall_source_result` in the
        research node synthesizes the result from in-scope citations
        before the executor is ever called.
        """
        return ToolResult(
            tool_name="recall_source",
            output="recall_source must be called within a research workflow.",
            success=False,
        )
