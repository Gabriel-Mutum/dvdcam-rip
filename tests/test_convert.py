import io
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ifo_builder import write_disc  # noqa: E402
from test_ifo import chapters_disc  # noqa: E402

from dvdcam_rip import cli  # noqa: E402
from dvdcam_rip.convert import (  # noqa: E402
    check_output_location,
    file_name,
    parse_only,
    plan_jobs,
    short_duration,
    space_needed,
)
from dvdcam_rip.source import open_source  # noqa: E402
from dvdcam_rip.recordings import Recording  # noqa: E402
from dvdcam_rip.ifo import Cell, Duration  # noqa: E402
from datetime import datetime  # noqa: E402
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
            media = open_source(root)
            self.assertEqual(media.name, "Video Camera")  # folder holding VIDEO_TS
            recs = media.recordings
            self.assertEqual(file_name(recs[1], len(recs)), "Recording 02 (40s).mp4")
            with self.assertRaises(ValueError):
                check_output_location(root / "out", media)
            with self.assertRaises(ValueError):
                check_output_location(root / "VIDEO_TS" / "out", media)
            check_output_location(Path(tmp) / "elsewhere", media)

            out = Path(tmp) / "out"
            jobs = plan_jobs(recs, len(recs), out)
            full = space_needed(jobs, overwrite=False)
            out.mkdir()
            jobs[0].path.write_bytes(b"done")
            self.assertEqual(space_needed(jobs, overwrite=False), full - recs[0].size_bytes)
            self.assertEqual(space_needed(jobs, overwrite=True), full)


class DatedNameTests(unittest.TestCase):
    def rec(self, index, date, seconds):
        cell = Cell(1, 1, 1, Duration.from_seconds(seconds), 0, 9, 0, False, False)
        return Recording(index, [cell], date=date, seconds=seconds or None)

    def test_dated_names_and_collisions(self):
        d = datetime(2008, 7, 4, 10, 15, 30)
        recs = [self.rec(1, d, 9), self.rec(2, d, 9), self.rec(3, None, 0), self.rec(4, d, 0)]
        names = [j.path.name for j in plan_jobs(recs, 4, Path("out"))]
        self.assertEqual(names, [
            "2008-07-04 10-15-30 (9s).mp4",
            "2008-07-04 10-15-30 (9s) #2.mp4",  # same second and length
            "Recording 03.mp4",  # length unknown: left out
            "2008-07-04 10-15-30.mp4",
        ])


