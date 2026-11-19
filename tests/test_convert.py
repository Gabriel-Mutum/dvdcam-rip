import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ifo_builder import write_disc  # noqa: E402
from test_ifo import chapters_disc  # noqa: E402

from dvdcam_rip import cli  # noqa: E402
from dvdcam_rip.convert import (  # noqa: E402
    check_output_location,
    disc_name,
    file_name,
    parse_only,
    plan_jobs,
    short_duration,
    space_needed,
)
from dvdcam_rip.ifo import read_disc  # noqa: E402
from dvdcam_rip.recordings import list_recordings  # noqa: E402
from dvdcam_rip.vob import PTS_WRAP, read_pts, read_scr, shift_pack, write_pts, write_scr  # noqa: E402


def pack_with_pes(scr: int, pts: int, dts: int | None) -> bytearray:
    """A 2048-byte MPEG-2 pack holding one video PES with PTS (and DTS)."""
    p = bytearray(2048)
    p[0:4] = b"\x00\x00\x01\xba"
    p[4:14] = bytes([0x44, 0, 0x04, 0, 0x04, 0x01, 0x01, 0x89, 0xC3, 0xF8])
    write_scr(p, 0, scr)
    o = 14
    flags = 0xC0 if dts is not None else 0x80
    hlen = 10 if dts is not None else 5
    length = 2048 - o - 6
    p[o:o + 9] = b"\x00\x00\x01\xe0" + length.to_bytes(2, "big") + bytes([0x81, flags, hlen])
    p[o + 9] = 0x31 if dts is not None else 0x21
    write_pts(p, o + 9, pts)
    if dts is not None:
        p[o + 14] = 0x11
        write_pts(p, o + 14, dts)
    return p


class TimestampTests(unittest.TestCase):
    def test_pts_round_trip(self):
        for v in (0, 1, 19309, 90000 * 3600, PTS_WRAP - 1):
            b = bytearray(b"\x21\x00\x01\x00\x01")
            write_pts(b, 0, v)
            self.assertEqual(read_pts(b, 0), v)
            self.assertEqual(b[0] >> 4, 0x2, "prefix kept")
            self.assertEqual(b[0] & 1, 1, "marker kept")

    def test_scr_round_trip_keeps_extension(self):
        p = pack_with_pes(0, 0, None)
        ext_before = (p[8] & 3, p[9])
        for v in (0, 12345678, PTS_WRAP - 1):
            write_scr(p, 0, v)
            self.assertEqual(read_scr(p, 0), v)
        self.assertEqual((p[8] & 3, p[9]), ext_before)

    def test_shift_pack(self):
        p = pack_with_pes(1000, 19309, 16306)
        payload = bytes(p[14 + 19:])
        shift_pack(p, 90000)
        self.assertEqual(read_scr(p, 0), 91000)
        self.assertEqual(read_pts(p, 14 + 9), 19309 + 90000)
        self.assertEqual(read_pts(p, 14 + 14), 16306 + 90000)
        self.assertEqual(bytes(p[14 + 19:]), payload, "payload untouched")

    def test_shift_wraps(self):
        p = pack_with_pes(PTS_WRAP - 10, PTS_WRAP - 5, None)
        shift_pack(p, 20)
        self.assertEqual(read_scr(p, 0), 10)
        self.assertEqual(read_pts(p, 14 + 9), 15)


class PlanningTests(unittest.TestCase):
    def test_parse_only(self):
        self.assertEqual(parse_only("3", 12), [3])
        self.assertEqual(parse_only("1, 4-6,5", 12), [1, 4, 5, 6])
        self.assertEqual(parse_only("7-5", 12), [5, 6, 7])
        for bad in ("0", "13", "a", "2-x", ""):
            with self.assertRaises(ValueError):
                parse_only(bad, 12)

    def test_short_duration(self):
        self.assertEqual(short_duration(8.5), "8s")
        self.assertEqual(short_duration(119.1), "1m59s")
        self.assertEqual(short_duration(3725), "1h02m05s")

    def test_names_and_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Video Camera"
            write_disc(root, [chapters_disc()], [(1, 1)])
            disc = read_disc(root)
            self.assertEqual(disc_name(disc), "Video Camera")  # folder holding VIDEO_TS
            recs = list_recordings(disc)
            self.assertEqual(file_name(recs[1], len(recs)), "Recording 02 (40s).mp4")
            with self.assertRaises(ValueError):
                check_output_location(disc.root / "out", disc)
            check_output_location(Path(tmp) / "elsewhere", disc)

            out = Path(tmp) / "out"
            jobs = plan_jobs(recs, len(recs), out)
            full = space_needed(jobs, overwrite=False)
            out.mkdir()
            jobs[0].path.write_bytes(b"done")
            self.assertEqual(space_needed(jobs, overwrite=False), full - recs[0].size_bytes)
            self.assertEqual(space_needed(jobs, overwrite=True), full)


