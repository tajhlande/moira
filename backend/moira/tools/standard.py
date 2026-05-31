"""Standard tool definitions seeded into the database at startup.

Tools with implementations use make_definition() from the implementation
class, which is the authoritative source for name, description, argument
schema, etc. Tools without implementations yet use hand-crafted
ToolDefinitions with an empty implementation string — they appear in the
catalog but cannot be executed."""

from moira.tools.base import ToolDefinition
from moira.tools.builtin.calculator import CalculatorTool
from moira.tools.builtin.url_content import UrlContentTool

DEFAULT_GROUP = {"name": "standard", "display_name": "Standard"}

STANDARD_TOOLS: list[ToolDefinition] = [
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
    ToolDefinition(
        name="web_search",
        description=(
            "Search the web for information about specific topics. "
            "Returns a sorted list of URLs and relevance scores."
        ),
        implementation="",
        group_name="standard",
        is_default=True,
        enabled=True,
        built_in=True,
        argument_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of web domains to restrict search to",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of search results to return",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    ),
    UrlContentTool.make_definition(),
    CalculatorTool.make_definition(),
]