class CliFlowTests(unittest.TestCase):
    """The interactive flow: list -> "does this look right?" -> format -> convert.
    FFmpeg is mocked: fake outputs contain the name of their format."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        write_disc(self.tmp / "disc", [chapters_disc()], [(1, 1)])

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *args, answers=None, output=True, extra=()):
        """Run the CLI with scripted answers; once they run out, input ends
        (as if the window were closed), which leaves any menu."""
        def fake_convert(job, *a, fmt="original", **k):
            job.status = "done"
            job.path.write_text(fmt)

        def fake_existing(tools, path):
            return path.read_text()

        script = iter(answers or [])
        self.prompts = []

        def fake_input(prompt=""):
            self.prompts.append(prompt)
            try:
                return next(script)
            except StopIteration:
                raise EOFError from None

        fake_tools = mock.Mock(version="test", ffmpeg=Path("ffmpeg"))
        buf = io.StringIO()
        out_args = ["-o", str(self.tmp / "out")] if output else []
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(cli, "find_tools", return_value=fake_tools))
            stack.enter_context(mock.patch.object(cli, "has_encoder", return_value=True))
            stack.enter_context(mock.patch.object(cli, "existing_format", side_effect=fake_existing))
            stack.enter_context(mock.patch.object(
                cli, "default_output", side_effect=lambda d, f: self.tmp / f"default-{d.name}-{f}"))
            stack.enter_context(mock.patch.object(cli.sys.stdin, "isatty", return_value=True))
            stack.enter_context(mock.patch("builtins.input", side_effect=fake_input))
            convert = stack.enter_context(mock.patch.object(cli, "convert_one", side_effect=fake_convert))
            for patch in extra:
                stack.enter_context(patch)
            stack.enter_context(redirect_stdout(buf))
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
        self.assertEqual(len(list((self.tmp / "default-disc-h264").glob("*.mp4"))), 3)

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


class EndMenuTests(CliFlowTests):
    """What happens after a disc is done: the "What next?" menu."""

    def menu_block(self, out: str) -> str:
        return out[out.rindex("What next?"):]

    def test_menu_options_and_exit_is_last(self):
        code, out, _ = self.run_cli(answers=["y", "1", "4"])
        self.assertEqual(code, 0)
        menu = self.menu_block(out)
        self.assertIn("1  Convert another disc", menu)
        self.assertIn("2  Open the videos folder", menu)
        self.assertIn("3  Remove dvdcam-rip from this PC...", menu)
        self.assertIn("4  Exit", menu)

    def test_no_menu_in_unattended_modes(self):
        for flags in (["--yes"], ["--list"], ["--dry-run"]):
            _, out, _ = self.run_cli(*flags)
            self.assertNotIn("What next?", out, flags)

    def test_menu_after_declining_has_no_open_folder(self):
        _, out, _ = self.run_cli(answers=["n", "3"])
        menu = self.menu_block(out)
        self.assertNotIn("Open the videos folder", menu)
        self.assertIn("3  Exit", menu)

    def test_convert_another_disc(self):
        write_disc(self.tmp / "disc2", [chapters_disc()], [(1, 1)])
        second = f'"{self.tmp / "disc2"}"'  # dragged into the window: quoted
        code, out, convert = self.run_cli(answers=["y", "1", "1", second, "y", "1", "4"], output=False)
        self.assertEqual(code, 0)
        self.assertEqual(convert.call_count, 6)
        self.assertIn(str(self.tmp / "disc2"), out)

    def test_empty_path_goes_back_to_the_menu(self):
        _, out, convert = self.run_cli(answers=["y", "1", "1", "", "4"])
        self.assertEqual(convert.call_count, 3)
        self.assertEqual(out.count("What next?"), 2)

    def test_open_folder(self):
        opener = mock.patch.object(cli, "open_folder")
        with opener as opened:
            self.run_cli(answers=["y", "1", "2", "4"])
        opened.assert_called_once_with(self.tmp / "out")

    def test_remove_cancel_and_default_no(self):
        _, out, _ = self.run_cli(answers=["y", "1", "3", "4", "4"])  # Cancel in the submenu
        self.assertIn("Cancelled. Nothing was removed.", out)
        removed = mock.patch.object(cli, "remove_script")
        with removed as remove:
            _, out, _ = self.run_cli(answers=["y", "1", "3", "1", "", "4"])  # Enter = no
        remove.assert_not_called()
        self.assertIn("Cancelled. Nothing was removed.", out)

    def test_remove_everything_gives_feedback(self):
        from dvdcam_rip.removal import Install, Step
        patches = [
            mock.patch.object(cli, "detect_install", return_value=Install(True, None)),
            mock.patch.object(cli, "winget_package", return_value="Gyan.FFmpeg"),
            mock.patch.object(cli, "remove_script",
                              return_value=[Step("done", "dvdcam-rip package uninstalled")]),
            mock.patch.object(cli, "remove_ffmpeg", return_value=Step("done", "FFmpeg removed")),
            mock.patch.object(cli, "guide_python", return_value=Step("next", "Settings is open")),
        ]
        code, out, _ = self.run_cli(answers=["y", "1", "3", "3", "y", ""], extra=patches)
        self.assertEqual(code, 0)
        self.assertIn("This will remove:", out)
        self.assertIn("the dvdcam-rip package (pip uninstall dvdcam-rip)", out)
        self.assertIn("FFmpeg (winget package Gyan.FFmpeg)", out)
        self.assertIn("Python: Windows Settings will open", out)
        self.assertIn("NOT touched", out)
        self.assertIn("[done]  dvdcam-rip package uninstalled", out)
        self.assertIn("[done]  FFmpeg removed", out)
        self.assertIn("[next]  Settings is open", out)
        self.assertIn("dvdcam-rip has been removed from this PC.", out)
        self.assertIn("Your videos are still in", out)
        self.assertIn("Press Enter to close this window.", self.prompts[-1])


if __name__ == "__main__":
    unittest.main()
