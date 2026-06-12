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
        default="60",
        label="Default Budget",
        description="Default budget allocated to each research run.",
        group="budget",
        constraints={"type": "integer", "minimum": 35, "maximum": 150},
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
    "budget.cost.verification": SettingDefinition(
        key="budget.cost.verification",
        type="integer",
        default="8",
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
