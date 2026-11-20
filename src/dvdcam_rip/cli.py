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
from .media import MediaError
from .removal import detect_install, guide_python, remove_ffmpeg, remove_script, winget_package
from .recordings import SPLIT_MODES
from .source import Media, SourceError, open_source
from .vob import VobError

EXIT_OK, EXIT_FAILED, EXIT_INPUT, EXIT_NO_FFMPEG, EXIT_INTERRUPTED = 0, 1, 2, 3, 130


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dvdcam-rip",
        description=(
            "Copy the recordings on a DVD camcorder disc into separate MP4 files, "
            "without re-encoding (same quality as the disc). The disc files are only read."
        ),
        epilog=(
            "examples:\n"
            '  dvdcam-rip "D:\\"                                    the disc in drive D:\n'
            '  dvdcam-rip "C:\\Users\\me\\Downloads\\Video Camera"     a copied disc folder\n'
            '  dvdcam-rip "C:\\Users\\me\\Downloads\\disc.iso"         an ISO image\n'
            '  dvdcam-rip "D:\\" --format h264 --yes               iPhone-friendly copies, no questions'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("source", help="the disc (e.g. D:\\), a folder with the copied disc files, or an ISO image")
    p.add_argument("-o", "--output", help="output folder (default: Downloads\\dvdcam-rip\\<disc name>)")
    p.add_argument("--list", action="store_true", help="only list the recordings, convert nothing")
    p.add_argument("--json", action="store_true", help="print what's on the disc as JSON, convert nothing")
    p.add_argument("--dry-run", action="store_true", help="show what would be created, then stop")
    p.add_argument("--only", metavar="N", help="convert only these recordings, e.g. 3 or 1,4-6")
    p.add_argument("-y", "--yes", action="store_true",
                   help="don't ask any questions (uses the original format unless --format is given)")
    p.add_argument("--format", choices=FORMATS,
                   help="original = exact MPEG-2 copy; h264 = re-encoded to play everywhere "
                        "(default: ask, or original with --yes)")
    p.add_argument("--overwrite", action="store_true", help="replace files that already exist (default: skip them)")
    p.add_argument("--open", action="store_true", help="open the output folder when finished")
    p.add_argument("--split", choices=SPLIT_MODES, default="chapter",
                   help="finalized DVD-Video discs: what counts as one recording (default: chapter)")
    p.add_argument("--scan", action="store_true",
                   help="ignore the disc map and find recordings by scanning the video "
                        "(automatic for unfinalized discs)")
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


def describe_media(ui: Ui, media: Media) -> None:
    ui.say(f"Source:  {media.location}")
    ui.say(f"Type:    {media.kind}")
    if media.camera:
        ui.say(f"Camera:  {media.camera}")
    ui.say(f"Video:   {media.video}")
    ui.say(f"Audio:   {media.audio_detail or ', '.join(media.audio) or 'none'}")
    for note in media.notes:
        ui.say(f"Note:    {note}")


def print_table(ui: Ui, jobs: list[Job], dated: bool) -> None:
    head = f"  {'#':>3}  {'Length':>8}  {'Size':>9}  "
    if dated:
        head += f"{'Recorded':<16}  "
    ui.always(head + f"{'Source':<9}  File")
    for j in jobs:
        r = j.recording
        length = short_duration(r.duration_seconds) if r.duration_seconds else "?"
        line = f"  {r.index:>3}  {length:>8}  {fmt_size(r.size_bytes):>9}  "
        if dated:
            line += f"{(f'{r.date:%Y-%m-%d %H:%M}' if r.date else '-'):<16}  "
        ui.always(line + f"{r.origin:<9}  {j.path.name}")
        if ui.verbose:
            vobs = ",".join(str(v) for v in r.vob_ids)
            ui.always(f"{'':>8}cells {len(r.cells)}, VOB ids {vobs}, sectors {r.first_sector}-{r.last_sector}")


def media_json(media: Media) -> dict:
    out: dict = {
        "source": str(media.location),
        "type": media.kind,
        "camera": media.camera,
        "video": media.video,
        "audio": media.audio,
        "notes": media.notes,
        "recordings": [
            {
                "index": r.index,
                "source": r.origin,
                "recorded": r.date.isoformat() if r.date else None,
                "duration_seconds": round(r.duration_seconds, 3) or None,
                "size_bytes": r.size_bytes,
                "first_sector": r.first_sector,
                "last_sector": r.last_sector,
                "cells": [
                    {
                        "vob_id": c.vob_id,
                        "cell_id": c.cell_id,
                        "duration": str(c.duration),
                        "first_sector": c.first_sector,
                        "last_sector": c.last_sector,
                    }
                    for c in r.cells
                ],
            }
            for r in media.recordings
        ],
    }
    if media.disc:
        d = media.disc
        out["dvd_video"] = {
            "title_sets": {
                n: {"video": asdict(v.video), "audio": [asdict(a) for a in v.audio]}
                for n, v in d.title_sets.items()
            },
            "titles": [asdict(t) for t in d.vmg.titles],
        }
    return out


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
    ui.always(f'  - find them from the video:     dvdcam-rip "{source}" --scan')
    ui.always(f'  - convert only some of them:    dvdcam-rip "{source}" --only 3,5-7')
    ui.always("  - report a problem:             https://github.com/Gabriel-Mutum/dvdcam-rip/issues")


# ------------------------------------------------------------------ main


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ui = Ui(args.quiet, args.verbose)
    menu = sys.stdin.isatty() and not (args.yes or args.list or args.json or args.dry_run)
    source = args.source
    while True:
        code, output = run_disc(args, ui, source)
        if not menu or code == EXIT_INTERRUPTED:
            return code
        try:
            action = end_menu(ui, output)
        except (KeyboardInterrupt, EOFError):
            return code
        if action == "exit":
            return code
        if action == "removed":
            return EXIT_OK
        # "another": a new disc, with the per-disc options reset
        source = action
        args.only = None
        args.output = None


def end_menu(ui: Ui, output: Path | None) -> str:
    """Ask what to do next. Returns "exit", "removed", or the next disc's path."""
    while True:
        options = [("another", "Convert another disc")]
        if output is not None and output.is_dir():
            options.append(("open", "Open the videos folder"))
        options.append(("remove", "Remove dvdcam-rip from this PC..."))
        options.append(("exit", "Exit"))
        print("\nWhat next?")
        for i, (_, label) in enumerate(options, start=1):
            print(f"  {i}  {label}")
        choice = pick(len(options))
        key = options[choice - 1][0]
        if key == "exit":
            return "exit"
        if key == "open":
            open_folder(output)
            print(f"Opened {output}")
        elif key == "another":
            nxt = ask_source()
            if nxt:
                print()
                return nxt
        elif key == "remove":
            if removal_menu(ui, output):
                return "removed"


def pick(count: int, default: int | None = None) -> int:
    hint = f" [{default}]" if default else ""
    while True:
        answer = input(f"Choose 1-{count}{hint}: ").strip()
        if not answer and default:
            return default
        if answer.isdigit() and 1 <= int(answer) <= count:
            return int(answer)
        print(f"Please type a number from 1 to {count}.")


def ask_source() -> str | None:
    print("\nDrag the next disc folder, DVD drive or ISO file into this window and press Enter")
    print("(or type its path, e.g. D:\\). Press Enter on its own to go back.")
    answer = input("> ").strip().strip('"').strip("'").strip()
    return answer or None


REMOVE_MENU = """
Remove what?
  1  dvdcam-rip only
  2  dvdcam-rip and FFmpeg
  3  dvdcam-rip, FFmpeg and Python (Python with your help, in Windows Settings)
  4  Cancel
"""


def removal_menu(ui: Ui, output: Path | None) -> bool:
    """Returns True if something was removed (dvdcam-rip should then close)."""
    print(REMOVE_MENU, end="")
    level = pick(4)
    if level == 4:
        print("Cancelled. Nothing was removed.")
        return False

    install = detect_install()
    try:
        ffmpeg: Path | None = find_tools(None).ffmpeg
    except FfmpegMissing:
        ffmpeg = None

    package = winget_package(ffmpeg) if level >= 2 else None
    will: list[str] = []
    kept: list[str] = []
    if install.pip:
        will.append("the dvdcam-rip package (pip uninstall dvdcam-rip)")
    if install.folder is not None:
        if install.folder_deletable:
            extra = " (a git clone with no changes of your own)" if install.folder_is_git else ""
            will.append(f"the dvdcam-rip folder{extra}: {install.folder}")
        else:
            kept.append(f"the dvdcam-rip folder {install.folder}, because {install.keep_reason()}")
    if not install.found:
        kept.append("dvdcam-rip itself: couldn't tell where it's installed")
    if level >= 2:
        if ffmpeg is None:
            kept.append("FFmpeg: not found on this PC")
        elif package:
            will.append(f"FFmpeg (winget package {package})")
        else:
            kept.append(f"FFmpeg at {ffmpeg}: it wasn't installed with winget")
    if level == 3:
        will.append("Python: Windows Settings will open so you can uninstall it yourself")

    if will:
        print("\nThis will remove:")
        for line in will:
            print(f"  - {line}")
    if kept:
        print("\nThis will be kept:")
        for line in kept:
            print(f"  - {line}")
    if not will:
        print("\nThere's nothing dvdcam-rip can remove automatically. You can delete what's listed")
        print("above yourself (see \"Removing dvdcam-rip\" in the README).")
        return False
    videos = output
    if output is not None and output.parent.name == "dvdcam-rip":
        videos = output.parent  # the default place, holding every disc's folder
    print("\nYour converted videos" + (f" in {videos}" if videos else "") + " and your disc files are NOT touched.")

    if not ask_no("Remove these?"):
        print("Cancelled. Nothing was removed.")
        return False

    print("\nRemoving...")
    steps = remove_script(install) if install.found else []
    if level >= 2:
        steps.append(remove_ffmpeg(ffmpeg))
    if level == 3:
        steps.append(guide_python())
    for step in steps:
        print(f"  [{step.status}]  {step.text}")

    failed = [s for s in steps if s.status == "failed"]
    if failed:
        print("\nSome things couldn't be removed; see above.")
    else:
        print("\ndvdcam-rip has been removed from this PC." if any(
            s.status == "done" for s in steps) else "\nNothing needed removing.")
    if videos:
        print(f"Your videos are still in {videos}")
    try:
        input("\nPress Enter to close this window.")
    except (EOFError, KeyboardInterrupt):
        pass
    return True


def ask_no(question: str) -> bool:
    """A yes/no question where Enter means no."""
    while True:
        answer = input(f"\n{question} [y/N] ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("", "n", "no"):
            return False
        print("Please answer y or n.")


def run_disc(args, ui: Ui, source: str) -> tuple[int, Path | None]:

    output: Path | None = None

    # 1. Read the disc
    def scan_progress(fraction: float) -> None:
        ui.progress("Scanning the video for recordings...", fraction)

    try:
        media = open_source(source, args.split, args.scan, progress=scan_progress)
    except (SourceError, MediaError, VobError, OSError) as e:
        ui.error(str(e))
        return EXIT_INPUT, output
    if ui.tty and not ui.quiet and "scanned" in media.kind:
        print("\r" + " " * 60 + "\r", end="")
    all_recordings = media.recordings
    if not all_recordings:
        ui.error("no recordings found on this disc")
        return EXIT_INPUT, output

    if args.json:
        print(json.dumps(media_json(media), indent=2))
        return EXIT_OK, output

    recordings = all_recordings
    if args.only:
        try:
            wanted = set(parse_only(args.only, len(all_recordings)))
        except ValueError as e:
            ui.error(str(e))
            return EXIT_INPUT, output
        recordings = [r for r in all_recordings if r.index in wanted]

    # 2. Show what was found
    ui.say(f"dvdcam-rip {__version__}\n")
    describe_media(ui, media)
    total_len = sum(r.duration_seconds for r in recordings)
    total_size = sum(r.size_bytes for r in recordings)
    picked = f" (of {len(all_recordings)})" if args.only else ""
    if not ui.quiet or args.list or not args.yes:  # the table is what the user confirms
        ui.always(f"\nFound {len(recordings)} recordings{picked}, {short_duration(total_len)} in total, "
                  f"{fmt_size(total_size)}:\n")
        print_table(ui, plan_jobs(recordings, len(all_recordings), Path(".")), media.has_dates)

    if args.list:
        return EXIT_OK, output

    try:
        tools = find_tools(args.ffmpeg)
    except FfmpegMissing as e:
        ui.error(str(e))
        return EXIT_NO_FFMPEG, output
    ui.detail(f"\nUsing FFmpeg {tools.version}: {tools.ffmpeg}")

    # 3. Ask: does the list look right, and which format?
    interactive = not args.yes and not args.dry_run
    if interactive and not sys.stdin.isatty():
        ui.error("not asking for confirmation without a terminal; add --yes to convert")
        return EXIT_INPUT, output
    fmt = args.format or "original"
    try:
        if interactive:
            n = len(recordings)
            if not ask(f"\nDoes this look right? Convert {n} recording{'s' if n != 1 else ''}?"):
                print_declined(ui, source)
                return EXIT_OK, output
            if not args.format:
                fmt = ask_format()
    except KeyboardInterrupt:
        ui.always("\nCancelled. Nothing was written.")
        return EXIT_INTERRUPTED, output
    except EOFError:
        ui.always("\nNo answer was given, so nothing was written. Add --yes to convert without asking.")
        return EXIT_INPUT, output

    if fmt == "h264" and not has_encoder(tools, "libx264"):
        ui.error("this FFmpeg build has no H.264 encoder (libx264); install the full build: "
                 "winget install Gyan.FFmpeg")
        return EXIT_NO_FFMPEG, output

    # 4. Work out the output and check it
    output = Path(args.output).expanduser() if args.output else default_output(media, fmt)
    try:
        check_output_location(output, media)
    except ValueError as e:
        ui.error(str(e))
        return EXIT_INPUT, output
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
        return EXIT_INPUT, output

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
        return EXIT_OK, output

    needed = space_needed(todo, overwrite=True)
    free = free_space(output)
    if needed + SPACE_MARGIN > free:
        ui.error(f"not enough free space in {output.anchor or output}: "
                 f"need about {fmt_size(needed + SPACE_MARGIN)}, only {fmt_size(free)} free")
        return EXIT_INPUT, output

    if args.dry_run:
        ui.always("\nDry run: nothing was written.")
        return EXIT_OK, output

    # 5. Convert
    try:
        output.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        ui.error(f"can't create the output folder {output}: {e}")
        return EXIT_INPUT, output
    log = Log(output / "dvdcam-rip.log")
    log.write(f"dvdcam-rip {__version__}, FFmpeg {tools.version}, source {media.location}, "
              f"{media.kind}, camera '{media.camera}', {len(todo)} to convert")

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
                "comment": f"{media.camera or 'DVD camcorder'}; {job.recording.origin}; "
                           f"{how} by dvdcam-rip {__version__}",
            }
            if job.recording.date:
                metadata["creation_time"] = job.recording.date.isoformat()
            ui.progress(prefix, 0.0, force=True)
            try:
                convert_one(
                    job, media, tools, log,
                    progress=lambda _j, f, p=prefix: ui.progress(p, f),
                    metadata=metadata,
                    verbose_cmd=(lambda c: ui.detail("\n" + subprocess.list2cmdline(c))) if args.verbose else None,
                    fmt=fmt,
                )
            except (VobError, MediaError, OSError, RuntimeError) as e:
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
        return EXIT_INTERRUPTED, output
    return (EXIT_FAILED if count["failed"] else EXIT_OK), output


if __name__ == "__main__":
    raise SystemExit(main())
