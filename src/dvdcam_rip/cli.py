"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .convert import (
    FORMATS,
    SPACE_MARGIN,
    Job,
    Log,
    check_output_location,
    convert_one,
    default_output,
    existing_format,
    free_space,
    open_folder,
    parse_only,
    plan_jobs,
    short_duration,
    space_needed,
    write_report,
)
from .ffmpeg import FfmpegMissing, find_tools, has_encoder
from .ifo import Disc, IfoError, read_disc
from .recordings import SPLIT_MODES, Recording, list_recordings
from .vob import VobError

EXIT_OK, EXIT_FAILED, EXIT_INPUT, EXIT_NO_FFMPEG, EXIT_INTERRUPTED = 0, 1, 2, 3, 130


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dvdcam-rip",
        description=(
            "Copy the recordings on a DVD camcorder disc into separate MP4 files, "
            "without re-encoding (same quality as the disc). The disc files are only read."
        ),
        epilog="Example: dvdcam-rip \"D:\\\"   or   dvdcam-rip \"C:\\Users\\me\\Downloads\\Video Camera\"",
    )
    p.add_argument("source", help="the disc (e.g. D:\\) or a folder with the copied disc files (VIDEO_TS)")
    p.add_argument("-o", "--output", help="output folder (default: Downloads\\dvdcam-rip\\<disc name>)")
    p.add_argument("--list", action="store_true", help="only list the recordings, convert nothing")
    p.add_argument("--json", action="store_true", help="with --list: print the disc structure as JSON")
    p.add_argument("--dry-run", action="store_true", help="show what would be created, then stop")
    p.add_argument("--only", metavar="N", help="convert only these recordings, e.g. 3 or 1,4-6")
    p.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    p.add_argument("--format", choices=FORMATS,
                   help="original = exact MPEG-2 copy; h264 = re-encoded to play everywhere "
                        "(default: ask, or original with --yes)")
    p.add_argument("--overwrite", action="store_true", help="replace files that already exist (default: skip them)")
    p.add_argument("--open", action="store_true", help="open the output folder when finished")
    p.add_argument("--split", choices=SPLIT_MODES, default="chapter",
                   help="what counts as one recording (default: chapter)")
    p.add_argument("--ffmpeg", metavar="PATH", help="path to ffmpeg if it isn't found automatically")
    p.add_argument("-q", "--quiet", action="store_true", help="print less")
    p.add_argument("-v", "--verbose", action="store_true", help="print more, including FFmpeg commands")
    p.add_argument("--version", action="version", version=f"dvdcam-rip {__version__}")
    return p


# ---------------------------------------------------------------- output


class Ui:
    def __init__(self, quiet: bool, verbose: bool):
        self.quiet = quiet
        self.verbose = verbose
        self.tty = sys.stdout.isatty()
        self._last_draw = 0.0

    def say(self, text: str = "") -> None:
        if not self.quiet:
            print(text, flush=True)

    def always(self, text: str = "") -> None:
        print(text, flush=True)

    def error(self, text: str) -> None:
        print(f"Error: {text}", file=sys.stderr, flush=True)

    def detail(self, text: str) -> None:
        if self.verbose:
            print(text, flush=True)

    def progress(self, prefix: str, fraction: float, force: bool = False) -> None:
        if self.quiet or not self.tty:
            return
        now = time.monotonic()
        if not force and now - self._last_draw < 0.1:
            return
        self._last_draw = now
        print(f"\r{prefix}  {fraction * 100:5.1f}%", end="", flush=True)

    def finish_line(self, prefix: str, result: str) -> None:
        if self.quiet:
            return
        end = "\r" if self.tty else ""
        print(f"{end}{prefix}  {result}" + " " * 10, flush=True)


def fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def describe_disc(ui: Ui, disc: Disc) -> None:
    ui.say(f"Disc:    {disc.root}")
    if disc.vmg.provider:
        ui.say(f"Camera:  {disc.vmg.provider}")
    for n, vts in disc.title_sets.items():
        v = vts.video
        label = "Video:  " if len(disc.title_sets) == 1 else f"VTS {n:02d}:"
        ui.say(f"{label} {v.codec} {v.width}x{v.height} {v.standard} {v.aspect}")
        audio = ", ".join(f"{a.codec} {a.channels}ch {a.sample_rate // 1000} kHz" for a in vts.audio)
        ui.say(f"Audio:   {audio or 'none'}")


def print_table(ui: Ui, jobs: list[Job]) -> None:
    ui.always(f"  {'#':>3}  {'Length':>8}  {'Size':>9}  {'Chapter':>7}  File")
    for j in jobs:
        r = j.recording
        ui.always(
            f"  {r.index:>3}  {short_duration(r.duration_seconds):>8}  {fmt_size(r.size_bytes):>9}"
            f"  {f'{r.title}.{r.chapter}':>7}  {j.path.name}"
        )
        if ui.verbose:
            vobs = ",".join(str(v) for v in r.vob_ids)
            ui.always(f"{'':>8}cells {len(r.cells)}, VOB ids {vobs}, sectors {r.first_sector}-{r.last_sector}")


