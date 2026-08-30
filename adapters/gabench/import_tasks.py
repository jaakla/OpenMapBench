#!/usr/bin/env python3
"""Backward-compatible entry point for the packaged GABench importer."""

import sys

from openmapbench.cli import app

if __name__ == "__main__":
    sys.argv.insert(1, "gabench-import")
    app()
