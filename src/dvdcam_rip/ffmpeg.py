"""Find FFmpeg and run it."""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

INSTALL_HINT = (
    "FFmpeg is needed but wasn't found.\n"
    "  Windows: winget install Gyan.FFmpeg   (then open a new terminal)\n"
    "  macOS:   brew install ffmpeg\n"
    "  Linux:   sudo apt install ffmpeg"
)


class FfmpegMissing(RuntimeError):
    pass


@dataclass(frozen=True)
class Tools:
    ffmpeg: Path
    ffprobe: Path
    version: str


def _candidates(name: str) -> list[str]:
    exe = name + (".exe" if os.name == "nt" else "")
    found = [shutil.which(name)]
    if os.name == "nt":
        # winget installs aren't on PATH until a new terminal is opened
        local = os.environ.get("LOCALAPPDATA", "")
        found.append(os.path.join(local, "Microsoft", "WinGet", "Links", exe))
        found += sorted(
            glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages", "*FFmpeg*", "*", "bin", exe)),
            reverse=True,
        )
        found += [os.path.join(p, "ffmpeg", "bin", exe) for p in ("C:\\", os.environ.get("ProgramFiles", ""))]
    return [f for f in found if f and os.path.isfile(f)]


def find_tools(explicit: str | None = None) -> Tools:
    if explicit:
        ffmpeg = Path(explicit)
        probe = ffmpeg.with_name(ffmpeg.name.replace("ffmpeg", "ffprobe"))
        if not ffmpeg.is_file() or not probe.is_file():
            raise FfmpegMissing(f"--ffmpeg {explicit}: ffmpeg or ffprobe not found there")
        pairs = [(str(ffmpeg), str(probe))]
    else:
        pairs = []
        for f in _candidates("ffmpeg"):
            probe = os.path.join(os.path.dirname(f), os.path.basename(f).replace("ffmpeg", "ffprobe"))
            if os.path.isfile(probe):
                pairs.append((f, probe))
    for ffmpeg, probe in pairs:
        try:
            out = subprocess.run(
                [ffmpeg, "-hide_banner", "-version"], capture_output=True, text=True, timeout=20
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        m = re.search(r"ffmpeg version (\S+)", out)
        return Tools(Path(ffmpeg), Path(probe), m.group(1) if m else "unknown")
    raise FfmpegMissing(INSTALL_HINT)


def remux_command(tools: Tools, out: Path, audio_codecs: list[str], metadata: dict[str, str]) -> list[str]:
    """FFmpeg command that reads an MPEG program stream on stdin and copies it
    into an MP4 without re-encoding. LPCM audio can't go in MP4 as-is, so it
    is stored as FLAC, which is also lossless."""
    cmd = [
        str(tools.ffmpeg), "-hide_banner", "-nostats", "-loglevel", "warning",
        "-probesize", "50M", "-analyzeduration", "20M",
        "-f", "mpeg", "-i", "pipe:0",
        "-map", "0:v:0", "-map", "0:a?",
        "-c", "copy",
    ]
    for i, codec in enumerate(audio_codecs):
        if codec == "LPCM":
            cmd += [f"-c:a:{i}", "flac"]
    for key, value in metadata.items():
        cmd += ["-metadata", f"{key}={value}"]
    cmd += ["-movflags", "+faststart", "-f", "mp4", "-y", str(out)]
    return cmd


@dataclass(frozen=True)
class Probe:
    duration: float
    video_frames: int
    audio_streams: int


def probe(tools: Tools, path: Path) -> Probe:
    out = subprocess.run(
        [
            str(tools.ffprobe), "-v", "error", "-count_packets",
            "-show_entries", "format=duration:stream=codec_type,nb_read_packets",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, timeout=600,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "ffprobe failed")
    data = json.loads(out.stdout)
    streams = data.get("streams", [])
    frames = sum(int(s.get("nb_read_packets", 0)) for s in streams if s.get("codec_type") == "video")
    audio = sum(1 for s in streams if s.get("codec_type") == "audio")
    return Probe(float(data.get("format", {}).get("duration", 0) or 0), frames, audio)
