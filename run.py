#!/usr/bin/env python3
"""Backward-compatible launcher. Prefer: `paper2youtube` or `python -m paper2youtube`."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper2youtube.cli import main

if __name__ == "__main__":
    raise SystemExit(main())