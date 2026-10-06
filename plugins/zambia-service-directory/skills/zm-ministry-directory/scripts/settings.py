#!/usr/bin/env python3
"""Run profiles: models, parallelism and verification depth, chosen at startup.

Usage:
  settings.py show [--profile fast|balanced|thorough] [--set key=value ...]
  settings.py list
  settings.py alias <model>      # claude-opus-5-5 -> opus (the name the Agent tool accepts)

Reads <project>/input/settings.json (falls back to templates/settings.default.json).
Prints the resolved settings as JSON. Exit 2 on an unknown profile, key or bad value.

The Agent tool only accepts the short names sonnet / opus / haiku / fable for its `model` argument, and an agent
started without one falls back to the model in its own file. `agent_models` in the resolved settings holds the short
name for each role: pass exactly that as `model` on every Agent call.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from zmlog import get_logger, load_json, project_root, templates_dir

log = get_logger("settings")

SCHEMA = {
    "roster_model": str, "researcher_model": str, "verifier_model": str,
    "max_parallel": int, "verifier_chunk": int, "audit_sample": int,
    "reverify_scope": ("pending", "all"),
    "live_check": ("script", "llm"),
    "audit_focus": ("researcher_written", "all"),
}
MODEL_RE = re.compile(r"^(claude-[a-z0-9.-]+|sonnet|opus|haiku|fable)$")
AGENT_ALIASES = ("sonnet", "opus", "haiku", "fable")
FAMILY_RE = re.compile(r"^claude-(sonnet|opus|haiku|fable)(?:-|$)")
AGENT_ROLES = ("roster", "researcher", "verifier")


class SettingsError(Exception):
    pass


def agent_alias(model: str) -> str:
    """The short model name the Agent tool accepts: 'claude-opus-5-5' -> 'opus'."""
    name = str(model).strip()
    if name in AGENT_ALIASES:
        return name
    hit = FAMILY_RE.match(name)
    if hit:
        return hit.group(1)
    raise SettingsError(f"{model!r}: cannot map to an agent model name; use one of {list(AGENT_ALIASES)}")


def settings_path() -> Path:
    return project_root() / "input" / "settings.json"


def load_settings(path: Path | None = None) -> dict:
    path = path or settings_path()
    if not path.exists():
        path = templates_dir() / "settings.default.json"
    data = load_json(path)
    if "profiles" not in data or not data["profiles"]:
        raise SettingsError(f"{path}: no profiles defined")
    return data


def _cast(key: str, raw):
    rule = SCHEMA.get(key)
    if rule is None:
        raise SettingsError(f"unknown setting {key!r}; valid: {sorted(SCHEMA)}")
    if isinstance(rule, tuple):
        if raw not in rule:
            raise SettingsError(f"{key}={raw!r}: must be one of {list(rule)}")
        return raw
    if rule is int:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise SettingsError(f"{key}={raw!r}: must be an integer") from None
        if value < 0 or (key == "max_parallel" and value < 1):
            raise SettingsError(f"{key}={value}: out of range")
        return value
    if not MODEL_RE.match(str(raw)):
        raise SettingsError(f"{key}={raw!r}: not a model id or alias (e.g. claude-sonnet-5-5, sonnet, opus)")
    agent_alias(raw)
    return str(raw)


def resolve(profile: str | None = None, overrides: dict | None = None, path: Path | None = None) -> dict:
    data = load_settings(path)
    name = profile or data.get("default_profile") or next(iter(data["profiles"]))
    if name not in data["profiles"]:
        raise SettingsError(f"unknown profile {name!r}; available: {list(data['profiles'])}")
    merged = {**data["profiles"][name], **(overrides or {})}
    resolved = {"profile": name}
    for key in SCHEMA:
        if key not in merged:
            raise SettingsError(f"profile {name!r} is missing {key!r}")
        resolved[key] = _cast(key, merged[key])
    resolved["agent_models"] = {role: agent_alias(resolved[f"{role}_model"]) for role in AGENT_ROLES}
    resolved["description"] = data["profiles"][name].get("description", "")
    resolved["overrides"] = {k: _cast(k, v) for k, v in (overrides or {}).items()}
    log.debug("resolved profile=%s overrides=%s", name, resolved["overrides"])
    return resolved


def parse_overrides(pairs: list[str]) -> dict:
    out = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise SettingsError(f"--set expects key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("show")
    sp.add_argument("--profile")
    sp.add_argument("--set", dest="sets", action="append", default=[])
    sub.add_parser("list")
    ap_alias = sub.add_parser("alias")
    ap_alias.add_argument("model")
    args = ap.parse_args()
    try:
        if args.cmd == "alias":
            print(agent_alias(args.model))
            return 0
        if args.cmd == "list":
            data = load_settings()
            for name, p in data["profiles"].items():
                mark = " (default)" if name == data.get("default_profile") else ""
                print(f"{name}{mark}: {p.get('description', '')}")
            return 0
        print(json.dumps(resolve(args.profile, parse_overrides(args.sets)), indent=2))
        return 0
    except SettingsError as exc:
        log.error("%s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
