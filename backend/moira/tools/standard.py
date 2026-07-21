"""Standard tool definitions seeded into the database at startup.

Tools with implementations use make_definition() from the implementation
class, which is the authoritative source for name, description, argument
schema, etc. Tools without implementations yet use hand-crafted
ToolDefinitions with an empty implementation string — they appear in the
catalog but cannot be executed."""

from moira.tools.base import ToolDefinition
from moira.tools.builtin.calculator import CalculatorTool
from moira.tools.builtin.date_time import DateTimeTool
from moira.tools.builtin.recall_source import RecallSourceTool
from moira.tools.builtin.url_content import UrlContentTool
from moira.tools.builtin.web_search import WebSearchTool

DEFAULT_GROUP = {"name": "standard", "display_name": "Standard"}

STANDARD_TOOLS: list[ToolDefinition] = [
    DateTimeTool.make_definition(invocation_cost=0.1),
    ToolDefinition(
        name="user_question",
        description=(
            "Ask the user a follow-up question to clarify the research "
            "question or guide the answer. Presents multiple-choice options "
            "plus a free-text response."
        ),
        implementation="moira.tools.builtin.user_question.UserQuestionTool",
        group_name="standard",
        is_default=True,
        enabled=True,
        built_in=True,
        invocation_cost=0.1,
        argument_schema={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "A/B/C/D multiple choice options",
                },
            },
            "required": ["question", "options"],
        },
    ),
    WebSearchTool.make_definition(
        invocation_cost=5.0, call_limit_per_run=10, call_limit_per_step=5
    ),
    UrlContentTool.make_definition(
        invocation_cost=3.0, call_limit_per_run=15, call_limit_per_step=8
    ),
    # recall_source is free — it reads existing citation content from
    # workflow state. Call limits prevent context bloat. The research loop
    # intercepts these calls and synthesizes results from in-scope citations.
    RecallSourceTool.make_definition(
        invocation_cost=0.0, call_limit_per_run=10, call_limit_per_step=5
    ),
    CalculatorTool.make_definition(invocation_cost=0.1),
]
