"""Hypermedia URL pruning shared by tool serialization and research feedback.

JSON APIs (PokeAPI being the observed case) serialize every relationship as
a full URL, so the first few thousand chars of a payload — often all the
model sees under the feedback cap or the citation content limit — can be
almost entirely link walls with no identifying data (types/stats/moves
pushed past the truncation point).

The rule is deliberately general, not API-specific: drop a URL-bearing
field only when a sibling identifying field exists in the same object —
the URL is then a redundant link and the identifier carries the meaning.
When a URL is the sole content of an object, keep it; prose bodies are
never touched.

Pruning must run while the payload is still a parsed object (before
truncation): a mid-JSON slice no longer parses, so post-hoc pruning of
stored/truncated strings silently no-ops. RESTTool therefore prunes in
``_serialize_json_truncated`` at the source; research additionally prunes
valid-JSON model-facing copies (feedback bodies, recall_source serving).
"""

import json
from typing import Any

# Sibling keys that identify an object independently of its URL. When one of
# these is present, a `url`-like field in the same object is a redundant link.
IDENTIFYING_SIBLING_KEYS = frozenset(
    {
        "name",
        "title",
        "id",
        "identifier",
        "label",
        "key",
        "slug",
        "code",
    }
)

# Field names treated as URL-bearing. Prefix AND suffix matching so both
# `url_front`/`endpoint_uri` and `sprite_url`/`image_url` variants are
# covered; the value must still look like an http(s) URL to be pruned.
URL_FIELD_PREFIXES = ("url", "uri", "href", "link")
URL_FIELD_SUFFIXES = ("url", "uri", "href", "link")

# Skip pruning entirely below this size — the parse/prune pass costs more
# than it saves on small bodies.
PRUNE_MIN_BODY_LENGTH = 2_000


def _looks_like_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def prune_url_fields(node: Any) -> Any:
    """Recursively prune redundant URL fields from parsed JSON.

    Hypermedia rule: drop a URL-bearing field only when a sibling identifying
    field (see IDENTIFYING_SIBLING_KEYS) exists in the same object — the URL
    is then a redundant link and the identifier carries the meaning. When a
    URL is the sole identifying content of an object (no sibling), keep it.
    """
    if isinstance(node, dict):
        has_identifying_sibling = any(
            k in node and node[k] not in (None, "") for k in IDENTIFYING_SIBLING_KEYS
        )
        pruned = {}
        for k, v in node.items():
            kl = k.lower()
            is_url_field = kl.startswith(URL_FIELD_PREFIXES) or kl.endswith(URL_FIELD_SUFFIXES)
            if (
                has_identifying_sibling
                and is_url_field
                and isinstance(v, str)
                and _looks_like_url(v)
            ):
                continue
            pruned[k] = prune_url_fields(v)
        return pruned
    if isinstance(node, list):
        return [prune_url_fields(item) for item in node]
    return node


def prune_redundant_urls(body: str) -> str:
    """Densify JSON tool-result content by removing URL walls.

    If the body parses as JSON, prune recursively via the hypermedia rule
    (prune_url_fields) and re-serialize. Otherwise return it unchanged —
    prose pages legitimately contain URLs and we do not touch prose. Bodies
    under PRUNE_MIN_BODY_LENGTH skip the parse entirely (cost guard).
    """
    if not body or len(body) < PRUNE_MIN_BODY_LENGTH:
        return body
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return body
    pruned = prune_url_fields(parsed)
    return json.dumps(pruned, indent=2)
