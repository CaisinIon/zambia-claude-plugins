"""Shared logging and path helpers for the Zambia service-directory harness.

Log level comes from LOG_LEVEL (DEBUG, INFO, WARNING, ERROR; default INFO).
Set LOG_LEVEL=DEBUG for verbose tracing. When ZM_RUN_DIR is set, logs are
also appended to <ZM_RUN_DIR>/run.log.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_configured = False


def use_utf8_stdio() -> None:
    """Print UTF-8 on every OS. On Windows a piped stdout uses the ANSI code page
    (cp1252), which cannot encode eServices text such as U+202F and crashes the script."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):  # replaced or detached stream (e.g. pytest capture)
            pass


use_utf8_stdio()


def _configure_root() -> None:
    global _configured
    if _configured:
        return
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger("zm")
    root.setLevel(level)
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(stderr)
    run_dir = os.environ.get("ZM_RUN_DIR")
    if run_dir:
        Path(run_dir).mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(Path(run_dir) / "run.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(fh)
    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the 'zm' namespace, configuring handlers once."""
    _configure_root()
    return logging.getLogger(f"zm.{name}")


def kv(**fields) -> str:
    """Render structured fields for log lines: kv(id=1, n=2) -> '{"id": 1, "n": 2}'."""
    return json.dumps(fields, ensure_ascii=False, default=str)


def skill_dir() -> Path:
    """Directory of the zm-ministry-directory skill (parent of scripts/)."""
    return Path(__file__).resolve().parent.parent


def plugin_root() -> Path | None:
    """Nearest ancestor of this file that is a Claude Code plugin install root.

    A plugin install is identified by `.claude-plugin/plugin.json` two levels
    above `scripts/` (mirrors `skill_dir()`'s own parent walk). Returns None
    when these scripts are running from a project-local `.claude/skills/` tree.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / ".claude-plugin" / "plugin.json").is_file():
            return parent
    return None


def project_root() -> Path:
    """Project (working-folder) root.

    Resolution order:
    1. `$ZM_ROOT`, when set — always authoritative (tests and `--smoke` rely on this,
       and re-check it on every call: they monkeypatch it per-test in the same
       process, so this function must stay uncached).
    2. When running from a plugin install (see `plugin_root()`), the current
       working directory — the plugin's own folder must never be treated as
       the user's project, since it holds no writable `input/`/`output/`/`cache/`.
    3. The nearest ancestor of this file that contains `input/` — the
       project-local dev layout (`dist/` folder build, this repo's own
       `.claude/skills/...`).
    4. The current working directory, as a last resort.
    """
    env = os.environ.get("ZM_ROOT")
    if env:
        root, reason = Path(env).resolve(), "ZM_ROOT"
    elif plugin_root() is not None:
        root, reason = Path.cwd().resolve(), "plugin-cwd"
    else:
        root, reason = None, None
        for parent in Path(__file__).resolve().parents:
            if (parent / "input").is_dir():
                root, reason = parent, "walk-up"
                break
        if root is None:
            root, reason = Path.cwd().resolve(), "cwd"
    get_logger("path").debug("project_root resolved %s", kv(root=str(root), reason=reason))
    return root


def references_dir() -> Path:
    return skill_dir() / "references"


def templates_dir() -> Path:
    return skill_dir() / "templates"


def load_json(path: Path | str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(data, path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
