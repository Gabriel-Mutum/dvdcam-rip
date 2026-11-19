"""Build DVD-VR discs (DVD_RTAV/VR_MANGR.IFO + VR_MOVIE.VRO) for tests.

Follows the layout described in dvdcam_rip/dvdvr.py. It only proves the
parser and the builder agree; real recorder discs are the final check.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dvdcam_rip.dvdvr import encode_timestamp  # noqa: E402

VIDEO_NTSC_704_43 = (1 << 14) | (1 << 3)  # MPEG-2, 704 wide, 4:3, NTSC


@dataclass
class Program:
    date: datetime | None
    first_sector: int  # in VR_MOVIE.VRO
    vobu_sizes: list[int]  # sectors
    s_ptm: int = 0
    e_ptm: int = 0  # 90 kHz


def build_vr_ifo(programs: list[Program], info: str = "DVD-VR TEST",
                 video_attr: int = VIDEO_NTSC_704_43) -> bytes:
    head = bytearray(512)
    head[0:12] = b"DVD_RTR_VMG0"
    head[67] = 0x11  # ISO 8859-1
    text = info.encode("latin-1")[:64]
    head[98:98 + len(text)] = text
    struct.pack_into(">I", head, 256, 512)  # PGIT right after the header

    fmt = bytearray(60)
    struct.pack_into(">H", fmt, 0, video_attr)
    fmt[2] = 1  # one audio stream
    fmt[4:7] = bytes([0x00, 0x01, 0x00])  # AC-3, 2 channels

    records = []
    for p in programs:
        r = bytearray(21)
        r[2:7] = encode_timestamp(p.date) if p.date else bytes(5)
        r[8] = 1  # format 1
        struct.pack_into(">I", r, 9, p.s_ptm)
        struct.pack_into(">I", r, 15, p.e_ptm)
        r += bytes(2)  # the 2 unexplained bytes before the VOBU map
        r += struct.pack(">HHHI", 0, len(p.vobu_sizes), 0, p.first_sector)
        for size in p.vobu_sizes:
            r += bytes([0]) + struct.pack(">H", size & 0x03FF)
        records.append(bytes(r))

    pgit = bytearray(8) + fmt
    pgit[2] = 1
    pgit[3] = 1
    gi = len(pgit)
    pgit += struct.pack(">H", len(programs))
    offsets_at = len(pgit)
    pgit += bytes(4 * len(programs))
    for i, rec in enumerate(records):
        struct.pack_into(">I", pgit, offsets_at + 4 * i, len(pgit))
        pgit += rec
    struct.pack_into(">I", pgit, 4, len(pgit) - 1)
    assert gi == 68
    data = bytes(head) + bytes(pgit)
    out = bytearray(data + bytes(-len(data) % 2048))
    struct.pack_into(">I", out, 12, len(out) - 1)
    return bytes(out)


def write_vr_disc(root: Path, programs: list[Program], vro: bytes | None = None) -> Path:
    d = root / "DVD_RTAV"
    d.mkdir(parents=True, exist_ok=True)
    ifo = build_vr_ifo(programs)
    (d / "VR_MANGR.IFO").write_bytes(ifo)
    (d / "VR_MANGR.BUP").write_bytes(ifo)
    if vro is None:
        total = max(p.first_sector + sum(p.vobu_sizes) for p in programs)
        vro = bytes(total * 2048)
    (d / "VR_MOVIE.VRO").write_bytes(vro)
    return d
