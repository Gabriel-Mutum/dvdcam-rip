"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .ifo import Disc, IfoError, read_disc
from .recordings import SPLIT_MODES, Recording, list_recordings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dvdcam-rip",
        description="Split a DVD camcorder disc into one file per recording.",
    )
    parser.add_argument("source", help="disc root (e.g. D:\\) or VIDEO_TS folder")
    parser.add_argument("-o", "--output", default="output", help="output folder")
    parser.add_argument("--list", action="store_true", help="only list recordings")
    parser.add_argument("--json", action="store_true", help="with --list: print JSON")
    parser.add_argument(
        "--split",
        choices=SPLIT_MODES,
        default="chapter",
        help="what counts as one recording (default: chapter)",
    )
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    try:
        disc = read_disc(Path(args.source))
    except (IfoError, OSError) as e:
        print(f"dvdcam-rip: {e}", file=sys.stderr)
        return 2

    recordings = list_recordings(disc, args.split)

    if args.json:
        print(json.dumps(_as_json(disc, recordings), indent=2))
        return 0

    _print_table(disc, recordings)
    if not args.list:
        print("\nConversion is not implemented yet; use --list for now.", file=sys.stderr)
        return 1
    return 0


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def _print_table(disc: Disc, recordings: list[Recording]) -> None:
    print(f"Disc: {disc.root}")
    for n, vts in disc.title_sets.items():
        v = vts.video
        audio = ", ".join(f"{a.codec} {a.channels}ch {a.sample_rate // 1000}kHz" for a in vts.audio)
        print(f"  VTS {n:02d}: {v.codec} {v.width}x{v.height} {v.standard} {v.aspect}; audio: {audio or 'none'}")
    total = sum(r.duration_seconds for r in recordings)
    print(f"{len(recordings)} recordings, {_fmt_time(total)} total\n")
    print(f"{'#':>3}  {'title':>5}  {'ch':>3}  {'length':>8}  {'size':>9}  {'cells':>5}  vob ids")
    for r in recordings:
        vobs = ",".join(str(v) for v in r.vob_ids)
        print(
            f"{r.index:>3}  {r.title:>5}  {r.chapter:>3}  {_fmt_time(r.duration_seconds):>8}"
            f"  {_fmt_size(r.size_bytes):>9}  {len(r.cells):>5}  {vobs}"
        )


def _as_json(disc: Disc, recordings: list[Recording]) -> dict:
    return {
        "root": str(disc.root),
        "title_sets": {
            n: {"video": asdict(v.video), "audio": [asdict(a) for a in v.audio]}
            for n, v in disc.title_sets.items()
        },
        "titles": [asdict(t) for t in disc.vmg.titles],
        "recordings": [
            {
                "index": r.index,
                "title": r.title,
                "chapter": r.chapter,
                "vts": r.vts,
                "pgc": r.pgc,
                "duration_seconds": round(r.duration_seconds, 3),
                "size_bytes": r.size_bytes,
                "first_sector": r.first_sector,
                "last_sector": r.last_sector,
                "vob_ids": r.vob_ids,
                "cells": [
                    {
                        "vob_id": c.vob_id,
                        "cell_id": c.cell_id,
                        "duration": str(c.duration),
                        "first_sector": c.first_sector,
                        "last_sector": c.last_sector,
                        "stc_discontinuity": c.stc_discontinuity,
                    }
                    for c in r.cells
                ],
            }
            for r in recordings
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
