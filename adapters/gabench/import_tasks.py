#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


LFS_PREFIX = "version https://git-lfs.github.com/spec/v1"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a local metadata manifest from a GABench checkout."
    )
    parser.add_argument("--source", type=Path, required=True, help="Path to GABench checkout")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    csv_path = args.source / "benchmark" / "benchmark.csv"
    if not csv_path.exists():
        raise SystemExit(f"Missing {csv_path}")

    head = csv_path.read_text(encoding="utf-8", errors="replace")[:200]
    if head.startswith(LFS_PREFIX):
        raise SystemExit(
            "benchmark.csv is still a Git LFS pointer. "
            "Run `git lfs pull` inside the GABench checkout first."
        )

    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise SystemExit("GABench benchmark.csv has no header")
        rows = list(reader)

    manifest = {
        "adapter": "gabench",
        "schema_version": "0.1",
        "source_root": str(args.source.resolve()),
        "source_csv": str(csv_path.resolve()),
        "columns": reader.fieldnames,
        "task_count": len(rows),
        "tasks": [
            {
                "source_row": index + 2,
                "source_fields": row,
            }
            for index, row in enumerate(rows)
        ],
        "note": (
            "Local interoperability manifest only. It contains upstream metadata "
            "and should not be committed until upstream licensing is clarified."
        ),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote {len(rows)} tasks to {args.output}")
    print("Columns:")
    for column in reader.fieldnames:
        print(f"  - {column}")


if __name__ == "__main__":
    main()
