"""Turn our contracts (contracts/*.schema.json) into the schema dialect each provider accepts.

Why this is needed: our contracts use full JSON Schema (lengths, numeric ranges, if/then).
Each provider's structured-output feature supports a DIFFERENT subset, and an unsupported
keyword makes the request fail (Anthropic returns HTTP 400). So we send each provider a
simplified copy, and keep the full contract as the real gate:

    provider schema  = "help the model produce the right shape"   (best effort)
    full contract    = "the code decides whether the answer is accepted"  (always enforced)

Nothing is lost by stripping a constraint: contracts.validate() still checks it afterwards.
Keyword support per provider is recorded in capabilities.py (verify again at activation).
"""

from __future__ import annotations

import copy

# Structural keywords every provider understands.
_CORE = {"type", "properties", "required", "items", "enum", "additionalProperties",
         "description", "anyOf"}

ALLOWED = {
    # Anthropic: no minLength/maxLength, minimum/maximum, maxItems; minItems only 0/1;
    # no if/then, not, oneOf. (Structured outputs doc, Nov 2026.)
    "anthropic": _CORE | {"minItems"},
    # OpenAI strict mode: we keep only the safe core (+ array bounds, numeric range).
    "openai": _CORE | {"minItems", "maxItems", "minimum", "maximum"},
    # Gemini: array/number bounds are left out. Gemini compiles the schema into a decoding
    # automaton, and bounds such as maxItems 200 on nested objects made it refuse the request
    # ("too many states for serving", real run gemini-free-5, 24.11.2026). The full contract
    # still enforces every bound afterwards.
    "gemini": _CORE - {"description"},
}


def _type_of_enum(values: list) -> str | None:
    kinds = {type(v) for v in values}
    if kinds == {str}:
        return "string"
    if kinds <= {int}:
        return "integer"
    return None


def for_provider(schema: dict, provider: str) -> dict:
    allowed = ALLOWED[provider]

    def walk(node):
        if not isinstance(node, dict):
            return node
        out = {}
        for key, value in node.items():
            if key not in allowed:
                continue
            if key == "additionalProperties" and provider == "gemini":
                continue                                   # fewer automaton states; contract checks it                                   # e.g. allOf/if/then, $schema, maxLength
            if key == "minItems" and provider == "anthropic" and value > 1:
                value = 1
            if key == "properties":
                out[key] = {name: walk(sub) for name, sub in value.items()}
            elif key == "items":
                out[key] = walk(value)
            elif key == "anyOf":
                out[key] = [walk(s) for s in value]
            else:
                out[key] = copy.deepcopy(value)
        if "enum" in out and "type" not in out:            # explicit type helps every provider
            t = _type_of_enum(out["enum"])
            if t:
                out["type"] = t
        if out.get("type") == "object" and "additionalProperties" not in out and provider != "gemini":
            out["additionalProperties"] = False             # Anthropic and OpenAI require this
        return out

    result = walk(schema)
    if provider == "openai":
        result = _openai_strict(result)
    return result


def _openai_strict(node: dict) -> dict:
    """OpenAI strict mode: every property must be listed in `required`. Optional properties
    therefore become "required but may be null"; strip_nulls() removes those nulls again."""
    if not isinstance(node, dict):
        return node
    node = dict(node)
    if node.get("type") == "object" and "properties" in node:
        required = set(node.get("required", []))
        props = {}
        for name, sub in node["properties"].items():
            sub = _openai_strict(sub)
            if name not in required:
                sub = _nullable(sub)
            props[name] = sub
        node["properties"] = props
        node["required"] = list(node["properties"])
        node["additionalProperties"] = False
    if "items" in node:
        node["items"] = _openai_strict(node["items"])
    return node


def _nullable(sub: dict) -> dict:
    sub = dict(sub)
    t = sub.get("type")
    if isinstance(t, list):
        if "null" not in t:
            sub["type"] = t + ["null"]
    elif isinstance(t, str):
        sub["type"] = [t, "null"]
    else:
        return {"anyOf": [sub, {"type": "null"}]}
    if "enum" in sub and None not in sub["enum"]:
        sub["enum"] = list(sub["enum"]) + [None]
    return sub


def strip_nulls(data, schema: dict):
    """Remove `null` values the ORIGINAL contract does not allow (they exist only because of
    the OpenAI strict-mode transform). Nulls the contract allows are kept."""
    if isinstance(data, dict) and isinstance(schema, dict):
        props = schema.get("properties", {})
        out = {}
        for key, value in data.items():
            sub = props.get(key, {})
            if value is None and not _allows_null(sub):
                continue
            out[key] = strip_nulls(value, sub)
        return out
    if isinstance(data, list) and isinstance(schema, dict):
        return [strip_nulls(v, schema.get("items", {})) for v in data]
    return data


def _allows_null(sub: dict) -> bool:
    t = sub.get("type")
    return t == "null" or (isinstance(t, list) and "null" in t) or None in sub.get("enum", [1])
