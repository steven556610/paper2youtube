#!/usr/bin/env bash
# Thin wrapper so agents can run the pipeline without remembering the repo path.
set -euo pipefail
if command -v paper2youtube >/dev/null 2>&1; then
  exec paper2youtube "$@"
fi
ROOT="${PAPER2YOUTUBE_ROOT:-$HOME/ai_for_research/paper2youtube}"
PY="${PAPER2YOUTUBE_PYTHON:-python3}"
exec "$PY" -m paper2youtube "$@" 2>/dev/null || exec "$PY" "$ROOT/run.py" "$@"