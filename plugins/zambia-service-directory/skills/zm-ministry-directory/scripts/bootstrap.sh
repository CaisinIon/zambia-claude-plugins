#!/usr/bin/env bash
# Start bootstrap.py with a working Python on macOS, Linux and Windows (Git Bash).
# Order: uv (downloads its own Python 3.12); if that fails, the Python uv installed (uv python find);
# then the first of python3 / python / py that is really Python 3.10+. On Windows `python3` is often a
# Microsoft Store stub that only prints an install hint, so each candidate is test-run, not just looked up.
# uv can fail in Git Bash (it rejects the symlink Git Bash makes for its Python), so a failed uv run
# falls through to the next candidate instead of ending the script.
# Arguments go to bootstrap.py unchanged. Last stdout line = the venv python path.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if command -v uv >/dev/null 2>&1; then
  uv run --no-project --python 3.12 "$here/bootstrap.py" "$@" && exit 0
  echo "WARN [bootstrap] uv run failed (exit $?); trying the Python that uv installed, then other Pythons." >&2
  uv_python="$(uv python find --no-project 3.12 2>/dev/null | tail -n 1)"
  if [ -n "$uv_python" ] && "$uv_python" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    exec "$uv_python" "$here/bootstrap.py" "$@"
  fi
fi
for candidate in python3 python py; do
  if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    exec "$candidate" "$here/bootstrap.py" "$@"
  fi
done
echo "ERROR [bootstrap] no Python 3.10+ found. Install uv (https://docs.astral.sh/uv/getting-started/installation/) or Python 3.12, then run this again." >&2
exit 5