def disc_json(disc: Disc, recordings: list[Recording]) -> dict:
    return {
        "root": str(disc.root),
        "provider": disc.vmg.provider,
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
                        "seamless": c.seamless,
                        "stc_discontinuity": c.stc_discontinuity,
                    }
                    for c in r.cells
                ],
            }
            for r in recordings
        ],
    }


def ask(question: str) -> bool:
    while True:
        answer = input(f"{question} [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Please answer y or n.")


FORMAT_MENU = """
Which format?
  1  Original (MPEG-2)  Exact copy of the disc, done in seconds. Won't play on iPhone/Mac.
  2  H.264              Plays everywhere, iPhone included. Re-encoded (looks the same), takes minutes.
"""


def ask_format() -> str:
    print(FORMAT_MENU, end="")
    while True:
        answer = input("Choose 1 or 2 [1]: ").strip()
        if answer in ("", "1"):
            return "original"
        if answer == "2":
            return "h264"
        print("Please type 1 or 2.")


def print_declined(ui: Ui, source: str) -> None:
    ui.always("\nOK, nothing was converted and no files were written.")
    ui.always("If the list isn't what you expected:")
    ui.always(f'  - see every piece of the disc:  dvdcam-rip "{source}" --list --split cell')
    ui.always(f'  - convert only some of them:    dvdcam-rip "{source}" --only 3,5-7')
    ui.always("  - report a problem:             https://github.com/Gabriel-Mutum/dvdcam-rip/issues")


