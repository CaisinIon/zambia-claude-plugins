#!/usr/bin/env python3
"""Create a venv and install requirements.txt. Safe to re-run.

Usage: bash bootstrap.sh [--check] [--venv PATH]   (or: python bootstrap.py ...)
Prints the venv python path on the last stdout line.
bootstrap.sh picks uv or a working Python on macOS, Linux and Windows (Git Bash).
Uses only the standard library so it runs before dependencies exist
(kept independent from zmlog.py for that reason; its plugin/project root
logic is intentionally duplicated in miniature here, not imported).

Project root and requirements.txt location:
- `$ZM_ROOT`, when set, is always authoritative.
- Otherwise, when running from a Claude Code plugin install (an ancestor
  directory holds `.claude-plugin/plugin.json`), requirements.txt is read
  from that plugin root and the project root is the current working
  directory — a plugin install ships no writable `input/`/`output/`.
- Otherwise, the nearest ancestor of this file containing both `input/`
  and `requirements.txt` (the project-local dev layout), else CWD.

If `uv` is on PATH, the venv is created with `uv venv --python 3.12` and packages
are installed with `uv pip`; uv downloads Python itself when the machine has none.
Without uv it uses the standard `venv` module and pip (needs a system python3).

Venv location defaults to `<project root>/.venv`; override with `--venv`
so a plugin install can put it under `${CLAUDE_PLUGIN_DATA}` instead of
inside the (non-writable, replaced-on-update) plugin folder.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

REQUIRED_IMPORTS = ["openpyxl", "requests", "pypdf", "rapidfuzz", "jsonschema", "bs4"]
UV_PYTHON = "3.12"  # used only when `uv` is available; uv downloads it if the machine has no suitable Python


def use_utf8_stdio() -> None:
    """Windows pipes default to cp1252; a non-ASCII venv path would crash print()."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    # pip/uv output may not be valid in the Windows code page; never fail on decoding it.
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def log(level: str, msg: str) -> None:
    print(f"{level} [bootstrap] {msg}", file=sys.stderr)


def plugin_root() -> Path | None:
    """Nearest ancestor of this file holding `.claude-plugin/plugin.json`, or None."""
    for parent in Path(__file__).resolve().parents:
        if (parent / ".claude-plugin" / "plugin.json").is_file():
            return parent
    return None


def find_root() -> Path:
    env = os.environ.get("ZM_ROOT")
    if env:
        return Path(env).resolve()
    if plugin_root() is not None:
        return Path.cwd().resolve()
    for parent in Path(__file__).resolve().parents:
        if (parent / "input").is_dir() and (parent / "requirements.txt").is_file():
            return parent
    return Path.cwd().resolve()


def venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def deps_ok(py: Path) -> bool:
    code = "import " + ",".join(REQUIRED_IMPORTS)
    return run([str(py), "-c", code]).returncode == 0


def main() -> int:
    use_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="only report status, do not install")
    ap.add_argument("--venv", type=Path, default=None,
                    help="venv location (default: <project root>/.venv)")
    args = ap.parse_args()

    root = find_root()
    proot = plugin_root()
    mode = "plugin" if proot is not None else "project"
    venv_dir = args.venv.resolve() if args.venv is not None else (root / ".venv")
    py = venv_python(venv_dir)
    req = (proot / "requirements.txt") if proot is not None else (root / "requirements.txt")
    log("INFO", f"mode={mode} root={root} venv={venv_dir} requirements={req}")

    if py.exists() and deps_ok(py):
        log("INFO", "venv present and dependencies importable")
        print(py)
        return 0
    if args.check:
        log("WARN", "venv missing or dependencies not installed")
        return 1
    if not req.is_file():
        log("ERROR", f"requirements.txt not found at {req}")
        return 2
    uv = shutil.which("uv")
    used_uv = False
    if not py.exists() and uv:
        # uv fetches a private Python itself when the machine has none, so the customer needs no Python install.
        log("INFO", f"creating venv at {venv_dir} with uv (python {UV_PYTHON})")
        r = run([uv, "venv", "--python", UV_PYTHON, str(venv_dir)])
        if r.returncode == 0:
            used_uv = True
        else:
            log("WARN", f"uv venv failed, falling back to the standard venv module:\n{r.stdout}\n{r.stderr}")
    if not py.exists():
        log("INFO", f"creating venv at {venv_dir}")
        venv.EnvBuilder(with_pip=True).create(venv_dir)
    log("INFO", "installing requirements" + (" (uv pip)" if used_uv else ""))
    if used_uv:
        cmd = [uv, "pip", "install", "-q", "--python", str(py), "-r", str(req)]
    else:
        cmd = [str(py), "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", str(req)]
    res = run(cmd)
    if res.returncode != 0:
        log("ERROR", f"pip install failed:\n{res.stdout}\n{res.stderr}")
        return 3
    if not deps_ok(py):
        log("ERROR", "dependencies still not importable after install")
        return 4
    log("INFO", "bootstrap complete")
    print(py)
    return 0


if __name__ == "__main__":
    sys.exit(main())
