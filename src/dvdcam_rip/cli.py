"""Command-line entry point (placeholder until the IFO parser lands)."""

import argparse
import sys

from . import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dvdcam-rip",
        description="Split a DVD camcorder disc into one file per recording.",
    )
    parser.add_argument("source", help="disc root, VIDEO_TS folder, or .iso image")
    parser.add_argument("-o", "--output", default="output", help="output folder")
    parser.add_argument("--list", action="store_true", help="only list recordings")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    print(f"dvdcam-rip {__version__}: not implemented yet ({args.source})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
