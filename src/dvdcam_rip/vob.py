"""Read title VOBs (read-only) and stitch a recording's cells into one stream.

Camcorders restart the MPEG timestamps at every cell, even between seamless
cells of the same recording. A DVD player hides this by playing each cell
for exactly as long as its NAV packs say (vobu_s_ptm .. vobu_e_ptm).
We do the same: every cell's timestamps (pack SCR, PES PTS/DTS) are shifted
so the cell starts exactly where the previous one ended. The audio and video
payload bytes are never touched, so the result is bit-for-bit lossless.

FFmpeg's own `dvdvideo` demuxer would also fix the timing, but it drops the
leading B-frames of a recording's first closed GOP; doing it here keeps
every frame.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .ifo import SECTOR, Cell

PTS_WRAP = 1 << 33
CHUNK_SECTORS = 512  # 1 MB reads


class VobError(ValueError):
    pass


class TitleVobs:
    """The title VOBs of one title set (VTS_xx_1.VOB … VTS_xx_9.VOB) as one
    sector-addressed, read-only byte range."""

    def __init__(self, folder: Path, vts: int):
        by_upper = {p.name.upper(): p for p in Path(folder).iterdir()}
        self.files: list[tuple[int, int, Path]] = []  # (first sector, sectors, path)
        start = 0
        for n in range(1, 10):
            path = by_upper.get(f"VTS_{vts:02d}_{n}.VOB")
            if path is None:
                break
            sectors = path.stat().st_size // SECTOR
            self.files.append((start, sectors, path))
            start += sectors
        if not self.files:
            raise VobError(f"no VTS_{vts:02d}_1.VOB in {folder}")
        self.total_sectors = start

    def read(self, first: int, count: int) -> Iterator[bytes]:
        """Yield the bytes of sectors [first, first + count) in chunks."""
        if first < 0 or first + count > self.total_sectors:
            raise VobError(
                f"sectors {first}-{first + count - 1} are past the end of the VOBs "
                f"({self.total_sectors} sectors); the disc copy may be incomplete"
            )
        sector, remaining = first, count
        for start, sectors, path in self.files:
            if remaining == 0:
                break
            if sector >= start + sectors:
                continue
            with open(path, "rb") as f:  # read-only, always
                f.seek((sector - start) * SECTOR)
                n = min(remaining, start + sectors - sector)
                while n:
                    take = min(n, CHUNK_SECTORS)
                    data = f.read(take * SECTOR)
                    if len(data) != take * SECTOR:
                        raise VobError(f"{path.name} ended early")
                    yield data
                    sector += take
                    remaining -= take
                    n -= take

    def sector(self, n: int) -> bytes:
        return b"".join(self.read(n, 1))


# ------------------------------------------------------------- NAV packs


@dataclass(frozen=True)
class Nav:
    lbn: int  # sector number recorded in the DSI
    vob_id: int
    cell_id: int
    s_ptm: int  # first video presentation time of the VOBU (90 kHz)
    e_ptm: int  # end of the VOBU's video presentation (90 kHz)


def parse_nav(sector: bytes) -> Nav | None:
    """Parse a NAV pack (PCI + DSI). Returns None if the sector isn't one."""
    if (
        sector[:4] != b"\x00\x00\x01\xba"
        or sector[0x26:0x2A] != b"\x00\x00\x01\xbf"
        or sector[0x400:0x404] != b"\x00\x00\x01\xbf"
    ):
        return None
    pci, dsi = 0x2D, 0x407
    return Nav(
        lbn=struct.unpack_from(">I", sector, dsi + 4)[0],
        vob_id=struct.unpack_from(">H", sector, dsi + 24)[0],
        cell_id=sector[dsi + 27],
        s_ptm=struct.unpack_from(">I", sector, pci + 12)[0],
        e_ptm=struct.unpack_from(">I", sector, pci + 16)[0],
    )


@dataclass(frozen=True)
class CellTiming:
    cell: Cell
    start: int  # vobu_s_ptm of the first VOBU
    end: int  # vobu_e_ptm of the last VOBU

    @property
    def duration(self) -> int:
        return (self.end - self.start) % PTS_WRAP


