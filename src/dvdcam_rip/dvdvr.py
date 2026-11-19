"""DVD-VR discs (DVD-RAM / DVD-RW in VR mode): DVD_RTAV/VR_MANGR.IFO + VR_MOVIE.VRO.

VR_MANGR.IFO lists every recording ("program") with its recording date and
time, its video/audio format and a VOBU map: the program's first sector in
VR_MOVIE.VRO followed by the size of each VOBU. A program is one continuous
MPEG program stream, so it is copied as-is (no timestamp stitching needed).

Layout as implemented by Pádraig Brady's GPL tool dvd-vr (offsets in bytes,
big-endian); this is an independent implementation from that description:

  IFO   0  "DVD_RTR_VMG0"           67  text encoding
       98  disc info text (64)     256  PGIT offset (u32)
      304  default program set info offset (u32)
  PGIT  +2 number of PGI tables (u8)  +3 number of VOB formats (u8)
        +8 VOB formats, 60 bytes each: video attr u16, audio count u8, -,
           audio attr 3 bytes, ...
        then number of programs (u16), then one u32 per program: the
        program's record offset from the PGIT.
  program record (21 bytes): attr u16, timestamp 5 bytes, -, format id u8,
        start PTM (u32 + u16), end PTM (u32 + u16); then 12 bytes more if
        attr & 0x80, then 2 bytes, then the VOBU map: time entries u16,
        VOBU entries u16, time offset u16, first sector u32; time entries
        (7 bytes each); VOBU entries (3 bytes each, size = low 10 bits of
        bytes 1-2, in sectors).
  timestamp: 40 bits = year 14, month 4, day 5, hour 5, minute 6, second 6.

The recording date is local camera time with no time zone.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime

from .ifo import Cell, Duration
from .media import SECTOR, FileRef, SpanReader
from .recordings import Recording

MAGIC = b"DVD_RTR_VMG0"
CHARSETS = {0x00: "ascii", 0x11: "latin-1", 0x12: "shift_jis"}


class VrError(ValueError):
    pass


def _u8(b: bytes, o: int) -> int:
    return b[o]


def _u16(b: bytes, o: int) -> int:
    return struct.unpack_from(">H", b, o)[0]


def _u32(b: bytes, o: int) -> int:
    return struct.unpack_from(">I", b, o)[0]


def parse_timestamp(b: bytes) -> datetime | None:
    if len(b) < 5:
        return None
    year = ((b[0] << 8) | b[1]) >> 2
    month = ((b[1] & 0x03) << 2) | (b[2] >> 6)
    day = (b[2] & 0x3E) >> 1
    hour = ((b[2] & 0x01) << 4) | (b[3] >> 4)
    minute = ((b[3] & 0x0F) << 2) | (b[4] >> 6)
    second = b[4] & 0x3F
    if year == 0:
        return None
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def encode_timestamp(d: datetime) -> bytes:
    """Inverse of parse_timestamp (used by tests)."""
    v = (d.year << 26) | (d.month << 22) | (d.day << 17) | (d.hour << 12) | (d.minute << 6) | d.second
    return v.to_bytes(5, "big")


@dataclass(frozen=True)
class VrFormat:
    video: str
    audio: list[str]
    fps: float


def _video_attr(v: int) -> tuple[str, float]:
    std = "PAL" if (v >> 12) & 3 == 1 else "NTSC"
    height = 576 if std == "PAL" else 480
    width, h = {0: (720, height), 1: (704, height), 2: (352, height), 3: (352, height // 2),
                4: (544, height), 5: (480, height)}.get((v >> 3) & 7, (0, height))
    aspect = "16:9" if (v >> 10) & 3 == 1 else "4:3"
    codec = "MPEG-2" if (v >> 14) & 3 == 1 else "MPEG-1"
    return f"{codec} {width}x{h} {std} {aspect}", 25.0 if std == "PAL" else 29.97


def _audio_attr(b: bytes) -> str:
    return {0: "AC-3", 2: "MPEG-1", 3: "MPEG-2", 4: "LPCM"}.get(b[0] >> 5, "unknown")


@dataclass
class VrDisc:
    info: str
    formats: list[VrFormat]
    recordings: list[Recording]
    notes: list[str]


def parse_vr(ifo: bytes, vro: SpanReader) -> VrDisc:
    if ifo[:12] != MAGIC:
        raise VrError("not a VR_MANGR.IFO (missing DVD_RTR_VMG0)")
    charset = CHARSETS.get(ifo[67], "latin-1")
    info = " ".join(
        t for t in (ifo[98:162].split(b"\0")[0].decode(charset, "replace").strip(),
                    ifo[162:226].split(b"\0")[0].decode(charset, "replace").strip()) if t
    )

    p = _u32(ifo, 256)
    if p == 0 or p + 8 > len(ifo):
        raise VrError("VR_MANGR.IFO has no program table")
    if ifo[p + 2] == 0:
        raise VrError("VR_MANGR.IFO lists no recordings")
    n_formats = ifo[p + 3]
    formats = []
    for i in range(n_formats):
        f = p + 8 + 60 * i
        video, fps = _video_attr(_u16(ifo, f))
        n_audio = ifo[f + 2]
        audio = [_audio_attr(ifo[f + 4:f + 7])] if n_audio else []
        if n_audio > 1:
            audio.append(_audio_attr(ifo[f + 7:f + 10]))
        formats.append(VrFormat(video, audio, fps))

    gi = p + 8 + 60 * n_formats
    n_programs = _u16(ifo, gi)
    notes: list[str] = []
    recordings: list[Recording] = []
    for i in range(n_programs):
        rec_at = p + _u32(ifo, gi + 2 + 4 * i)
        attr = _u16(ifo, rec_at)
        date = parse_timestamp(ifo[rec_at + 2:rec_at + 7])
        fmt_id = ifo[rec_at + 8]
        s_ptm, e_ptm = _u32(ifo, rec_at + 9), _u32(ifo, rec_at + 15)
        m = rec_at + 21 + (12 if attr & 0x80 else 0) + 2
        n_time, n_vobu = _u16(ifo, m), _u16(ifo, m + 2)
        first = _u32(ifo, m + 6)
        entries = m + 10 + 7 * n_time
        sizes = [_u16(ifo, entries + 3 * k + 1) & 0x03FF for k in range(n_vobu)]
        sectors = sum(sizes)
        if sectors == 0:
            notes.append(f"recording {i + 1} is empty and was left out")
            continue
        if first + sectors > vro.total_sectors:
            notes.append(f"recording {i + 1} runs past the end of VR_MOVIE.VRO and was left out")
            continue
        fmt = formats[fmt_id - 1] if 1 <= fmt_id <= len(formats) else (formats[0] if formats else None)
        rate = fmt.fps if fmt else 29.97

        seconds = ((e_ptm - s_ptm) % (1 << 32)) / 90000
        # sanity check: SD video is roughly 1-15 Mbit/s
        bits = sectors * SECTOR * 8
        if not (bits / 20e6 <= seconds <= bits / 0.5e6):
            seconds = 0.0  # unknown; measured after conversion

        last_vobu = first + sectors - (sizes[-1] if sizes else 1)
        cell = Cell(
            number=1, vob_id=i + 1, cell_id=1, duration=Duration.from_seconds(seconds, rate),
            first_sector=first, last_sector=first + sectors - 1, last_vobu_sector=last_vobu,
            seamless=False, stc_discontinuity=False,
        )
        recordings.append(Recording(
            index=len(recordings) + 1, cells=[cell], reader=vro, stitch=False, date=date,
            origin=f"prog {i + 1}", seconds=seconds or None,
        ))

    if _scrambled(vro, recordings):
        notes.append("this disc is copy-protected (CPRM, usually TV recordings); "
                     "dvdcam-rip can't convert protected video")
    return VrDisc(info, formats, recordings, notes)


def _scrambled(vro: SpanReader, recordings: list[Recording]) -> bool:
    """True if the first video packet of the first recording is encrypted."""
    if not recordings:
        return False
    rec = recordings[0]
    data = b"".join(vro.read(rec.first_sector, min(64, rec.cells[0].sectors)))
    i = data.find(b"\x00\x00\x01\xe0")
    return i >= 0 and i + 7 < len(data) and (data[i + 6] >> 4) & 3 != 0


def find_vr_files(files: dict[str, FileRef]) -> tuple[FileRef, FileRef] | None:
    vro = files.get("VR_MOVIE.VRO")
    ifo = files.get("VR_MANGR.IFO") or files.get("VR_MANGR.BUP")
    return (ifo, vro) if ifo and vro else None


def read_vr(files: dict[str, FileRef]) -> VrDisc:
    pair = find_vr_files(files)
    if not pair:
        raise VrError("DVD_RTAV has no VR_MANGR.IFO and VR_MOVIE.VRO")
    vro = SpanReader.of_files([pair[1]])
    errors = []
    for name in ("VR_MANGR.IFO", "VR_MANGR.BUP"):
        ref = files.get(name)
        if not ref:
            continue
        try:
            return parse_vr(ref.read(), vro)
        except (VrError, struct.error, IndexError) as e:
            errors.append(f"{name}: {e or type(e).__name__}")
    raise VrError("can't read the DVD-VR disc map: " + "; ".join(errors))
