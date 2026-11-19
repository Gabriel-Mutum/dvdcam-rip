"""Plan and run the conversion of recordings to MP4 files.

Safety rules, enforced here:
- The source is only ever opened for reading.
- Nothing is written inside the disc folder.
- An existing output file is never overwritten unless asked (--overwrite);
  by default it counts as already converted and is skipped, so an
  interrupted run can simply be started again.
- Each file is written as "<name>.part" and renamed only once FFmpeg has
  finished and the result has been checked, so a crash or Ctrl+C never
  leaves a half-written file that looks complete.
"""

from __future__ import annotations

import csv
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .ffmpeg import Tools, encode_command, probe, remux_command
from .recordings import Recording
from .source import Media
from .vob import cell_timing, plain_stream, recording_duration, stitched_stream

SPACE_MARGIN = 100 * 1024 * 1024  # keep 100 MB free beyond the estimate


# ------------------------------------------------------------ planning


def downloads_folder() -> Path:
    """The user's Downloads folder (honours a moved Downloads on Windows)."""
    if os.name == "nt":
        try:
            import winreg

            key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                value, _ = winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")
            path = Path(os.path.expandvars(value))
            if path.is_dir():
                return path
        except OSError:
            pass
    path = Path.home() / "Downloads"
    return path if path.is_dir() else Path.home()


# Output formats: "original" copies the camera's MPEG-2 untouched; "h264"
# re-encodes for devices that can't play MPEG-2.
FORMATS = ("original", "h264")
VIDEO_CODEC = {"original": "mpeg2video", "h264": "h264"}


def default_output(media: Media, fmt: str = "original") -> Path:
    name = media.name + (" (H.264)" if fmt == "h264" else "")
    return downloads_folder() / "dvdcam-rip" / name


def existing_format(tools: Tools, path: Path) -> str | None:
    """Which format an existing output file is in ("original"/"h264"), or None."""
    try:
        codec = probe(tools, path, count=False).video_codec
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return next((f for f, c in VIDEO_CODEC.items() if c == codec), None)


def check_output_location(output: Path, media: Media) -> None:
    if media.protect is None:
        return
    out = output.resolve()
    root = media.protect.resolve()
    if out == root or root in out.parents:
        raise ValueError(
            f"the output folder {output} is inside the disc folder {media.protect}; "
            "choose a folder outside it so the original files stay untouched"
        )


def short_duration(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s" if m else f"{s}s"


def file_name(rec: Recording, count: int) -> str:
    """"2007-08-14 15-32-10 (1m59s).mp4" when the disc has the recording date,
    otherwise "Recording 03 (1m59s).mp4". The length is left out if unknown."""
    length = f" ({short_duration(rec.duration_seconds)})" if rec.duration_seconds else ""
    if rec.date:
        return f"{rec.date:%Y-%m-%d %H-%M-%S}{length}.mp4"
    width = max(2, len(str(count)))
    return f"Recording {rec.index:0{width}d}{length}.mp4"


def parse_only(spec: str, count: int) -> list[int]:
    """Parse "3,5-7" into sorted recording numbers, checked against count."""
    chosen: set[int] = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ValueError(f"--only: can't read '{part}' (use e.g. 3,5-7)")
        a = int(m.group(1))
        b = int(m.group(2) or a)
        if a > b:
            a, b = b, a
        if a < 1 or b > count:
            raise ValueError(f"--only: {part} is out of range; this disc has recordings 1-{count}")
        chosen.update(range(a, b + 1))
    if not chosen:
        raise ValueError("--only: no recordings given")
    return sorted(chosen)


@dataclass
class Job:
    recording: Recording
    path: Path
    status: str = "pending"  # pending, done, skipped, failed, warning
    note: str = ""
    seconds: float = 0.0
    duration: float = 0.0  # measured from the output file

    @property
    def part(self) -> Path:
        return self.path.with_name(self.path.name + ".part")


def plan_jobs(recordings: list[Recording], total: int, output: Path) -> list[Job]:
    jobs, used = [], set()
    for r in recordings:
        name = file_name(r, total)
        stem, n = name[:-4], 2
        while name.lower() in used:  # two recordings in the same second
            name = f"{stem} #{n}.mp4"
            n += 1
        used.add(name.lower())
        jobs.append(Job(r, output / name))
    return jobs


def space_needed(jobs: list[Job], overwrite: bool) -> int:
    return sum(j.recording.size_bytes for j in jobs if overwrite or not j.path.exists())


def free_space(folder: Path) -> int:
    probe_dir = folder
    while not probe_dir.exists():
        probe_dir = probe_dir.parent
    return shutil.disk_usage(probe_dir).free


# ------------------------------------------------------------- running


class Log:
    """Appends to dvdcam-rip.log in the output folder."""

    def __init__(self, path: Path):
        self.path = path
        self.file = open(path, "a", encoding="utf-8")

    def write(self, message: str) -> None:
        self.file.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}\n")
        self.file.flush()

    def close(self) -> None:
        self.file.close()


