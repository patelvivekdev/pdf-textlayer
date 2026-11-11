"""Entry point of the ``pdf-textlayer`` command. The command itself is Rust."""

from __future__ import annotations

import sys

from ._native import run_cli


def main(argv: list[str] | None = None) -> int:
    """Run the command with ``argv`` (default: ``sys.argv[1:]``)."""
    args = sys.argv[1:] if argv is None else argv
    sys.stdout.flush()
    return run_cli(["pdf-textlayer", *args])


if __name__ == "__main__":
    raise SystemExit(main())
