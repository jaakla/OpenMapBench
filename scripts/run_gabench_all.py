#!/usr/bin/env python3
"""Repository entry point for the OpenMapBench GABench batch runner."""

import sys
from pathlib import Path

# Use the repository source tree so the script always matches the checked-out revision.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openmapbench.gabench_batch import main

if __name__ == "__main__":
    raise SystemExit(main())
