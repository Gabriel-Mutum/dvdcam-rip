"""Parser for DVD-Video IFO files (VIDEO_TS.IFO and VTS_xx_0.IFO).

Only the tables needed to find recordings are read:

- VMGI (VIDEO_TS.IFO): TT_SRPT, the disc-wide list of titles.
- VTSI (VTS_xx_0.IFO): VTS_PTT_SRPT (chapters), VTS_PGCI (program chains,
  their programs and cells) and VTS_C_ADT (cell sector ranges), plus the
  video and audio attributes of the title VOBs.

All multi-byte values are big-endian. Table pointers in the IFO header are
sector numbers (2048 bytes) from the start of the IFO; offsets inside a
table are bytes from the start of that table. Cell sector numbers are
relative to the first sector of the title VOBs (VTS_xx_1.VOB).

References: "DVD-Video information" (dvd.sourceforge.net/dvdinfo) and
libdvdread's ifo_types.h.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

SECTOR = 2048


class IfoError(ValueError):
    """The file is not a valid or supported IFO."""


# ---------------------------------------------------------------- helpers


def _u8(b: bytes, o: int) -> int:
    return b[o]


def _u16(b: bytes, o: int) -> int:
    return struct.unpack_from(">H", b, o)[0]


def _u32(b: bytes, o: int) -> int:
    return struct.unpack_from(">I", b, o)[0]


def _bcd(v: int) -> int:
    return (v >> 4) * 10 + (v & 0x0F)


@dataclass(frozen=True)
class Duration:
    """A DVD playback time (BCD hh:mm:ss + frames at 25 or 29.97 fps)."""

    hours: int
    minutes: int
    seconds: int
    frames: int
    fps: float  # 25.0, 29.97, or 0.0 when the rate bits are unset

    @classmethod
    def parse(cls, b: bytes, o: int) -> "Duration":
        h, m, s, f = b[o], b[o + 1], b[o + 2], b[o + 3]
        rate = {1: 25.0, 3: 29.97}.get(f >> 6, 0.0)
        return cls(_bcd(h), _bcd(m), _bcd(s), _bcd(f & 0x3F), rate)

    @property
    def total_seconds(self) -> float:
        frac = self.frames / self.fps if self.fps else 0.0
        return self.hours * 3600 + self.minutes * 60 + self.seconds + frac

    def __str__(self) -> str:
        return f"{self.hours:02d}:{self.minutes:02d}:{self.seconds:02d}.{self.frames:02d}"


# ------------------------------------------------------------- attributes


@dataclass(frozen=True)
class VideoAttributes:
    codec: str  # "MPEG-1" / "MPEG-2"
    standard: str  # "NTSC" / "PAL"
    aspect: str  # "4:3" / "16:9"
    width: int
    height: int

    @classmethod
    def parse(cls, b: bytes, o: int) -> "VideoAttributes":
        b0, b1 = b[o], b[o + 1]
        codec = {0: "MPEG-1", 1: "MPEG-2"}.get(b0 >> 6, "unknown")
        standard = {0: "NTSC", 1: "PAL"}.get((b0 >> 4) & 3, "unknown")
        aspect = {0: "4:3", 3: "16:9"}.get((b0 >> 2) & 3, "unknown")
        height = 576 if standard == "PAL" else 480
        res = (b1 >> 3) & 7
        width, height = {
            0: (720, height),
            1: (704, height),
            2: (352, height),
            3: (352, height // 2),
        }.get(res, (0, 0))
        return cls(codec, standard, aspect, width, height)


@dataclass(frozen=True)
class AudioAttributes:
    codec: str  # "AC-3", "LPCM", "MPEG-1", "MPEG-2", "DTS"
    channels: int
    sample_rate: int
    language: str  # ISO 639 code or ""

    @classmethod
    def parse(cls, b: bytes, o: int) -> "AudioAttributes":
        b0, b1 = b[o], b[o + 1]
        codec = {0: "AC-3", 2: "MPEG-1", 3: "MPEG-2", 4: "LPCM", 6: "DTS"}.get(
            b0 >> 5, "unknown"
        )
        rate = {0: 48000, 1: 96000}.get((b1 >> 4) & 3, 0)
        lang = b[o + 2 : o + 4]
        language = lang.decode("ascii", "replace") if (b0 >> 2) & 3 == 1 else ""
        return cls(codec, (b1 & 7) + 1, rate, language.strip("\x00"))


# ------------------------------------------------------------------ VMGI


@dataclass(frozen=True)
class TitleEntry:
    """A title as listed disc-wide in VIDEO_TS.IFO (TT_SRPT)."""

    number: int  # 1-based, disc-wide
    angles: int
    chapters: int
    vts: int  # title set number (VTS_xx)
    vts_title: int  # title number inside that title set


@dataclass
class Vmg:
    title_sets: int
    titles: list[TitleEntry]


def parse_vmg(data: bytes) -> Vmg:
    if data[:12] != b"DVDVIDEO-VMG":
        raise IfoError("not a VIDEO_TS.IFO (missing DVDVIDEO-VMG)")
    title_sets = _u16(data, 0x3E)
    tt = _u32(data, 0xC4) * SECTOR
    if tt == 0 or tt + 8 > len(data):
        raise IfoError("VIDEO_TS.IFO has no title table")
    count = _u16(data, tt)
    titles = []
    for i in range(count):
        e = tt + 8 + i * 12
        titles.append(
            TitleEntry(
                number=i + 1,
                angles=_u8(data, e + 1),
                chapters=_u16(data, e + 2),
                vts=_u8(data, e + 6),
                vts_title=_u8(data, e + 7),
            )
        )
    return Vmg(title_sets, titles)


# ------------------------------------------------------------------ VTSI


@dataclass(frozen=True)
class Cell:
    """One cell of a program chain, in playback order."""

    number: int  # 1-based within its PGC
    vob_id: int
    cell_id: int
    duration: Duration
    first_sector: int  # relative to the start of the title VOBs
    last_sector: int  # inclusive
    seamless: bool
    stc_discontinuity: bool  # timestamps restart at this cell

    @property
    def sectors(self) -> int:
        return self.last_sector - self.first_sector + 1

    @property
    def size_bytes(self) -> int:
        return self.sectors * SECTOR


@dataclass
class Program:
    number: int  # 1-based within its PGC
    cells: list[Cell]

    @property
    def duration_seconds(self) -> float:
        return sum(c.duration.total_seconds for c in self.cells)


@dataclass
class Pgc:
    number: int  # 1-based within the VTS
    entry: bool  # entry PGC of a title
    title: int  # VTS title number it belongs to (from the category byte)
    duration: Duration
    programs: list[Program]
    cells: list[Cell]


@dataclass(frozen=True)
class Chapter:
    """A part-of-title: which PGC and program a chapter starts at."""

    number: int
    pgc: int
    program: int


@dataclass
class Vts:
    number: int
    video: VideoAttributes
    audio: list[AudioAttributes]
    pgcs: list[Pgc]
    chapters: dict[int, list[Chapter]]  # VTS title number -> chapters
    vob_ranges: dict[tuple[int, int], tuple[int, int]] = field(default_factory=dict)
    """(vob_id, cell_id) -> (first, last) sector, from VTS_C_ADT."""


def _parse_pgc(data: bytes, o: int, number: int, entry: bool, title: int) -> Pgc:
    n_programs = _u8(data, o + 2)
    n_cells = _u8(data, o + 3)
    duration = Duration.parse(data, o + 4)
    prog_map = _u16(data, o + 0xE6)
    playback = _u16(data, o + 0xE8)
    position = _u16(data, o + 0xEA)

    cells: list[Cell] = []
    if n_cells and playback and position:
        for i in range(n_cells):
            p = o + playback + i * 24
            q = o + position + i * 4
            cat = data[p]
            cells.append(
                Cell(
                    number=i + 1,
                    vob_id=_u16(data, q),
                    cell_id=_u8(data, q + 3),
                    duration=Duration.parse(data, p + 4),
                    first_sector=_u32(data, p + 8),
                    last_sector=_u32(data, p + 20),
                    seamless=bool(cat & 0x08),
                    stc_discontinuity=bool(cat & 0x02),
                )
            )

    entry_cells = [_u8(data, o + prog_map + i) for i in range(n_programs)] if prog_map else []
    programs: list[Program] = []
    for i, first in enumerate(entry_cells):
        last = entry_cells[i + 1] - 1 if i + 1 < len(entry_cells) else n_cells
        if not 1 <= first <= last <= n_cells:
            raise IfoError(f"PGC {number}: program {i + 1} has bad cell range {first}-{last}")
        programs.append(Program(i + 1, cells[first - 1 : last]))

    return Pgc(number, entry, title, duration, programs, cells)


def parse_vts(data: bytes, number: int = 0) -> Vts:
    if data[:12] != b"DVDVIDEO-VTS":
        raise IfoError("not a VTS IFO (missing DVDVIDEO-VTS)")

    video = VideoAttributes.parse(data, 0x200)
    n_audio = min(_u16(data, 0x202), 8)
    audio = [AudioAttributes.parse(data, 0x204 + i * 8) for i in range(n_audio)]

    # VTS_PGCI: program chains
    pgci = _u32(data, 0xCC) * SECTOR
    if pgci == 0 or pgci + 8 > len(data):
        raise IfoError("VTS IFO has no program chain table")
    pgcs = []
    for i in range(_u16(data, pgci)):
        e = pgci + 8 + i * 8
        cat = data[e]
        pgcs.append(
            _parse_pgc(data, pgci + _u32(data, e + 4), i + 1, bool(cat & 0x80), cat & 0x7F)
        )

    # VTS_PTT_SRPT: chapters per title
    chapters: dict[int, list[Chapter]] = {}
    ptt = _u32(data, 0xC8) * SECTOR
    if ptt and ptt + 8 <= len(data):
        n_titles = _u16(data, ptt)
        end = _u32(data, ptt + 4) + 1  # end address is the last byte
        offsets = [_u32(data, ptt + 8 + i * 4) for i in range(n_titles)] + [end]
        for t in range(n_titles):
            entries = []
            for k, off in enumerate(range(offsets[t], offsets[t + 1], 4)):
                entries.append(Chapter(k + 1, _u16(data, ptt + off), _u16(data, ptt + off + 2)))
            chapters[t + 1] = entries

    # VTS_C_ADT: sector range of every (vob_id, cell_id)
    ranges: dict[tuple[int, int], tuple[int, int]] = {}
    cadt = _u32(data, 0xE0) * SECTOR
    if cadt and cadt + 8 <= len(data):
        end = _u32(data, cadt + 4) + 1
        for e in range(cadt + 8, min(cadt + end, len(data) - 11), 12):
            ranges[(_u16(data, e), _u8(data, e + 2))] = (_u32(data, e + 4), _u32(data, e + 8))

    return Vts(number, video, audio, pgcs, chapters, ranges)


# ------------------------------------------------------------------- disc


@dataclass
class Disc:
    root: Path  # the VIDEO_TS folder
    vmg: Vmg
    title_sets: dict[int, Vts]


def find_video_ts(source: Path) -> Path:
    """Accept a disc root or a VIDEO_TS folder; return the VIDEO_TS folder."""
    source = Path(source)
    if source.is_file():
        if source.suffix.lower() == ".iso":
            raise IfoError("ISO images are not supported yet; mount the image and pass its drive")
        source = source.parent
    for candidate in (source, source / "VIDEO_TS", source / "video_ts"):
        if _ifo_path(candidate, "VIDEO_TS.IFO"):
            return candidate
    if (source / "DVD_RTAV").is_dir():
        raise IfoError("this is a DVD-VR disc (DVD_RTAV/VR_MOVIE.VRO), not supported yet")
    raise IfoError(f"no VIDEO_TS/VIDEO_TS.IFO under {source}")


def _ifo_path(folder: Path, name: str) -> Path | None:
    """Find an IFO, falling back to its .BUP backup copy."""
    if not folder.is_dir():
        return None
    by_upper = {p.name.upper(): p for p in folder.iterdir()}
    return by_upper.get(name) or by_upper.get(name[:-4] + ".BUP")


def read_disc(source: Path) -> Disc:
    root = find_video_ts(source)
    vmg = parse_vmg(_read_ifo(root, "VIDEO_TS.IFO"))
    sets = {}
    for n in sorted({t.vts for t in vmg.titles}):
        sets[n] = parse_vts(_read_ifo(root, f"VTS_{n:02d}_0.IFO"), n)
    return Disc(root, vmg, sets)


def _read_ifo(folder: Path, name: str) -> bytes:
    """Read an IFO; if it is damaged or missing, try its .BUP backup."""
    by_upper = {p.name.upper(): p for p in folder.iterdir()}
    errors = []
    for candidate in (name, name[:-4] + ".BUP"):
        path = by_upper.get(candidate)
        if path is None:
            continue
        data = path.read_bytes()
        if data[:9] == b"DVDVIDEO-":
            return data
        errors.append(f"{path.name} has no DVDVIDEO header")
    raise IfoError(f"cannot read {name} in {folder}: " + ("; ".join(errors) or "not found"))
