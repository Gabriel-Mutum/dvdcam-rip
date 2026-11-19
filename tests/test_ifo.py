import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ifo_builder import C, P, VtsSpec, write_disc  # noqa: E402

from dvdcam_rip.cli import main  # noqa: E402
from dvdcam_rip.source import SourceError, open_source  # noqa: E402


def chapters_disc() -> VtsSpec:
    """One title, three clips as three chapters (the common camcorder layout)."""
    return VtsSpec(
        pgcs=[
            P(
                title=1,
                programs=[
                    [C(1, 1, 12.5, 0, 29)],
                    [C(2, 1, 40.0, 30, 129, stc_discontinuity=True)],
                    [C(3, 1, 7.0, 130, 149, stc_discontinuity=True)],
                ],
            )
        ],
        titles=[[(1, 1), (1, 2), (1, 3)]],
    )


def recordings(path, split="chapter"):
    return open_source(path, split).recordings


class TempDiscTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class ParseTests(TempDiscTest):
    def test_chapters_become_recordings(self):
        write_disc(self.root, [chapters_disc()], [(1, 1)])
        media = open_source(self.root)
        disc = media.disc
        self.assertEqual(media.kind, "DVD-Video")
        self.assertEqual(len(disc.vmg.titles), 1)
        self.assertEqual(disc.vmg.titles[0].chapters, 3)
        vts = disc.title_sets[1]
        self.assertEqual(vts.video.codec, "MPEG-2")
        self.assertEqual((vts.video.width, vts.video.height), (720, 480))
        self.assertEqual(vts.audio[0].codec, "AC-3")
        self.assertEqual(vts.audio[0].channels, 2)
        self.assertEqual(media.video, "MPEG-2 720x480 NTSC 4:3")

        recs = media.recordings
        self.assertEqual([r.chapter for r in recs], [1, 2, 3])
        self.assertEqual([r.origin for r in recs], ["ch 1.1", "ch 1.2", "ch 1.3"])
        self.assertEqual([r.vob_ids for r in recs], [[1], [2], [3]])
        self.assertEqual([(r.first_sector, r.last_sector) for r in recs], [(0, 29), (30, 129), (130, 149)])
        self.assertAlmostEqual(recs[0].duration_seconds, 12.5, places=1)
        self.assertEqual(recs[1].size_bytes, 100 * 2048)
        self.assertTrue(recs[1].cells[0].stc_discontinuity)
        self.assertIsNone(recs[0].date)
        self.assertTrue(all(r.reader is not None and r.stitch for r in recs))

    def test_one_title_per_clip(self):
        spec = VtsSpec(
            pgcs=[
                P(title=1, programs=[[C(1, 1, 5, 0, 9)]]),
                P(title=2, programs=[[C(2, 1, 6, 10, 19)]]),
                P(title=3, programs=[[C(3, 1, 7, 20, 29)]]),
            ],
            titles=[[(1, 1)], [(2, 1)], [(3, 1)]],
        )
        write_disc(self.root, [spec], [(1, 1), (1, 2), (1, 3)])
        recs = recordings(self.root)
        self.assertEqual([(r.title, r.chapter, r.pgc) for r in recs], [(1, 1, 1), (2, 1, 2), (3, 1, 3)])

    def test_split_modes(self):
        spec = VtsSpec(
            pgcs=[
                P(
                    title=1,
                    programs=[
                        [C(1, 1, 5, 0, 9), C(1, 2, 5, 10, 19), C(2, 1, 5, 20, 29)],
                        [C(3, 1, 5, 30, 39)],
                    ],
                )
            ],
            titles=[[(1, 1), (1, 2)]],
            standard="PAL",
            aspect="16:9",
        )
        write_disc(self.root, [spec], [(1, 1)])
        disc = open_source(self.root).disc
        self.assertEqual(disc.title_sets[1].video.height, 576)
        self.assertEqual(disc.title_sets[1].video.aspect, "16:9")
        self.assertEqual(len(recordings(self.root, "chapter")), 2)
        self.assertEqual([r.vob_ids for r in recordings(self.root, "vob")], [[1], [2], [3]])
        self.assertEqual(len(recordings(self.root, "cell")), 4)

    def test_cell_address_table(self):
        write_disc(self.root, [chapters_disc()], [(1, 1)])
        ranges = open_source(self.root).disc.title_sets[1].vob_ranges
        self.assertEqual(ranges[(2, 1)], (30, 129))

    def test_falls_back_to_bup(self):
        write_disc(self.root, [chapters_disc()], [(1, 1)], bup_only=True)
        self.assertEqual(len(recordings(self.root)), 3)

    def test_damaged_ifo_uses_bup(self):
        vt = write_disc(self.root, [chapters_disc()], [(1, 1)])
        (vt / "VTS_01_0.IFO").write_bytes(b"\0" * 4096)
        self.assertEqual(len(recordings(self.root)), 3)

    def test_accepts_video_ts_folder_and_loose_files(self):
        vt = write_disc(self.root, [chapters_disc()], [(1, 1)])
        self.assertEqual(len(recordings(vt)), 3)  # the VIDEO_TS folder itself
        self.assertEqual(len(recordings(vt / "VTS_01_1.VOB")), 3)  # a file inside it
        self.assertEqual(open_source(vt).name, self.root.name)  # named after the disc folder

    def test_errors(self):
        with self.assertRaises(SourceError):
            open_source(self.root)  # empty folder
        with self.assertRaises(SourceError):
            open_source(self.root / "nope")

    def test_damaged_maps_without_video_fail_clearly(self):
        vt = write_disc(self.root, [chapters_disc()], [(1, 1)])
        for name in ("VTS_01_0.IFO", "VTS_01_0.BUP"):
            (vt / name).write_bytes(b"\0" * 4096)
        # the zero-filled VOB has no NAV packs, so scanning finds nothing
        with self.assertRaisesRegex(SourceError, "no recordings found"):
            open_source(self.root)


class CliTests(TempDiscTest):
    def run_cli(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(list(args))
        return code, buf.getvalue()

    def test_list(self):
        write_disc(self.root, [chapters_disc()], [(1, 1)])
        code, out = self.run_cli(str(self.root), "--list")
        self.assertEqual(code, 0)
        self.assertIn("3 recordings", out)
        self.assertIn("MPEG-2 720x480 NTSC 4:3", out)
        self.assertIn("Type:    DVD-Video", out)

    def test_json(self):
        write_disc(self.root, [chapters_disc()], [(1, 1)])
        code, out = self.run_cli(str(self.root), "--list", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["type"], "DVD-Video")
        self.assertEqual(len(data["recordings"]), 3)
        self.assertEqual(data["recordings"][1]["cells"][0]["vob_id"], 2)
        self.assertIn("dvd_video", data)

    def test_missing_disc(self):
        code, _ = self.run_cli(str(self.root / "nope"), "--list")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
