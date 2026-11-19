"""Build small synthetic IFO files for tests, following the DVD-Video layout.

Real camcorder discs can't go in the repo (they're home videos), so tests
write minimal VIDEO_TS.IFO / VTS_xx_0.IFO files with just the tables the
parser reads.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

SECTOR = 2048


def bcd(n: int) -> int:
    return ((n // 10) << 4) | (n % 10)


def bcd_time(seconds: float, fps: int = 30) -> bytes:
    whole = int(seconds)
    frames = round((seconds - whole) * (29.97 if fps == 30 else 25))
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    rate = 0xC0 if fps == 30 else 0x40
    return bytes([bcd(h), bcd(m), bcd(s), rate | bcd(frames)])


@dataclass
class C:
    """A cell: VOB ID, cell ID, length in seconds, first/last sector."""

    vob_id: int
    cell_id: int
    seconds: float
    first: int
    last: int
    stc_discontinuity: bool = False


@dataclass
class P:
    """A program chain: its programs, each a list of cells."""

    title: int
    programs: list[list[C]]
    entry: bool = True


@dataclass
class VtsSpec:
    pgcs: list[P]
    # per VTS title: list of (pgc number, program number) chapters
    titles: list[list[tuple[int, int]]]
    standard: str = "NTSC"
    aspect: str = "4:3"
    audio: list[str] = field(default_factory=lambda: ["AC-3"])


def _pad(b: bytes) -> bytes:
    return b + bytes(-len(b) % SECTOR)


def _pgc(p: P) -> bytes:
    cells = [c for prog in p.programs for c in prog]
    total = sum(c.seconds for c in cells)
    head = bytearray(0xEC)
    head[2] = len(p.programs)
    head[3] = len(cells)
    head[4:8] = bcd_time(total)
    prog_map = bytearray()
    n = 1
    for prog in p.programs:
        prog_map.append(n)
        n += len(prog)
    if len(prog_map) % 2:
        prog_map.append(0)
    playback = bytearray()
    position = bytearray()
    for c in cells:
        rec = bytearray(24)
        rec[0] = 0x02 if c.stc_discontinuity else 0x00
        rec[4:8] = bcd_time(c.seconds)
        struct.pack_into(">I", rec, 8, c.first)
        struct.pack_into(">I", rec, 16, c.last)  # last VOBU start (approx.)
        struct.pack_into(">I", rec, 20, c.last)
        playback += rec
        position += struct.pack(">HBB", c.vob_id, 0, c.cell_id)
    off_map = 0xEC
    off_play = off_map + len(prog_map)
    off_pos = off_play + len(playback)
    struct.pack_into(">HHH", head, 0xE6, off_map, off_play, off_pos)
    return bytes(head + prog_map + playback + position)


def build_vts(spec: VtsSpec) -> bytes:
    # PTT_SRPT
    n_titles = len(spec.titles)
    ptt_body = bytearray()
    offsets = []
    base = 8 + 4 * n_titles
    for chapters in spec.titles:
        offsets.append(base + len(ptt_body))
        for pgcn, pgn in chapters:
            ptt_body += struct.pack(">HH", pgcn, pgn)
    ptt = struct.pack(">HHI", n_titles, 0, base + len(ptt_body) - 1)
    ptt += b"".join(struct.pack(">I", o) for o in offsets) + ptt_body

    # PGCI
    blobs = [_pgc(p) for p in spec.pgcs]
    srp = bytearray()
    pos = 8 + 8 * len(blobs)
    for p, blob in zip(spec.pgcs, blobs):
        srp += struct.pack(">BBHI", (0x80 if p.entry else 0) | p.title, 0, 0, pos)
        pos += len(blob)
    pgci = struct.pack(">HHI", len(blobs), 0, pos - 1) + bytes(srp) + b"".join(blobs)

    # C_ADT
    cells = sorted({(c.vob_id, c.cell_id, c.first, c.last) for p in spec.pgcs for prog in p.programs for c in prog})
    cadt_body = b"".join(struct.pack(">HBBII", v, cid, 0, f, l) for v, cid, f, l in cells)
    vob_count = len({v for v, *_ in cells})
    cadt = struct.pack(">HHI", vob_count, 0, 8 + len(cadt_body) - 1) + cadt_body

    tables = [_pad(ptt), _pad(pgci), _pad(cadt)]
    sectors = [1]
    for t in tables[:-1]:
        sectors.append(sectors[-1] + len(t) // SECTOR)

    head = bytearray(SECTOR)
    head[0:12] = b"DVDVIDEO-VTS"
    struct.pack_into(">I", head, 0xC8, sectors[0])
    struct.pack_into(">I", head, 0xCC, sectors[1])
    struct.pack_into(">I", head, 0xE0, sectors[2])
    std = {"NTSC": 0, "PAL": 1}[spec.standard]
    asp = {"4:3": 0, "16:9": 3}[spec.aspect]
    head[0x200] = (1 << 6) | (std << 4) | (asp << 2)
    head[0x201] = 0
    struct.pack_into(">H", head, 0x202, len(spec.audio))
    codes = {"AC-3": 0, "MPEG-1": 2, "LPCM": 4}
    for i, codec in enumerate(spec.audio):
        head[0x204 + i * 8] = codes[codec] << 5
        head[0x205 + i * 8] = 1  # 48 kHz, 2 channels
    return bytes(head) + b"".join(tables)


def build_vmg(titles: list[tuple[int, int, int]], title_sets: int) -> bytes:
    """titles: (vts number, vts title number, chapter count) per disc title."""
    head = bytearray(SECTOR)
    head[0:12] = b"DVDVIDEO-VMG"
    struct.pack_into(">H", head, 0x3E, title_sets)
    struct.pack_into(">I", head, 0xC4, 1)
    body = b"".join(struct.pack(">BBHHBBI", 0, 1, ch, 0, vts, ttn, 0) for vts, ttn, ch in titles)
    tt = struct.pack(">HHI", len(titles), 0, 8 + len(body) - 1) + body
    return bytes(head) + _pad(tt)


def write_disc(root: Path, vts_specs: list[VtsSpec], titles: list[tuple[int, int]], *, bup_only: bool = False) -> Path:
    """Write a VIDEO_TS folder. titles: (vts number, vts title number)."""
    vt = root / "VIDEO_TS"
    vt.mkdir(parents=True, exist_ok=True)
    entries = [(v, t, len(vts_specs[v - 1].titles[t - 1])) for v, t in titles]
    (vt / "VIDEO_TS.IFO").write_bytes(build_vmg(entries, len(vts_specs)))
    for i, spec in enumerate(vts_specs, start=1):
        data = build_vts(spec)
        name = f"VTS_{i:02d}_0"
        (vt / f"{name}.BUP").write_bytes(data)
        if not bup_only:
            (vt / f"{name}.IFO").write_bytes(data)
        # an empty (zero-filled) title VOB big enough for every cell
        last = max(c.last for p in spec.pgcs for prog in p.programs for c in prog)
        (vt / f"VTS_{i:02d}_1.VOB").write_bytes(bytes((last + 1) * SECTOR))
    return vt
