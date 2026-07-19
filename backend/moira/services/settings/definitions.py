from dataclasses import dataclass, field


@dataclass
class SettingDefinition:
    key: str
    type: str  # "string" | "integer" | "float" | "boolean"
    default: str
    label: str
    description: str
    group: str  # "budget", "inference", "research" — for UI grouping
    constraints: dict = field(default_factory=dict)

    def parse(self, raw: str):
        if self.type == "integer":
            return int(raw)
        if self.type == "float":
            return float(raw)
        if self.type == "boolean":
            return raw.lower() in ("true", "1", "yes")
        return raw


BUDGET_STEP_CONSTRAINTS = {"type": "integer", "minimum": 0, "maximum": 25}
RETRY_LIMIT_CONSTRAINTS = {"type": "integer", "minimum": 1, "maximum": 10}

SETTING_DEFINITIONS: dict[str, SettingDefinition] = {
    "budget.default_limit": SettingDefinition(
        key="budget.default_limit",
        type="integer",
        default="150",
        label="Default Budget",
        description="Default budget allocated to each research run.",
        group="budget",
        constraints={"type": "integer", "minimum": 35, "maximum": 300},
    ),
    "budget.cost.decomposition": SettingDefinition(
        key="budget.cost.decomposition",
        type="integer",
        default="2",
        label="Decomposition Cost Weight",
        description="Cost deducted when the decomposition step executes.",
        group="budget",
        constraints=BUDGET_STEP_CONSTRAINTS,
    ),
    "budget.cost.tool_identification": SettingDefinition(
        key="budget.cost.tool_identification",
        type="integer",
        default="1",
        label="Tool Identification Cost Weight",
        description="Cost deducted when the tool identification step executes.",
        group="budget",
        constraints=BUDGET_STEP_CONSTRAINTS,
    ),
    "budget.cost.planning": SettingDefinition(
        key="budget.cost.planning",
        type="integer",
        default="2",
        label="Planning Cost Weight",
        description="Cost deducted when the planning step executes.",
        group="budget",
        constraints=BUDGET_STEP_CONSTRAINTS,
    ),
    "budget.cost.research": SettingDefinition(
        key="budget.cost.research",
        type="integer",
        default="10",
        label="Research Cost Weight",
        description="Cost deducted when the research step executes.",
        group="budget",
        constraints=BUDGET_STEP_CONSTRAINTS,
    ),
    "budget.cost.synthesis": SettingDefinition(
        key="budget.cost.synthesis",
        type="integer",
        default="5",
        label="Synthesis Cost Weight",
        description="Cost deducted when the synthesis step executes.",
        group="budget",
        constraints=BUDGET_STEP_CONSTRAINTS,
    ),
    "budget.cost.research_review": SettingDefinition(
        key="budget.cost.research_review",
        type="integer",
        default="3",
        label="Research Review Cost Weight",
        description="Cost deducted when the research review step executes.",
        group="budget",
        constraints=BUDGET_STEP_CONSTRAINTS,
    ),
    "budget.cost.evaluation": SettingDefinition(
        key="budget.cost.evaluation",
        type="integer",
        default="5",
        label="Evaluation Cost Weight",
        description="Cost deducted when the evaluation step executes.",
        group="budget",
        constraints=BUDGET_STEP_CONSTRAINTS,
    ),
    "budget.cost.report_generation": SettingDefinition(
        key="budget.cost.report_generation",
        type="integer",
        default="3",
        label="Report Generation Cost Weight",
        description="Cost deducted when the report generation step executes.",
        group="budget",
        constraints=BUDGET_STEP_CONSTRAINTS,
    ),
    "tool_discovery.query_rewriting": SettingDefinition(
        key="tool_discovery.query_rewriting",
        type="boolean",
        default="true",
        label="Query Rewriting",
        description=(
            "Use the task model to rewrite the research plan into a "
            "tool-oriented search query before embedding. Improves "
            "semantic matching against tool descriptions."
        ),
        group="tool_discovery",
    ),
    "retry.max_review": SettingDefinition(
        key="retry.max_review",
        type="integer",
        default="3",
        label="Max Research Review Attempts",
        description=(
            "Maximum number of research review cycles per evaluation "
            "cycle. Each cycle goes research → synthesis → research_review."
        ),
        group="retry",
        constraints=RETRY_LIMIT_CONSTRAINTS,
    ),
    "retry.max_evaluation": SettingDefinition(
        key="retry.max_evaluation",
        type="integer",
        default="2",
        label="Max Evaluation Attempts",
        description=(
            "Maximum number of evaluation cycles per research run. "
            "Each retry resets the full pipeline from tool identification."
        ),
        group="retry",
        constraints=RETRY_LIMIT_CONSTRAINTS,
    ),
    "web_search.cache_enabled": SettingDefinition(
        key="web_search.cache_enabled",
        type="boolean",
        default="true",
        label="Enable Search Cache",
        description=(
            "Cache web search results in a local SQLite database to avoid "
            "redundant queries on repeated or similar searches."
        ),
        group="web_search",
    ),
    "web_search.cache_ttl_seconds": SettingDefinition(
        key="web_search.cache_ttl_seconds",
        type="integer",
        default="604800",
        label="Search Cache TTL (seconds)",
        description=(
            "How long cached search results remain valid, in seconds. "
            "Default is 604800 (7 days). Set to 0 to disable caching "
            "without toggling the enabled flag."
        ),
        group="web_search",
        constraints={"type": "integer", "minimum": 0, "maximum": 2592000},
    ),
}
