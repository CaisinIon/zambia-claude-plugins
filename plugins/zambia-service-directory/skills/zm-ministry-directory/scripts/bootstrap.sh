#!/usr/bin/env bash
# Start bootstrap.py with a working Python on macOS, Linux and Windows (Git Bash).
# Order: uv (downloads its own Python 3.12), then the first of python3 / python / py
# that is really Python 3.10+. On Windows `python3` is often a Microsoft Store stub
# that only prints an install hint, so each candidate is test-run, not just looked up.
# Arguments go to bootstrap.py unchanged. Last stdout line = the venv python path.
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if command -v uv >/dev/null 2>&1; then
  exec uv run --no-project --python 3.12 "$here/bootstrap.py" "$@"
fi
for candidate in python3 python py; do
  if "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    exec "$candidate" "$here/bootstrap.py" "$@"
  fi
done
echo "ERROR [bootstrap] no Python 3.10+ found. Install uv (https://docs.astral.sh/uv/getting-started/installation/) or Python 3.12, then run this again." >&2
exit 5
