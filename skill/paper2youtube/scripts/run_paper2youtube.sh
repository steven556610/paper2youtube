#!/usr/bin/env bash
# Thin wrapper so agents can run the pipeline without remembering the repo path.
set -euo pipefail
ROOT="${PAPER2YOUTUBE_ROOT:-$HOME/ai_for_research/paper2youtube}"
PY="${PAPER2YOUTUBE_PYTHON:-python3}"
exec "$PY" "$ROOT/run.py" "$@"