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
    decomposition: int = 2
    tool_identification: int = 1
    planning: int = 2
    research: int = 10
    synthesis: int = 5
    research_review: int = 3
    evaluation: int = 5
    report_generation: int = 3


class RetryLimits(BaseModel):
    max_review: int = 3
    max_evaluation: int = 2


class BudgetConfig(BaseModel):
    default_limit: int = 100
    cost_weights: CostWeights = CostWeights()
    retry_limits: RetryLimits = RetryLimits()


class EmbeddingConfig(BaseModel):
    provider: str = "local"
    model: str = "all-MiniLM-L6-v2"
    endpoint: str = ""
    api_key: str = ""


class ToolConfig(BaseModel):
    name: str
    description: str
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
    embedding: EmbeddingConfig = EmbeddingConfig()
    tools: list[ToolConfig] = []
    mcp_servers: list[MCPServerConfig] = []
    app: AppConfig = AppConfig()

    @property
    def full_cycle_cost(self) -> int:
        cw = self.budget.cost_weights
        return (
            cw.decomposition
            + cw.tool_identification
            + cw.planning
            + cw.research
            + cw.synthesis
            + cw.research_review
            + cw.evaluation
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


def _repo_root() -> str:
    """Return the absolute path to the MOiRA project root.

    Walks up from this file (backend/moira/config.py) to find the directory
    containing the 'backend/' folder. This keeps data paths stable regardless
    of the process working directory.
    """
    return str(Path(__file__).resolve().parent.parent.parent)


def resolve_data_dir(config: MoiraConfig) -> str:
    """Return the absolute base directory for all persistent data.

    MOIRA_DATA_DIR env var takes precedence. Otherwise falls back to
    <repo_root>/data/ so that running from any CWD (backend/, repo root,
    etc.) always produces the same path.
    """
    logger.debug("Resolving data directory")
    data_dir = os.environ.get("MOIRA_DATA_DIR")
    if data_dir:
        logger.info("Using MOIRA_DATA_DIR=%s", data_dir)
        return data_dir
    derived = str(Path(_repo_root()) / "data")
    logger.info("Using derived data_dir=%s (repo root)", derived)
    return derived


def resolve_db_path(config: MoiraConfig) -> str:
    """Convenience: returns the SQLite database file path."""
    return str(Path(resolve_data_dir(config)) / "moira.db")


def resolve_lancedb_path(config: MoiraConfig) -> str:
    """Convenience: returns the LanceDB storage directory path."""
    return str(Path(resolve_data_dir(config)) / "vectors")