class CliFlowTests(unittest.TestCase):
    """The interactive flow: list -> "does this look right?" -> format -> convert.
    FFmpeg is mocked: fake outputs contain the name of their format."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        write_disc(self.tmp / "disc", [chapters_disc()], [(1, 1)])

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args, answers=None, output=True):
        def fake_convert(job, *a, fmt="original", **k):
            job.status = "done"
            job.path.write_text(fmt)

        def fake_existing(tools, path):
            return path.read_text()

        fake_tools = mock.Mock(version="test", ffmpeg=Path("ffmpeg"))
        buf = io.StringIO()
        out_args = ["-o", str(self.tmp / "out")] if output else []
        with (
            mock.patch.object(cli, "find_tools", return_value=fake_tools),
            mock.patch.object(cli, "has_encoder", return_value=True),
            mock.patch.object(cli, "existing_format", side_effect=fake_existing),
            mock.patch.object(cli, "default_output", side_effect=lambda d, f: self.tmp / f"default-{f}"),
            mock.patch.object(cli.sys.stdin, "isatty", return_value=True),
            mock.patch("builtins.input", side_effect=answers or EOFError),
            mock.patch.object(cli, "convert_one", side_effect=fake_convert) as convert,
            redirect_stdout(buf),
        ):
            code = cli.main([str(self.tmp / "disc"), *out_args, *args])
        return code, buf.getvalue(), convert

    def test_declining_gives_feedback_and_writes_nothing(self):
        code, out, convert = self.run_cli(answers=["n"])
        self.assertEqual(code, 0)
        self.assertIn("Found 3 recordings", out)  # the list is shown before asking
        self.assertIn("nothing was converted", out)
        self.assertIn("--split cell", out)
        convert.assert_not_called()
        self.assertFalse((self.tmp / "out").exists())

    def test_no_answer_writes_nothing(self):
        code, out, convert = self.run_cli()
        self.assertEqual(code, 2)
        convert.assert_not_called()

    def test_dry_run_asks_nothing(self):
        code, out, convert = self.run_cli("--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("Dry run", out)
        convert.assert_not_called()

    def test_yes_then_original(self):
        code, out, convert = self.run_cli(answers=["y", ""])  # Enter = 1, original
        self.assertEqual(code, 0)
        self.assertIn("3 converted", out)
        self.assertEqual(convert.call_args.kwargs["fmt"], "original")
        self.assertTrue((self.tmp / "out" / "recordings.csv").exists())
        self.assertTrue((self.tmp / "out" / "dvdcam-rip.log").exists())

        code, out, convert = self.run_cli("--yes")  # running again skips everything
        self.assertIn("Nothing to do", out)
        convert.assert_not_called()

    def test_yes_then_h264_uses_its_own_folder(self):
        code, out, convert = self.run_cli(answers=["y", "x", "2"], output=False)
        self.assertEqual(code, 0)
        self.assertEqual(convert.call_args.kwargs["fmt"], "h264")
        self.assertIn("Format: H.264", out)
        self.assertEqual(len(list((self.tmp / "default-h264").glob("*.mp4"))), 3)

    def test_format_flag_skips_the_question(self):
        code, out, convert = self.run_cli("--format", "h264", answers=["y"])
        self.assertEqual(code, 0)
        self.assertEqual(convert.call_args.kwargs["fmt"], "h264")

    def test_other_format_in_folder_is_not_skipped_or_overwritten(self):
        self.run_cli("--yes")  # originals into out/
        code, out, convert = self.run_cli("--yes", "--format", "h264")
        self.assertEqual(code, 2)
        convert.assert_not_called()
        self.assertEqual((self.tmp / "out" / "Recording 01 (13s).mp4").read_text(), "original")


if __name__ == "__main__":
    unittest.main()
