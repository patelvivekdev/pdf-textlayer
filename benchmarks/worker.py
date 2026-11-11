"""Run one writer once and print its timing as JSON.

Started by ``run.py`` in a fresh process for every measurement, with
``PYTHONPATH`` pointing at the version of ``pdf_textlayer`` under test.
Only the ``write_textlayer`` call is timed: imports and loading the boxes
are the same work for every version.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import time

from pdf_textlayer import ParsedPage, TextBox, write_textlayer


def peak_rss_mb() -> float:
    """Peak resident memory of this process.

    Read from ``VmHWM`` because ``getrusage`` also counts the parent's memory
    from before this process was started.
    """
    with open("/proc/self/status") as status:
        for line in status:
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / 1024

    return 0.0


def main() -> None:
    pdf_path, boxes_path, output_path = sys.argv[1:4]

    with open(boxes_path) as boxes_file:
        document = json.load(boxes_file)

    # The first release had no ``rotation`` field.
    field_count = len(dataclasses.fields(TextBox))
    pages = [
        ParsedPage(
            page["page"],
            [TextBox(*box[:field_count]) for box in page["boxes"]],
        )
        for page in document["parsed_pages"]
    ]

    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    stats = write_textlayer(pdf_path, pages, output_path)
    cpu_seconds = time.process_time() - cpu_start
    wall_seconds = time.perf_counter() - wall_start

    print(
        json.dumps(
            {
                "seconds": wall_seconds,
                "cpu_seconds": cpu_seconds,
                "peak_rss_mb": peak_rss_mb(),
                "items_drawn": stats["items_drawn"],
                "ocr_pages": len(stats["ocr_pages"]),
            }
        )
    )


if __name__ == "__main__":
    main()
