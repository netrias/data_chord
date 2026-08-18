"""Build the strict JSON schema required by the Bedrock ranking tool."""

from typing import Any

from src.cde_recommend.types import ClosestMatchesIndex


def build_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a strict schema without changing the supplied schema."""
    # Shallow-copy here and recurse: each recursive call returns a fresh dict,
    # so the caller's schema graph is never reached through aliased references.
    result = dict(schema)

    if result.get("type") == "object":
        props = result.get("properties")
        if isinstance(props, dict):
            result["properties"] = {key: build_strict_schema(value) for key, value in props.items()}
            result["required"] = sorted(props.keys())
        result.setdefault("additionalProperties", False)
    elif result.get("type") == "array" and isinstance(result.get("items"), dict):
        result["items"] = build_strict_schema(result["items"])

    defs = result.get("$defs")
    if isinstance(defs, dict):
        result["$defs"] = {key: build_strict_schema(value) for key, value in defs.items()}

    return result


CLOSEST_MATCHES_SCHEMA = build_strict_schema(ClosestMatchesIndex.model_json_schema())
