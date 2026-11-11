"""Time each version of the writer on the synthetic dataset.

    uv run python benchmarks/run.py \\
        --variant original=/path/to/worktree-at-v0.1/src \\
        --variant batched=/path/to/worktree-at-v0.2/src \\
        --variant rust=

A variant is ``name=path``: ``path`` goes on ``PYTHONPATH`` so that version of
``pdf_textlayer`` is imported. An empty path uses the installed package.
Every measurement runs in a fresh process pinned to one CPU core, and the
reported value is the median of ``--runs`` runs.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

from generate import DATASET, PROFILE_LABELS

HERE = Path(__file__).resolve().parent


def measure(
    variant_path: str, pdf_path: Path, output_path: Path, core: int | None
) -> dict:
    environment = dict(os.environ)

    if variant_path:
        environment["PYTHONPATH"] = variant_path

    command = [
        sys.executable,
        str(HERE / "worker.py"),
        str(pdf_path),
        str(pdf_path.with_suffix(".boxes.json")),
        str(output_path),
    ]

    if core is not None:
        command = ["taskset", "-c", str(core), *command]

    completed = subprocess.run(
        command, env=environment, capture_output=True, text=True, check=True
    )

    return json.loads(completed.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--variant", action="append", required=True)
    parser.add_argument("--data", type=Path, default=HERE / "data")
    parser.add_argument("--out", type=Path, default=HERE / "out")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--core",
        type=int,
        default=2,
        help="CPU core to pin to. -1 leaves the process unpinned.",
    )
    args = parser.parse_args()

    variants = dict(variant.split("=", 1) for variant in args.variant)
    core = None if args.core < 0 else args.core
    results: list[dict] = []

    for profile, pages in DATASET:
        pdf_path = args.data / f"{profile}_{pages}.pdf"
        boxes = json.loads(pdf_path.with_suffix(".boxes.json").read_text())
        row: dict = {
            "profile": profile,
            "label": PROFILE_LABELS[profile],
            "pages": pages,
            "ocr_boxes": boxes["ocr_boxes"],
            "input_mb": pdf_path.stat().st_size / 1e6,
            "variants": {},
        }

        for name, variant_path in variants.items():
            output_path = args.out / name / pdf_path.name
            output_path.parent.mkdir(parents=True, exist_ok=True)
            runs = [
                measure(variant_path, pdf_path, output_path, core)
                for _ in range(args.runs)
            ]
            row["variants"][name] = {
                "seconds": statistics.median(run["seconds"] for run in runs),
                "cpu_seconds": statistics.median(run["cpu_seconds"] for run in runs),
                "peak_rss_mb": statistics.median(run["peak_rss_mb"] for run in runs),
                "spread_seconds": max(run["seconds"] for run in runs)
                - min(run["seconds"] for run in runs),
                "items_drawn": runs[0]["items_drawn"],
                "ocr_pages": runs[0]["ocr_pages"],
                "output_mb": output_path.stat().st_size / 1e6,
            }
            print(
                f"{pdf_path.name:34} {name:10} "
                f"{row['variants'][name]['seconds']:8.3f} s",
                flush=True,
            )

        results.append(row)

    results_path = args.out / "results.json"
    results_path.write_text(json.dumps(results, indent=2))
    print(f"Wrote {results_path}")


if __name__ == "__main__":
    main()
