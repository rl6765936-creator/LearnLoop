"""Load the shared JSON Schemas (contracts/) and validate data against them.

A JSON Schema is a formal description of what a JSON document must look like.
Every piece of data that crosses a component boundary is checked here, so no
component has to "guess" what another one meant (spec §59, APPROVED).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import jsonschema

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"
ENVELOPE = "transfer_envelope"
ANALYSIS = "content_analysis"
DECISION = "approval"
ACTIVITY = "activity"
GRADING = "grading"


class ContractError(ValueError):
    """Data does not match its contract. The message never contains the content itself."""


@lru_cache(maxsize=None)
def _validator(name: str) -> jsonschema.Draft202012Validator:
    schema = json.loads((CONTRACTS_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def validate(name: str, data: object) -> None:
    errors = sorted(_validator(name).iter_errors(data), key=lambda e: list(e.path))
    if errors:
        # Report WHERE and WHICH rule failed – not the value, which may be study content.
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "(root)"
        raise ContractError(f"{name}: invalid at {where} (rule: {first.validator}); "
                            f"{len(errors)} problem(s)")


def schema(name: str) -> dict:
    """The contract itself (a copy), e.g. to give a model provider the required answer shape."""
    import copy
    return copy.deepcopy(_validator(name).schema)
