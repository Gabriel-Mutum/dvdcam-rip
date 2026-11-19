"""Scan mode (unfinalized discs), DVD-VR discs and ISO images."""

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ifo_builder import write_disc  # noqa: E402
from test_ifo import chapters_disc  # noqa: E402
from vr_builder import Program, write_vr_disc  # noqa: E402

from dvdcam_rip.convert import plan_jobs  # noqa: E402
from dvdcam_rip.dvdvr import encode_timestamp, parse_timestamp  # noqa: E402
from dvdcam_rip.source import SourceError, open_source  # noqa: E402
from dvdcam_rip.vob import PCI_HEADER  # noqa: E402

START = 19309  # 0.2145 s, where Hitachi cells start


# --------------------------------------------------- synthetic DVD video


def nav_sector(vob_id: int, cell_id: int, s_ptm: int, e_ptm: int, ea: int, menu: bool = False) -> bytes:
    b = bytearray(2048)
    b[0:4] = b"\x00\x00\x01\xba"
    b[4] = 0x44
    b[13] = 0xF8
    b[0x26:0x2D] = PCI_HEADER
    b[0x400:0x404] = b"\x00\x00\x01\xbf"
    pci, dsi = 0x2D, 0x407
    struct.pack_into(">II", b, pci + 12, s_ptm, e_ptm)
    struct.pack_into(">I", b, dsi + 8, ea)
    struct.pack_into(">H", b, dsi + 24, vob_id)
    b[dsi + 27] = cell_id
    if menu:
        b[pci + 97] = 1  # highlight status: this is a menu
    return bytes(b)


def video_sector(timecode: float) -> bytes:
    b = bytearray(2048)
    seq = bytes([0, 0, 1, 0xB3, 0x2D, 0x01, 0xE0, 0x24]) + bytes(4) + b"\x00\x00\x01\xb5\x14"
    h, rem = divmod(int(timecode), 3600)
    m, s = divmod(rem, 60)
    f = int(round((timecode - int(timecode)) * 30))
    x = (h << 26) | (m << 20) | (1 << 19) | (s << 13) | (f << 7) | (1 << 6)
    gop = b"\x00\x00\x01\xb8" + x.to_bytes(4, "big")
    b[14:14 + len(seq)] = seq
    b[40:40 + len(gop)] = gop
    return bytes(b)


def cell(vob_id: int, seconds: float, timecode: float, menu: bool = False) -> bytes:
    """A cell of half-second VOBUs, each a NAV pack plus one video sector."""
    out = b""
    for k in range(int(seconds * 2)):
        s = START + k * 45000
        out += nav_sector(vob_id, 1, s, s + 45000, 1, menu) + video_sector(timecode + k * 0.5)
    return out


class Temp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class ScanTests(Temp):
    def scan_vob(self, data: bytes):
        folder = self.root / "disc"
        folder.mkdir(exist_ok=True)
        (folder / "VTS_01_1.VOB").write_bytes(data)  # no IFO files, like an unfinalized disc
        return open_source(folder)

    def test_elapsed_timecode_splits_recordings(self):
        media = self.scan_vob(cell(1, 2, 0) + cell(2, 3, 2.0) + cell(3, 1, 0))
        self.assertIn("scanned", media.kind)
        self.assertTrue(any("unfinalized" in n for n in media.notes))
        self.assertEqual([len(r.cells) for r in media.recordings], [2, 1])
        self.assertAlmostEqual(media.recordings[0].duration_seconds, 5.0, places=2)
        self.assertEqual(media.video, "MPEG-2 720x480 NTSC 4:3")
        self.assertTrue(all(r.stitch and r.reader for r in media.recordings))

    def test_time_of_day_timecode(self):
        media = self.scan_vob(cell(1, 2, 3600) + cell(2, 2, 3602) + cell(3, 2, 7200))
        self.assertEqual([len(r.cells) for r in media.recordings], [2, 1])

    def test_no_timecode_keeps_every_segment(self):
        data = b"".join(cell(i, 1, 0).replace(b"\x00\x00\x01\xb8", b"\x00\x00\x02\xb8") for i in (1, 2, 3))
        media = self.scan_vob(data)
        self.assertEqual(len(media.recordings), 3)
        self.assertTrue(any("no timecode" in n for n in media.notes))

    def test_menus_are_skipped(self):
        media = self.scan_vob(cell(9, 1, 0, menu=True) + cell(1, 2, 0))
        self.assertEqual(len(media.recordings), 1)
        self.assertEqual(media.recordings[0].cells[0].vob_id, 1)

    def test_scan_flag_and_raw_image(self):
        img = self.root / "unfinalized.img"
        img.write_bytes(bytes(2048 * 40) + cell(1, 2, 0) + cell(2, 1, 0))  # junk, then video
        media = open_source(img)
        self.assertIn("scanned", media.kind)
        self.assertEqual(len(media.recordings), 2)
        self.assertEqual(media.recordings[0].first_sector, 40)
        self.assertEqual(media.name, "unfinalized")
        self.assertIsNone(media.protect)

    def test_nothing_to_scan(self):
        with self.assertRaises(SourceError):
            self.scan_vob(bytes(2048 * 10))


