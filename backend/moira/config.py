import logging
import os
from pathlib import Path

import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class DatabaseConfig(BaseModel):
    sqlite_path: str = "./data/moira.db"
    lancedb_path: str = "./data/vectors"


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
    providers: list[InferenceEndpointConfig] = []
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


def resolve_data_dir(config: MoiraConfig) -> str:
    """Return the base directory for all persistent data.

    MOIRA_DATA_DIR env var takes precedence over config file paths.
    This ensures SQLite, LanceDB, and any future stores all land in
    the same location regardless of how the app is started.

    Falls back to the parent directory of the config's sqlite_path,
    which keeps the default working when running outside run.sh.
    """
    logger.debug("Resolving data directory")
    data_dir = os.environ.get("MOIRA_DATA_DIR")
    if data_dir:
        logger.info("Using MOIRA_DATA_DIR=%s", data_dir)
        return data_dir
    # Derive from the config's sqlite_path so that running without
    # MOIRA_DATA_DIR (e.g. direct uvicorn from backend/) still works.
    # sqlite_path "./data/moira.db" -> data dir "./data"
    config_dir = str(Path(config.database.sqlite_path).parent)
    logger.info("Using derived data_dir=%s from sqlite_path", config_dir)
    return config_dir


def resolve_db_path(config: MoiraConfig) -> str:
    """Convenience: returns the SQLite database file path."""
    return str(Path(resolve_data_dir(config)) / "moira.db")


def resolve_lancedb_path(config: MoiraConfig) -> str:
    """Convenience: returns the LanceDB storage directory path."""
    return str(Path(resolve_data_dir(config)) / "vectors")
