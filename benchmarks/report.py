"""Print ``benchmarks/out/results.json`` as a Markdown table.

    uv run python benchmarks/report.py original batched rust

The first name is the baseline every speedup is measured against.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def seconds(value: float) -> str:
    return f"{value:.2f} s" if value >= 0.995 else f"{value * 1000:.0f} ms"


def main() -> None:
    names = sys.argv[1:]
    results = json.loads((HERE / "out" / "results.json").read_text())
    baseline = names[0]

    header = ["Document profile", "Pages", "OCR boxes"]
    header += names
    header += [f"{name} vs {baseline}" for name in names[1:]]
    header.append("Peak RSS (MB)")
    print("| " + " | ".join(header) + " |")
    print("|" + "|".join(["---"] + ["--:"] * (len(header) - 1)) + "|")

    totals = dict.fromkeys(names, 0.0)

    for row in results:
        variants = row["variants"]
        cells = [row["label"], str(row["pages"]), f"{row['ocr_boxes']:,}"]
        cells += [seconds(variants[name]["seconds"]) for name in names]
        cells += [
            f"{variants[baseline]['seconds'] / variants[name]['seconds']:.1f}x"
            for name in names[1:]
        ]
        cells.append(
            " / ".join(f"{variants[name]['peak_rss_mb']:.0f}" for name in names)
        )
        print("| " + " | ".join(cells) + " |")

        for name in names:
            totals[name] += variants[name]["seconds"]

    cells = ["**Total**", "", ""]
    cells += [f"**{seconds(totals[name])}**" for name in names]
    cells += [f"**{totals[baseline] / totals[name]:.1f}x**" for name in names[1:]]
    cells.append("")
    print("| " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
