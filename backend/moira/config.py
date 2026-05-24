import logging
import os
from pathlib import Path

import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class DatabaseConfig(BaseModel):
    sqlite_path: str = "./data/moira.db"


class CostWeights(BaseModel):
    planning: int = 2
    tool_discovery: int = 1
    tool_selection: int = 2
    research_execution: int = 5
    compression: int = 1
    draft_synthesis: int = 3
    verification: int = 4
    report_generation: int = 3


class BudgetConfig(BaseModel):
    default_limit: int = 50
    cost_weights: CostWeights = CostWeights()


class InferenceEndpointConfig(BaseModel):
    name: str
    base_url: str
    api_key: str = ""


class InferenceModelsConfig(BaseModel):
    intelligence_endpoint: str = ""
    intelligence_model: str = ""
    task_endpoint: str = ""
    task_model: str = ""


class InferenceConfig(BaseModel):
    endpoints: list[InferenceEndpointConfig] = []
    models: InferenceModelsConfig = InferenceModelsConfig()


class EmbeddingConfig(BaseModel):
    provider: str = "local"
    model: str = "all-MiniLM-L6-v2"
    endpoint: str = ""
    api_key: str = ""


class ToolConfig(BaseModel):
    name: str
    description: str
    type: str
    endpoint: str = ""
    method: str = "GET"
    argument_schema: dict = {}
    tags: list[str] = []
    reliability: str = "unknown"


class MCPServerConfig(BaseModel):
    name: str
    address: str


class AppConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173"]


class MoiraConfig(BaseModel):
    database: DatabaseConfig = DatabaseConfig()
    budget: BudgetConfig = BudgetConfig()
    inference: InferenceConfig = InferenceConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    tools: list[ToolConfig] = []
    mcp_servers: list[MCPServerConfig] = []
    app: AppConfig = AppConfig()

    @property
    def full_cycle_cost(self) -> int:
        # Cost of one full retry cycle (Planning through Verification).
        # report_generation is deliberately excluded because it is
        # budget-exempt -- it always executes as the terminal node.
        cw = self.budget.cost_weights
        return (
            cw.planning
            + cw.tool_discovery
            + cw.tool_selection
            + cw.research_execution
            + cw.compression
            + cw.draft_synthesis
            + cw.verification
        )


def load_config() -> MoiraConfig:
    # Uses SystemExit rather than a custom exception because a missing or
    # invalid config is a fatal startup error with no recovery path.
    logger.info("Loading config")
    path = os.environ.get("MOIRA_CONFIG_FILE")
    if not path:
        raise SystemExit(
            "MOIRA_CONFIG_FILE environment variable is not set.\n"
            "Copy config/moira-config-template.yaml to config/moira-config.yaml, "
            "edit it, and set MOIRA_CONFIG_FILE to point to it."
        )
    p = Path(path)
    if not p.exists():
        raise SystemExit(
            f"Config file not found: {path}\n"
            "Copy config/moira-config-template.yaml to config/moira-config.yaml, "
            "edit it, and re-run."
        )
    with open(p) as f:
        raw = yaml.safe_load(f)
    return MoiraConfig.model_validate(raw)


def resolve_db_path(config: MoiraConfig) -> str:
    # MOIRA_DATA_DIR takes precedence over config file's sqlite_path.
    # Allows overriding the database location at deploy time without
    # modifying the config file (e.g., container volume mounts, test
    # isolation). When set, the filename is always "moira.db".
    logger.debug("Resolving database path")
    data_dir = os.environ.get("MOIRA_DATA_DIR")
    if data_dir:
        logger.info("Using MOIRA_DATA_DIR=%s", data_dir)
        return str(Path(data_dir) / "moira.db")
    logger.info("Using config sqlite_path=%s", config.database.sqlite_path)
    return config.database.sqlite_path