def cell_timing(vobs: TitleVobs, cell: Cell) -> CellTiming:
    first = parse_nav(vobs.sector(cell.first_sector))
    last = parse_nav(vobs.sector(cell.last_vobu_sector))
    if first is None or last is None:
        raise VobError(f"no NAV pack where the IFO says cell {cell.vob_id}/{cell.cell_id} is")
    if (first.vob_id, first.cell_id) != (cell.vob_id, cell.cell_id):
        raise VobError(
            f"IFO and VOB disagree at sector {cell.first_sector}: IFO says cell "
            f"{cell.vob_id}/{cell.cell_id}, VOB says {first.vob_id}/{first.cell_id}"
        )
    return CellTiming(cell, first.s_ptm, last.e_ptm)


def recording_duration(timings: list[CellTiming]) -> float:
    return sum(t.duration for t in timings) / 90000


# ----------------------------------------------------- timestamp rewriting


def read_pts(b: bytes | bytearray, o: int) -> int:
    return (
        ((b[o] >> 1) & 0x07) << 30
        | b[o + 1] << 22
        | (b[o + 2] >> 1) << 15
        | b[o + 3] << 7
        | b[o + 4] >> 1
    )


def write_pts(b: bytearray, o: int, value: int) -> None:
    """Write a 33-bit timestamp, keeping the 4-bit prefix and marker bits."""
    v = value % PTS_WRAP
    b[o] = (b[o] & 0xF0) | ((v >> 29) & 0x0E) | 1
    b[o + 1] = (v >> 22) & 0xFF
    b[o + 2] = ((v >> 14) & 0xFE) | 1
    b[o + 3] = (v >> 7) & 0xFF
    b[o + 4] = ((v << 1) & 0xFE) | 1


def read_scr(b: bytes | bytearray, o: int) -> int:
    """SCR base (33 bits) of an MPEG-2 pack header starting at o."""
    p = o + 4
    return (
        ((b[p] >> 3) & 0x07) << 30
        | (b[p] & 0x03) << 28
        | b[p + 1] << 20
        | ((b[p + 2] >> 3) & 0x1F) << 15
        | (b[p + 2] & 0x03) << 13
        | b[p + 3] << 5
        | (b[p + 4] >> 3)
    )


def write_scr(b: bytearray, o: int, value: int) -> None:
    """Rewrite the SCR base, keeping the SCR extension and marker bits."""
    v = value % PTS_WRAP
    p = o + 4
    ext_hi = b[p + 4] & 0x03  # top 2 bits of the 9-bit extension
    b[p] = 0x44 | ((v >> 27) & 0x38) | ((v >> 28) & 0x03)
    b[p + 1] = (v >> 20) & 0xFF
    b[p + 2] = ((v >> 12) & 0xF8) | 0x04 | ((v >> 13) & 0x03)
    b[p + 3] = (v >> 5) & 0xFF
    b[p + 4] = ((v << 3) & 0xF8) | 0x04 | ext_hi


_TIMED = set(range(0xC0, 0xF0)) | {0xBD}  # audio, video, private stream 1


def shift_pack(pack: bytearray, delta: int) -> None:
    """Shift the SCR and every PES PTS/DTS in one 2048-byte pack in place."""
    if pack[:4] != b"\x00\x00\x01\xba" or (pack[4] & 0xC0) != 0x40:
        return  # not an MPEG-2 pack header; leave it alone
    write_scr(pack, 0, read_scr(pack, 0) + delta)
    o = 14 + (pack[13] & 0x07)
    while o + 6 <= len(pack) and pack[o : o + 3] == b"\x00\x00\x01":
        sid = pack[o + 3]
        length = struct.unpack_from(">H", pack, o + 4)[0]
        if sid in _TIMED and o + 14 <= len(pack) and (pack[o + 6] & 0xC0) == 0x80:
            flags = pack[o + 7] >> 6
            if flags & 2:
                write_pts(pack, o + 9, read_pts(pack, o + 9) + delta)
            if flags == 3:
                write_pts(pack, o + 14, read_pts(pack, o + 14) + delta)
        o += 6 + length


def stitched_stream(vobs: TitleVobs, timings: list[CellTiming]) -> Iterator[bytes]:
    """Yield the recording as one MPEG program stream with continuous time."""
    if not timings:
        return
    position = timings[0].start  # where the next cell should start
    for t in timings:
        delta = (position - t.start) % PTS_WRAP
        for chunk in vobs.read(t.cell.first_sector, t.cell.sectors):
            if delta == 0:
                yield chunk
                continue
            buf = bytearray(chunk)
            for o in range(0, len(buf), SECTOR):
                pack = memoryview(buf)[o : o + SECTOR]
                shift_pack(pack, delta)  # type: ignore[arg-type]
            yield bytes(buf)
        position = (position + t.duration) % PTS_WRAP