# ------------------------------------------------------------- DVD-VR


class VrTests(Temp):
    def test_timestamp_round_trip(self):
        d = datetime(2006, 11, 30, 23, 59, 58)
        self.assertEqual(parse_timestamp(encode_timestamp(d)), d)
        self.assertIsNone(parse_timestamp(bytes(5)))

    def test_programs_dates_and_names(self):
        progs = [  # ~3 Mbit/s, like real SD video
            Program(datetime(2007, 5, 1, 9, 0, 1), 0, [200, 200], START, START + 2 * 90000),
            Program(datetime(2007, 5, 1, 9, 30, 0), 400, [200], START, START + 1 * 90000),
            Program(None, 600, [5]),  # no date, no usable length
        ]
        write_vr_disc(self.root, progs)
        media = open_source(self.root)
        self.assertEqual(media.kind, "DVD-VR")
        self.assertEqual(media.camera, "DVD-VR TEST")
        self.assertEqual(media.video, "MPEG-2 704x480 NTSC 4:3")
        self.assertEqual(media.audio, ["AC-3"])
        recs = media.recordings
        self.assertEqual([(r.first_sector, r.last_sector) for r in recs], [(0, 399), (400, 599), (600, 604)])
        self.assertEqual(recs[0].date, datetime(2007, 5, 1, 9, 0, 1))
        self.assertAlmostEqual(recs[0].duration_seconds, 2.0)
        self.assertEqual(recs[2].duration_seconds, 0)  # unknown until converted
        self.assertFalse(recs[0].stitch)
        names = [j.path.name for j in plan_jobs(recs, len(recs), self.root / "out")]
        self.assertEqual(names, ["2007-05-01 09-00-01 (2s).mp4", "2007-05-01 09-30-00 (1s).mp4",
                                 "Recording 03.mp4"])

    def test_bup_and_bad_programs(self):
        d = write_vr_disc(self.root, [Program(datetime(2010, 1, 1), 0, [4]), Program(None, 100, [4])],
                          vro=bytes(2048 * 4))
        (d / "VR_MANGR.IFO").write_bytes(b"garbage")
        media = open_source(self.root)
        self.assertEqual(len(media.recordings), 1)  # the second runs past the end of the VRO
        self.assertTrue(any("past the end" in n for n in media.notes))

    def test_no_dates_note(self):
        write_vr_disc(self.root, [Program(None, 0, [4])])
        self.assertTrue(any("doesn't store recording dates" in n for n in open_source(self.root).notes))


# ---------------------------------------------------------- ISO images


def _make_iso(source: Path, out: Path, filesystems: int) -> bool:
    if os.name != "nt" or not shutil.which("powershell"):
        return False
    script = Path(__file__).with_name("make_iso.ps1")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
         "-Source", str(source), "-Output", str(out), "-FileSystems", str(filesystems)],
        capture_output=True, text=True, timeout=120,
    )
    return r.returncode == 0 and out.exists()


class IsoTests(Temp):
    def check(self, filesystems: int):
        stage = self.root / "stage"
        write_disc(stage, [chapters_disc()], [(1, 1)])
        iso = self.root / "home movies.iso"
        if not _make_iso(stage, iso, filesystems):
            self.skipTest("needs Windows IMAPI2 to build test images")
        media = open_source(iso)
        self.assertEqual(media.kind, "DVD-Video")
        self.assertEqual(media.name, "home movies")
        self.assertEqual([(r.first_sector, r.last_sector) for r in media.recordings],
                         [(0, 29), (30, 129), (130, 149)])
        self.assertEqual(media.recordings[0].reader.total_sectors, 150)

    def test_udf_image(self):
        self.check(4)

    def test_iso9660_image(self):
        self.check(1)


if __name__ == "__main__":
    unittest.main()