# ------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ui = Ui(args.quiet, args.verbose)

    # 1. Read the disc
    try:
        disc = read_disc(Path(args.source))
    except (IfoError, OSError) as e:
        ui.error(str(e))
        return EXIT_INPUT
    all_recordings = list_recordings(disc, args.split)
    if not all_recordings:
        ui.error("no recordings found on this disc")
        return EXIT_INPUT

    if args.json:
        print(json.dumps(disc_json(disc, all_recordings), indent=2))
        return EXIT_OK

    recordings = all_recordings
    if args.only:
        try:
            wanted = set(parse_only(args.only, len(all_recordings)))
        except ValueError as e:
            ui.error(str(e))
            return EXIT_INPUT
        recordings = [r for r in all_recordings if r.index in wanted]

    # 2. Show what was found
    ui.say(f"dvdcam-rip {__version__}\n")
    describe_disc(ui, disc)
    total_len = sum(r.duration_seconds for r in recordings)
    total_size = sum(r.size_bytes for r in recordings)
    picked = f" (of {len(all_recordings)})" if args.only else ""
    if not ui.quiet or args.list or not args.yes:  # the table is what the user confirms
        ui.always(f"\nFound {len(recordings)} recordings{picked}, {short_duration(total_len)} in total, "
                  f"{fmt_size(total_size)}:\n")
        print_table(ui, plan_jobs(recordings, len(all_recordings), Path(".")))

    if args.list:
        return EXIT_OK

    try:
        tools = find_tools(args.ffmpeg)
    except FfmpegMissing as e:
        ui.error(str(e))
        return EXIT_NO_FFMPEG
    ui.detail(f"\nUsing FFmpeg {tools.version}: {tools.ffmpeg}")

    # 3. Ask: does the list look right, and which format?
    interactive = not args.yes and not args.dry_run
    if interactive and not sys.stdin.isatty():
        ui.error("not asking for confirmation without a terminal; add --yes to convert")
        return EXIT_INPUT
    fmt = args.format or "original"
    try:
        if interactive:
            n = len(recordings)
            if not ask(f"\nDoes this look right? Convert {n} recording{'s' if n != 1 else ''}?"):
                print_declined(ui, args.source)
                return EXIT_OK
            if not args.format:
                fmt = ask_format()
    except KeyboardInterrupt:
        ui.always("\nCancelled. Nothing was written.")
        return EXIT_INTERRUPTED
    except EOFError:
        ui.always("\nNo answer was given, so nothing was written. Add --yes to convert without asking.")
        return EXIT_INPUT

    if fmt == "h264" and not has_encoder(tools, "libx264"):
        ui.error("this FFmpeg build has no H.264 encoder (libx264); install the full build: "
                 "winget install Gyan.FFmpeg")
        return EXIT_NO_FFMPEG

    # 4. Work out the output and check it
    output = Path(args.output).expanduser() if args.output else default_output(disc, fmt)
    try:
        check_output_location(output, disc)
    except ValueError as e:
        ui.error(str(e))
        return EXIT_INPUT
    jobs = plan_jobs(recordings, len(all_recordings), output)

    clashes = []
    for j in jobs:
        if j.path.exists() and not args.overwrite:
            found = existing_format(tools, j.path)
            if found == fmt:
                j.status = "skipped"
            else:
                clashes.append(j.path.name)
    if clashes:
        ui.error(f"{len(clashes)} file(s) in {output} already exist in a different format "
                 f"(e.g. {clashes[0]}). Choose another folder with -o, or replace them with --overwrite.")
        return EXIT_INPUT

    todo = [j for j in jobs if j.status != "skipped"]
    ui.always(f"\nOutput folder: {output}")
    if len(todo) < len(jobs):
        ui.always(f"{len(jobs) - len(todo)} already converted and will be skipped "
                  "(use --overwrite to redo them).")
    if fmt == "h264":
        ui.say("Format: H.264. The video is re-encoded (deinterlaced to smooth 60 fps, AAC audio).")
    else:
        ui.say("Format: original MPEG-2. The video is copied as-is, the exact quality of the disc.")
    ui.say("The original disc files are only read, never changed.")

    if not todo:
        ui.always("\nNothing to do: every recording is already converted.")
        return EXIT_OK

    needed = space_needed(todo, overwrite=True)
    free = free_space(output)
    if needed + SPACE_MARGIN > free:
        ui.error(f"not enough free space in {output.anchor or output}: "
                 f"need about {fmt_size(needed + SPACE_MARGIN)}, only {fmt_size(free)} free")
        return EXIT_INPUT

    if args.dry_run:
        ui.always("\nDry run: nothing was written.")
        return EXIT_OK

    # 5. Convert
    try:
        output.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        ui.error(f"can't create the output folder {output}: {e}")
        return EXIT_INPUT
    log = Log(output / "dvdcam-rip.log")
    log.write(f"dvdcam-rip {__version__}, FFmpeg {tools.version}, source {disc.root}, "
              f"camera '{disc.vmg.provider}', {len(todo)} to convert")

    ui.say("")
    started = time.monotonic()
    interrupted = False
    width = len(str(len(jobs)))
    try:
        for n, job in enumerate(jobs, start=1):
            prefix = f"[{n:>{width}}/{len(jobs)}] {job.path.name}"
            if job.status == "skipped":
                job.note = "already converted"
                ui.finish_line(prefix, "skipped (already converted)")
                continue
            how = "encoded to H.264" if fmt == "h264" else "copied"
            metadata = {
                "title": job.path.stem,
                "comment": f"{disc.vmg.provider or 'DVD camcorder'}; title {job.recording.title} "
                           f"chapter {job.recording.chapter}; {how} by dvdcam-rip {__version__}",
            }
            ui.progress(prefix, 0.0, force=True)
            try:
                convert_one(
                    job, disc, tools, log,
                    progress=lambda _j, f, p=prefix: ui.progress(p, f),
                    metadata=metadata,
                    verbose_cmd=(lambda c: ui.detail("\n" + subprocess.list2cmdline(c))) if args.verbose else None,
                    fmt=fmt,
                )
            except (VobError, OSError, RuntimeError) as e:
                job.status = "failed"
                job.note = str(e)
                log.write(f"{job.path.name}: FAILED, {e}")
            size = fmt_size(job.path.stat().st_size) if job.path.exists() else ""
            result = {
                "done": f"done  {size}  ({job.seconds:.1f}s)",
                "warning": f"done with a warning: {job.note}",
                "failed": f"FAILED: {job.note}",
            }.get(job.status, job.status)
            ui.finish_line(prefix, result)
            if job.status == "failed" and ui.quiet:
                ui.error(f"{job.path.name}: {job.note}")
    except KeyboardInterrupt:
        interrupted = True
        log.write("stopped by the user (Ctrl+C)")

    # 6. Summary
    elapsed = time.monotonic() - started
    write_report(output / "recordings.csv", jobs)
    log.write("finished" if not interrupted else "stopped")
    log.close()

    count = {s: sum(1 for j in jobs if j.status == s) for s in ("done", "warning", "skipped", "failed")}
    converted = count["done"] + count["warning"]
    ui.always("")
    if interrupted:
        ui.always(f"Stopped. The unfinished file was removed; {converted} finished file"
                  f"{'s are' if converted != 1 else ' is'} kept. Run the same command again to continue.")
    else:
        parts = [f"{converted} converted"]
        if count["skipped"]:
            parts.append(f"{count['skipped']} skipped")
        if count["failed"]:
            parts.append(f"{count['failed']} failed")
        ui.always(f"Finished in {short_duration(elapsed)}: " + ", ".join(parts) + ".")
    if count["warning"]:
        ui.always(f"{count['warning']} file(s) converted with warnings; see recordings.csv.")
    ui.always(f"\nYour videos are in:\n  {output}")
    ui.say("A list of the files is in recordings.csv; technical details are in dvdcam-rip.log.")

    if args.open and converted + count["skipped"]:
        open_folder(output)

    if interrupted:
        return EXIT_INTERRUPTED
    return EXIT_FAILED if count["failed"] else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
