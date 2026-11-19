"""Open whatever the user points at and find the recordings on it.

Accepted inputs:
- a folder with a copied disc (VIDEO_TS, DVD_RTAV, or the files loose);
- the disc itself (a drive like D:\\);
- an ISO image of a disc;
- a single .IFO/.VOB/.VRO file (its folder is used).

Kinds of disc:
- finalized DVD-Video: recordings come from the IFO disc map;
- DVD-VR (DVD-RAM / DVD-RW in VR mode): recordings and their dates come
  from VR_MANGR.IFO;
- no usable disc map (unfinalized disc, lost or damaged IFOs): the video is
  scanned for its navigation packs instead (see scan.py).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .dvdvr import VrError, find_vr_files, read_vr
from .ifo import Disc, IfoError, read_video_ts
from .media import (
    FileRef,
    FolderVolume,
    MediaError,
    SpanReader,
    Volume,
    open_image,
    raw_device,
    whole_file,
)
from .recordings import Recording, SplitMode, list_recordings
from .scan import scan

IMAGE_SUFFIXES = {".iso", ".img", ".udf", ".bin"}


class SourceError(ValueError):
    pass


@dataclass
class Media:
    kind: str  # shown to the user
    location: Path  # what was opened
    name: str  # used for the output folder
    camera: str
    video: str
    audio: list[str]
    recordings: list[Recording]
    protect: Path | None  # never write inside this folder
    notes: list[str] = field(default_factory=list)
    disc: Disc | None = None  # finalized DVD-Video only
    audio_detail: str = ""  # e.g. "AC-3 2ch 48 kHz", when known

    @property
    def has_dates(self) -> bool:
        return any(r.date for r in self.recordings)


def safe_name(text: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")
    return text or "DVD"


def _volume_label(drive: str) -> str:
    if os.name != "nt":
        return ""
    try:
        import ctypes

        buf = ctypes.create_unicode_buffer(261)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(  # type: ignore[attr-defined]
            ctypes.c_wchar_p(drive), buf, 261, None, None, None, None, 0
        )
        return buf.value.strip() if ok else ""
    except (OSError, AttributeError):
        return ""


def _folder_name(folder: Path) -> str:
    if folder.name.upper() in ("VIDEO_TS", "DVD_RTAV"):
        folder = folder.parent
    if folder.name:
        return safe_name(folder.name)
    label = _volume_label(folder.anchor)
    return safe_name(label) if label else "DVD " + datetime.now().strftime("%Y-%m-%d")


def open_source(path: str | Path, split: SplitMode = "chapter", force_scan: bool = False,
                progress=None) -> Media:
    p = Path(path)
    if p.is_file():
        if p.suffix.lower() in IMAGE_SUFFIXES:
            return _open_image(p, split, force_scan, progress)
        p = p.parent  # a file inside a disc folder
    if not p.exists():
        raise SourceError(f"{p} doesn't exist")

    vol = FolderVolume(p)
    name = _folder_name(p)
    try:
        return _open_volume(vol, p, name, protect=p, split=split, force_scan=force_scan,
                            progress=progress, loose=True)
    except _NothingFound:
        pass

    device = raw_device(str(path))
    if device is not None:
        # A drive with no readable files: most likely an unfinalized disc.
        try:
            reader = whole_file(device)
        except OSError as e:
            raise SourceError(
                f"{p} has no readable files and the disc can't be read directly ({e}). "
                "If it's an unfinalized disc, finalize it in the camera, or make an ISO image "
                "of it with a disc imaging tool and pass the image instead."
            ) from e
        return _scanned(reader, device, name, None, progress,
                        "the drive has no readable files, so the disc was scanned directly")
    raise SourceError(
        f"no DVD video found in {p}: expected VIDEO_TS (IFO/VOB files) or DVD_RTAV "
        "(VR_MANGR.IFO / VR_MOVIE.VRO)"
    )


class _NothingFound(Exception):
    pass


def _open_volume(vol: Volume, location: Path, name: str, protect: Path | None,
                 split: SplitMode, force_scan: bool, progress, loose: bool = False) -> Media:
    rtav = vol.directory("DVD_RTAV")
    video_ts = vol.directory("VIDEO_TS")
    if loose and isinstance(vol, FolderVolume):
        files = vol.loose_files()
        if video_ts is None and ("VIDEO_TS.IFO" in files or "VIDEO_TS.BUP" in files or _vobs(files)):
            video_ts = files
        if rtav is None and find_vr_files(files):
            rtav = files

    if rtav is not None and find_vr_files(rtav):
        try:
            vr = read_vr(rtav)
        except VrError as e:
            raise SourceError(str(e)) from e
        fmt = vr.formats[0] if vr.formats else None
        notes = list(vr.notes)
        if not any(r.date for r in vr.recordings):
            notes.append("this disc doesn't store recording dates")
        return Media("DVD-VR", location, name, vr.info, fmt.video if fmt else "unknown video",
                     fmt.audio if fmt else [], vr.recordings, protect, notes)

    if video_ts is None:
        raise _NothingFound

    vobs = _vobs(video_ts)
    if not force_scan:
        try:
            disc = read_video_ts(video_ts)
        except IfoError as e:
            if not vobs:
                raise SourceError(f"{e}, and there are no VOB files to scan") from e
            if not any(n in video_ts for n in ("VIDEO_TS.IFO", "VIDEO_TS.BUP")):
                reason = ("there's no disc map (IFO files), which is normal for an unfinalized disc, "
                          "so the video was scanned")
            else:
                reason = f"the disc map is damaged ({e}), so the video was scanned instead"
        else:
            readers = {n: SpanReader.of_files(_title_vobs(video_ts, n)) for n in disc.title_sets}
            missing = [n for n, r in readers.items() if r.total_sectors == 0]
            if missing:
                raise SourceError(f"VTS_{missing[0]:02d}_1.VOB is missing; the disc copy is incomplete")
            recordings = list_recordings(disc, split, readers)
            vts = next(iter(disc.title_sets.values()))
            v = vts.video
            notes = ["no recording dates on this disc (DVD-Video has no standard place for them)"]
            detail = ", ".join(f"{a.codec} {a.channels}ch {a.sample_rate // 1000} kHz" for a in vts.audio)
            return Media(
                "DVD-Video", location, name, disc.vmg.provider,
                f"{v.codec} {v.width}x{v.height} {v.standard} {v.aspect}",
                [a.codec for a in vts.audio], recordings, protect, notes, disc, detail,
            )
    else:
        reason = "scanned on request (--scan)"
    if not vobs:
        raise SourceError("there are no VOB files to scan")
    return _scanned(SpanReader.of_files(vobs), location, name, protect, progress, reason)


def _vobs(files: dict[str, FileRef]) -> list[FileRef]:
    """Title VOBs (VTS_xx_1..9.VOB) in disc order, menus left out."""
    names = sorted(n for n in files if re.fullmatch(r"VTS_\d\d_[1-9]\.VOB", n))
    if not names:  # recovered files with other names
        names = sorted(n for n in files if n.endswith(".VOB") and n != "VIDEO_TS.VOB"
                       and not re.fullmatch(r"VTS_\d\d_0\.VOB", n))
    return [files[n] for n in names]


def _title_vobs(files: dict[str, FileRef], vts: int) -> list[FileRef]:
    out = []
    for k in range(1, 10):
        ref = files.get(f"VTS_{vts:02d}_{k}.VOB")
        if ref is None:
            break
        out.append(ref)
    return out


def _scanned(reader: SpanReader, location: Path, name: str, protect: Path | None, progress,
             reason: str) -> Media:
    result = scan(reader, progress)
    if not result.recordings:
        raise SourceError("no recordings found: " + "; ".join(result.notes or ["no DVD video in the data"]))
    notes = [reason, *result.notes,
             "without a disc map, recordings are found from the video's timecode; check the list"]
    return Media("DVD-Video, no disc map (scanned)", location, name, "", result.video, result.audio,
                 result.recordings, protect, notes)


def _open_image(path: Path, split: SplitMode, force_scan: bool, progress) -> Media:
    name = safe_name(path.stem)
    if not force_scan:
        try:
            vol = open_image(path)
        except MediaError:
            vol = None
        if vol is not None:
            try:
                return _open_volume(vol, path, name, protect=None, split=split,
                                    force_scan=False, progress=progress)
            except _NothingFound:
                pass
            finally:
                vol.close()  # type: ignore[attr-defined]
    reason = ("scanned on request (--scan)" if force_scan else
              "the image has no readable VIDEO_TS or DVD_RTAV folder (unfinalized disc?), so it was scanned")
    return _scanned(whole_file(path), path, name, None, progress, reason)
