#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# `import jsonschema` succeeds on the 3.x that ships with the system Python, and
# only the Draft202012Validator import fails, so the probe has to name the
# symbol the validator actually uses. Version numbers are the wrong predicate
# for the same reason CI runs this suite on both 3.10 and 3.13.
python_is_usable() {
  "$1" - <<'PY' >/dev/null 2>&1 || return 1
from jsonschema import Draft202012Validator  # noqa: F401
PY
  "$1" -m ruff --version >/dev/null 2>&1
}

find_python() {
  for candidate in \
    "${CONDA_PREFIX:-/nonexistent}/bin/python3" \
    python3 python python3.13 python3.12 python3.11 \
    "$HOME/miniconda3/bin/python3"
  do
    command -v "$candidate" >/dev/null 2>&1 || continue
    python_is_usable "$candidate" && { printf '%s\n' "$candidate"; return 0; }
  done
  return 1
}

if [[ -n "${PYTHON_BIN:-}" ]]; then
  if ! python_is_usable "$PYTHON_BIN"; then
    echo "[check] ERROR: PYTHON_BIN=$PYTHON_BIN cannot import jsonschema.Draft202012Validator or run ruff." >&2
    echo "[check] Install them with: $PYTHON_BIN -m pip install -r requirements-dev.txt" >&2
    exit 1
  fi
  PYTHON="$PYTHON_BIN"
elif ! PYTHON="$(find_python)"; then
  echo "[check] ERROR: found no Python with jsonschema>=4.23 and ruff." >&2
  echo "[check] Install them (python3 -m pip install -r requirements-dev.txt)" >&2
  echo "[check] or set PYTHON_BIN=/path/to/python and rerun." >&2
  exit 1
fi

echo "[check] Using Python: $("$PYTHON" -c 'import sys; print(sys.executable)')"

echo "[check] Ruff"
"$PYTHON" -m ruff check .

echo "[check] Ruff format"
"$PYTHON" -m ruff format --check .

echo "[check] Fixtures, registry, and semantic rules"
"$PYTHON" -m unittest discover -s tests
