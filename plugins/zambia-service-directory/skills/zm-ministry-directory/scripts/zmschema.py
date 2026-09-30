"""JSON-schema validation for roster, agency, passport and ministry files."""
from __future__ import annotations

from functools import lru_cache

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from zmlog import get_logger, load_json, references_dir

log = get_logger("schema")
SCHEMA_NAMES = ["passport", "agency", "roster", "ministries"]


@lru_cache(maxsize=1)
def _registry() -> Registry:
    resources = []
    for name in SCHEMA_NAMES:
        path = references_dir() / "schemas" / f"{name}.schema.json"
        resources.append((f"{name}.schema.json", Resource.from_contents(load_json(path))))
    return Registry().with_resources(resources)


def schema_errors(data, name: str) -> list[str]:
    """Return human-readable schema errors ('path: message'); empty list when valid."""
    schema = _registry().contents(f"{name}.schema.json")
    validator = Draft202012Validator(schema, registry=_registry())
    errors = []
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        path = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{path}: {err.message}")
    log.debug("schema check name=%s errors=%d", name, len(errors))
    return errors
