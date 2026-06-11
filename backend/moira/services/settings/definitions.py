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

SETTING_DEFINITIONS: dict[str, SettingDefinition] = {
    "budget.default_limit": SettingDefinition(
        key="budget.default_limit",
        type="integer",
        default="50",
        label="Default Budget",
        description="Default budget allocated to each research run.",
        group="budget",
        constraints={"type": "integer", "minimum": 35, "maximum": 150},
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
    "budget.cost.tool_discovery": SettingDefinition(
        key="budget.cost.tool_discovery",
        type="integer",
        default="1",
        label="Tool Discovery Cost Weight",
        description="Cost deducted when the tool discovery step executes.",
        group="budget",
        constraints=BUDGET_STEP_CONSTRAINTS,
    ),
    "budget.cost.tool_selection": SettingDefinition(
        key="budget.cost.tool_selection",
        type="integer",
        default="2",
        label="Tool Selection Cost Weight",
        description="Cost deducted when the tool selection step executes.",
        group="budget",
        constraints=BUDGET_STEP_CONSTRAINTS,
    ),
    "budget.cost.research_execution": SettingDefinition(
        key="budget.cost.research_execution",
        type="integer",
        default="5",
        label="Research Execution Cost Weight",
        description="Cost deducted when the research execution step executes.",
        group="budget",
        constraints=BUDGET_STEP_CONSTRAINTS,
    ),
    "budget.cost.compression": SettingDefinition(
        key="budget.cost.compression",
        type="integer",
        default="1",
        label="Compression Cost Weight",
        description="Cost deducted when the compression step executes.",
        group="budget",
        constraints=BUDGET_STEP_CONSTRAINTS,
    ),
    "budget.cost.draft_synthesis": SettingDefinition(
        key="budget.cost.draft_synthesis",
        type="integer",
        default="3",
        label="Draft Synthesis Cost Weight",
        description="Cost deducted when the draft synthesis step executes.",
        group="budget",
        constraints=BUDGET_STEP_CONSTRAINTS,
    ),
    "budget.cost.verification": SettingDefinition(
        key="budget.cost.verification",
        type="integer",
        default="4",
        label="Verification Cost Weight",
        description="Cost deducted when the verification step executes.",
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
}
