#!/usr/bin/env python3
"""Project entry. Run: python main.py"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rpent_wrs.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
