"""Load and parse prompt templates from an external Markdown file.

The file uses `## section.key` headings to delimit prompt sections. The loader
splits on those headings and returns a dict mapping section keys to their text
content (stripped of leading/trailing whitespace).

Default path: moira/resources/prompts.md (shipped with the package).
Override with the MOIRA_PROMPT_FILE environment variable.
"""

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "resources", "prompts.md")

_PROMPTS: dict[str, str] | None = None

# Every prompt section key required by the workflow nodes. The loader validates
# that all of these are present and non-empty at startup. When adding a new
# node or prompt variant, add the key here -- CI will catch omissions.
REQUIRED_SECTIONS = [
    "decomposition.system",
    "decomposition.user",
    "planning.system",
    "planning.user",
    "planning.system_retry",
    "planning.system_prior_report",
    "planning.system_earlier_turns",
    "research.system",
    "research.user",
    "research.parse_correction",
    "research.summary",
    "research.tool_feedback",
    "research.fact_extraction.system",
    "research.fact_extraction.user",
    "synthesis.system",
    "synthesis.user",
    "synthesis.system_retry",
    "verification.system",
    "verification.user",
    "verification.evidence",
    "report_generation.system",
    "report_generation.path_verified",
    "report_generation.path_budget_exhausted",
    "report_generation.path_retry_overruled",
    "report_generation.path_error",
    "report_generation.user",
    "tool_enrichment.system",
    "tool_enrichment.user",
]


def _resolve_path() -> str:
    env = os.environ.get("MOIRA_PROMPT_FILE")
    if env:
        logger.info("Using MOIRA_PROMPT_FILE=%s", env)
        return env
    return _DEFAULT_PATH


def _parse(content: str) -> dict[str, str]:
    """Split markdown content on `## key` headings into a dict."""
    prompts: dict[str, str] = {}
    # Match ## heading lines (the section delimiter)
    pattern = re.compile(r"^##\s+(\S+)\s*$", re.MULTILINE)
    matches = list(pattern.finditer(content))

    for i, match in enumerate(matches):
        key = match.group(1)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        # Skip the heading line itself, take everything until next heading
        body = content[start:end].strip()
        prompts[key] = body

    logger.info("Loaded %d prompt sections", len(prompts))
    return prompts


def load_prompts() -> dict[str, str]:
    """Load prompts from file. Results are cached for the process lifetime."""
    global _PROMPTS
    if _PROMPTS is not None:
        return _PROMPTS

    path = _resolve_path()
    p = Path(path)
    if not p.exists():
        raise SystemExit(
            f"Prompt file not found: {path}\n"
            "Expected config/prompts.md with ## section.key headings."
        )

    logger.info("Loading prompts from %s", p.resolve())
    content = p.read_text(encoding="utf-8")
    _PROMPTS = _parse(content)
    _validate_required(_PROMPTS)
    return _PROMPTS


def _validate_required(prompts: dict[str, str]) -> None:
    """Fail fast if any required section is missing or empty."""
    missing = [k for k in REQUIRED_SECTIONS if k not in prompts]
    if missing:
        raise SystemExit(
            f"Prompt file is missing required sections: {missing}\n"
            f"Present sections: {sorted(prompts.keys())}"
        )
    empty = [k for k in REQUIRED_SECTIONS if not prompts[k].strip()]
    if empty:
        raise SystemExit(f"Prompt sections are empty: {empty}")


def get_prompt(key: str) -> str:
    """Retrieve a single prompt template by key (e.g. 'planning.system').

    Returns the template string. Callers format it with .format(**kwargs).
    Raises KeyError if the key is not found in the prompts file.
    """
    prompts = load_prompts()
    if key not in prompts:
        raise KeyError(
            f"Prompt section '{key}' not found in prompts file. "
            f"Available sections: {sorted(prompts.keys())}"
        )
    return prompts[key]