Progress = Callable[[Job, float], None]


def convert_one(
    job: Job,
    media: Media,
    tools: Tools,
    log: Log,
    progress: Progress,
    metadata: dict[str, str],
    verbose_cmd: Callable[[list[str]], None] | None = None,
    fmt: str = "original",
) -> None:
    rec = job.recording
    started = time.monotonic()
    reader = rec.reader
    if reader is None:
        raise RuntimeError("no data source for this recording")
    if rec.stitch:
        timings = [cell_timing(reader, c) for c in rec.cells]
        expected: float | None = recording_duration(timings)
        stream = stitched_stream(reader, timings)
    else:
        expected = rec.duration_seconds or None
        stream = plain_stream(reader, rec.cells)

    if job.part.exists():
        job.part.unlink()  # our own leftover from an interrupted run
    if fmt == "h264":
        cmd = encode_command(tools, job.part, metadata)
    else:
        cmd = remux_command(tools, job.part, media.audio, metadata)
    exp = f"{expected:.3f}s" if expected else "unknown"
    log.write(f"{job.path.name}: {rec.origin}, {len(rec.cells)} cells, "
              f"sectors {rec.first_sector}-{rec.last_sector}, expected length {exp}")
    log.write("command: " + subprocess.list2cmdline(cmd))
    if verbose_cmd:
        verbose_cmd(cmd)

    total = rec.size_bytes
    written = 0
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            stderr=log.file, creationflags=flags)
    try:
        assert proc.stdin is not None
        try:
            for chunk in stream:
                proc.stdin.write(chunk)
                written += len(chunk)
                progress(job, written / total)
            proc.stdin.close()
        except BrokenPipeError:
            pass  # FFmpeg stopped early; its exit code says why
        code = proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        if job.part.exists():
            job.part.unlink()
        raise

    job.seconds = time.monotonic() - started
    if code != 0 or not job.part.exists():
        if job.part.exists():
            job.part.unlink()
        job.status = "failed"
        job.note = f"FFmpeg exited with code {code} (details in {log.path.name})"
        log.write(f"{job.path.name}: FAILED, ffmpeg exit code {code}")
        return

    # Check the result before giving it its final name.
    result = probe(tools, job.part)
    job.duration = result.duration
    problems = []
    if result.video_frames == 0:
        problems.append("no video in the output")
    elif result.video_codec != VIDEO_CODEC[fmt]:
        problems.append(f"video is {result.video_codec}, expected {VIDEO_CODEC[fmt]}")
    if media.audio and result.audio_streams == 0:
        problems.append("no audio in the output")
    if expected:
        tolerance = max(0.5, expected * 0.01)
        if abs(result.duration - expected) > tolerance:
            problems.append(f"length {result.duration:.1f}s, expected {expected:.1f}s")

    os.replace(job.part, job.path)
    if rec.date:  # file date = recording date
        stamp = rec.date.timestamp()
        os.utime(job.path, (stamp, stamp))
    if problems:
        job.status = "warning"
        job.note = "; ".join(problems)
    else:
        job.status = "done"
    log.write(f"{job.path.name}: {job.status}, {result.duration:.3f}s, {result.video_frames} frames"
              + (f" ({job.note})" if job.note else ""))


def write_report(path: Path, jobs: list[Job]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "recording", "recorded", "source", "length", "size_mb", "status", "note"])
        for j in jobs:
            r = j.recording
            w.writerow([
                j.path.name, r.index, f"{r.date:%Y-%m-%d %H:%M:%S}" if r.date else "", r.origin,
                short_duration(j.duration or r.duration_seconds),
                f"{r.size_bytes / 1048576:.1f}", j.status, j.note,
            ])


def open_folder(path: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass
