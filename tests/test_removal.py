from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dvdcam_rip import removal  # noqa: E402
from dvdcam_rip.removal import Install, detect_install, find_folder, guide_python, remove_ffmpeg, \
    remove_script, winget_package  # noqa: E402


def fake_checkout(root: Path, git: bool = False) -> Path:
    """A downloaded dvdcam-rip folder; returns the path of its __init__.py."""
    pkg = root / "src" / "dvdcam_rip"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (root / "pyproject.toml").write_text('[project]\nname = "dvdcam-rip"\n')
    (root / "dvdcam-rip.bat").write_text("@echo off\n")
    if git:
        (root / ".git").mkdir()
    return pkg / "__init__.py"


def ok(code=0, out=""):
    return subprocess.CompletedProcess([], code, out, "")


class Temp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self.cwd)
        self._tmp.cleanup()


class DetectTests(Temp):
    def test_finds_downloaded_folder(self):
        init = fake_checkout(self.tmp / "dvdcam-rip-main")
        self.assertEqual(find_folder(init), self.tmp / "dvdcam-rip-main")

    def test_ignores_other_folders(self):
        init = fake_checkout(self.tmp / "x")
        (self.tmp / "x" / "pyproject.toml").write_text('[project]\nname = "something-else"\n')
        self.assertIsNone(find_folder(init))
        site = self.tmp / "Lib" / "site-packages" / "dvdcam_rip"
        site.mkdir(parents=True)
        self.assertIsNone(find_folder(site / "__init__.py"))

    def test_git_working_copy_flagged(self):
        init = fake_checkout(self.tmp / "repo", git=True)
        with mock.patch.object(removal.metadata, "distribution", side_effect=removal.metadata.PackageNotFoundError):
            install = detect_install(init)
        self.assertEqual(install, Install(False, self.tmp / "repo", True))

    def test_winget_package(self):
        p = Path(r"C:\Users\u\AppData\Local\Microsoft\WinGet\Packages"
                 r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe")
        self.assertEqual(winget_package(p), "Gyan.FFmpeg")
        self.assertIsNone(winget_package(Path(r"C:\ffmpeg\bin\ffmpeg.exe")))
        self.assertIsNone(winget_package(None))


class RemoveTests(Temp):
    def test_folder_is_deleted_with_feedback(self):
        init = fake_checkout(self.tmp / "dvdcam-rip")
        steps = remove_script(Install(False, find_folder(init)))
        self.assertEqual([s.status for s in steps], ["done"])
        self.assertIn("folder deleted", steps[0].text)
        self.assertFalse((self.tmp / "dvdcam-rip").exists())

    def test_git_folder_is_kept(self):
        init = fake_checkout(self.tmp / "repo", git=True)
        steps = remove_script(Install(False, find_folder(init), True))
        self.assertEqual(steps[0].status, "skipped")
        self.assertTrue((self.tmp / "repo" / "pyproject.toml").exists())

    def test_pip_uninstall(self):
        run = mock.Mock(return_value=ok())
        steps = remove_script(Install(True, None), run=run)
        self.assertEqual(steps[0].status, "done")
        self.assertEqual(run.call_args.args[0][1:], ["-m", "pip", "uninstall", "-y", "dvdcam-rip"])
        run = mock.Mock(return_value=ok(1, "ERROR: boom"))
        self.assertEqual(remove_script(Install(True, None), run=run)[0].status, "failed")

    def test_ffmpeg_only_with_winget(self):
        run = mock.Mock(return_value=ok())
        step = remove_ffmpeg(Path(r"C:\ffmpeg\bin\ffmpeg.exe"), run=run)
        self.assertEqual(step.status, "skipped")
        run.assert_not_called()
        self.assertEqual(remove_ffmpeg(None, run=run).status, "skipped")

    @unittest.skipUnless(os.name == "nt", "winget is Windows-only")
    def test_ffmpeg_winget_uninstall(self):
        run = mock.Mock(return_value=ok())
        with mock.patch.object(removal.shutil, "which", return_value="winget"), \
                mock.patch.object(removal, "winget_package", return_value="Gyan.FFmpeg"):
            step = remove_ffmpeg(Path("ffmpeg.exe"), run=run)
        self.assertEqual(step.status, "done")
        self.assertEqual(run.call_args.args[0][:4], ["winget", "uninstall", "--id", "Gyan.FFmpeg"])

    def test_python_is_only_guided(self):
        step = guide_python(open_settings=False)
        self.assertEqual(step.status, "next")
        self.assertIn(sys.version.split()[0], step.text)


@unittest.skipUnless(shutil.which("git"), "needs git")
class GitCloneTests(Temp):
    """A clone made just to use dvdcam-rip can go; one with your own work stays."""

    def git(self, *args, cwd=None):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                       env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})

    def clone(self) -> Path:
        """A "GitHub" repo with dvdcam-rip in it, and a fresh clone of it."""
        origin = self.tmp / "origin"
        fake_checkout(origin)
        self.git("init", "-q", cwd=origin)
        self.git("add", ".", cwd=origin)
        self.git("commit", "-q", "-m", "initial", cwd=origin)
        clone = self.tmp / "dvdcam-rip"
        self.git("clone", "-q", str(origin), str(clone))
        return clone

    def install(self, folder: Path) -> Install:
        with mock.patch.object(removal.metadata, "distribution", side_effect=removal.metadata.PackageNotFoundError):
            return detect_install(folder / "src" / "dvdcam_rip" / "__init__.py")

    def test_clean_clone_is_deleted_read_only_files_included(self):
        clone = self.clone()
        install = self.install(clone)
        self.assertEqual(install.git_changes, [])
        self.assertTrue(install.folder_deletable)
        objects = [p for p in (clone / ".git" / "objects").rglob("*") if p.is_file()]
        self.assertTrue(objects)
        for p in objects:  # what git does on Windows; a plain delete would fail
            os.chmod(p, 0o444)
        steps = remove_script(install)
        self.assertEqual([s.status for s in steps], ["done"], steps)
        self.assertFalse(clone.exists())

    def test_clone_with_changes_is_kept(self):
        clone = self.clone()
        (clone / "pyproject.toml").write_text('[project]\nname = "dvdcam-rip"\n# my tweak\n')
        (clone / "notes.txt").write_text("mine")
        install = self.install(clone)
        self.assertEqual(install.git_changes, ["2 changed or added files"])
        steps = remove_script(install)
        self.assertEqual(steps[0].status, "skipped")
        self.assertIn("2 changed or added files", steps[0].text)
        self.assertTrue((clone / "notes.txt").exists())

    def test_unpushed_commit_and_stash_are_kept(self):
        clone = self.clone()
        (clone / "extra.txt").write_text("x")
        self.git("add", ".", cwd=clone)
        self.git("commit", "-q", "-m", "local", cwd=clone)
        (clone / "extra.txt").write_text("y")
        self.git("stash", "-q", cwd=clone)
        install = self.install(clone)
        self.assertEqual(install.git_changes, ["1 commit not pushed", "stashed changes"])
        self.assertFalse(install.folder_deletable)


if __name__ == "__main__":
    unittest.main()
