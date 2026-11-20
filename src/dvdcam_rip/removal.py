"""Remove dvdcam-rip (and optionally FFmpeg) from this PC, and guide Python removal.

What can be removed, and how:
- dvdcam-rip installed with pip: `python -m pip uninstall dvdcam-rip`.
- dvdcam-rip downloaded as a folder (ZIP): that folder is deleted. A folder
  that is a git repository (someone's working copy) is never deleted
  automatically; the user is told to delete it themselves.
- FFmpeg, only when it was installed with winget: `winget uninstall <id>`.
  Any other FFmpeg is left alone and its location is shown.
- Python is never removed automatically (it may be used by other programs,
  and it's what is running dvdcam-rip); Windows' Installed apps page is
  opened with instructions instead.

Converted videos and disc files are never touched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

PACKAGE = "dvdcam-rip"


@dataclass
class Install:
    pip: bool  # installed as a package (pip / pip install git+...)
    folder: Path | None  # the downloaded dvdcam-rip folder, if running from one
    folder_is_git: bool = False

    @property
    def found(self) -> bool:
        return self.pip or self.folder is not None


def find_folder(module_file: str | Path) -> Path | None:
    """The dvdcam-rip folder (with pyproject.toml and src/dvdcam_rip) that this
    code is running from, or None (e.g. when installed in site-packages)."""
    root = Path(module_file).resolve().parents[2]
    pyproject = root / "pyproject.toml"
    if not (root / "src" / "dvdcam_rip").is_dir() or not pyproject.is_file():
        return None
    try:
        if 'name = "dvdcam-rip"' not in pyproject.read_text(encoding="utf-8"):
            return None
    except OSError:
        return None
    return root


def detect_install(module_file: str | Path | None = None) -> Install:
    module_file = module_file or Path(__file__).with_name("__init__.py")
    try:
        metadata.distribution(PACKAGE)
        pip = True
    except metadata.PackageNotFoundError:
        pip = False
    folder = find_folder(module_file)
    return Install(pip, folder, bool(folder and (folder / ".git").exists()))


def winget_package(ffmpeg: Path | None) -> str | None:
    """The winget package id of this ffmpeg.exe, e.g. "Gyan.FFmpeg", or None."""
    if ffmpeg is None:
        return None
    try:
        path = Path(ffmpeg).resolve()
    except OSError:
        path = Path(ffmpeg)
    parts = path.parts
    for i, part in enumerate(parts[:-1]):
        if part.lower() == "packages" and i > 0 and parts[i - 1].lower() == "winget":
            name = parts[i + 1]
            return name.split("_Microsoft.Winget.Source")[0] if "_Microsoft.Winget.Source" in name else None
    return None


@dataclass
class Step:
    status: str  # "done", "failed", "skipped", "next"
    text: str


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600, creationflags=flags)


def remove_script(install: Install, run=_run) -> list[Step]:
    steps: list[Step] = []
    if install.pip:
        r = run([sys.executable, "-m", "pip", "uninstall", "-y", PACKAGE])
        if r.returncode == 0:
            steps.append(Step("done", "dvdcam-rip package uninstalled (pip uninstall dvdcam-rip)"))
        else:
            detail = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["unknown error"]
            steps.append(Step("failed", f"pip couldn't uninstall dvdcam-rip: {detail[0]}"))
    if install.folder is not None:
        if install.folder_is_git:
            steps.append(Step("skipped", f"{install.folder} is a git working copy (it may hold your own "
                                         "changes), so it wasn't deleted. Delete it yourself if you want."))
        else:
            steps.append(delete_folder(install.folder))
    if not steps:
        steps.append(Step("skipped", "couldn't tell how dvdcam-rip was installed; nothing was removed"))
    return steps


def delete_folder(folder: Path) -> Step:
    try:
        os.chdir(Path.home())  # don't hold the folder open ourselves
    except OSError:
        pass
    failed: list[str] = []

    def note(_func, path, _exc):
        failed.append(str(path))

    if sys.version_info >= (3, 12):
        shutil.rmtree(folder, onexc=note)
    else:  # pragma: no cover
        shutil.rmtree(folder, onerror=note)
    if not folder.exists():
        return Step("done", f"dvdcam-rip folder deleted: {folder}")
    leftovers = [p for p in folder.rglob("*")] if folder.exists() else []
    if not leftovers:
        return Step("done", f"dvdcam-rip files deleted. The empty folder {folder} is still open in another "
                            "window; delete it after closing that window.")
    return Step("failed", f"some files in {folder} couldn't be deleted (in use?): {failed[0] if failed else leftovers[0]}")


def remove_ffmpeg(ffmpeg: Path | None, run=_run) -> Step:
    if ffmpeg is None:
        return Step("skipped", "FFmpeg wasn't found on this PC, so there was nothing to remove")
    package = winget_package(ffmpeg)
    if os.name != "nt" or not package or shutil.which("winget") is None:
        return Step("skipped", f"FFmpeg at {ffmpeg} wasn't installed with winget, so it was left alone. "
                               "Remove it the way it was installed if nothing else uses it.")
    r = run(["winget", "uninstall", "--id", package, "-e", "--silent",
             "--disable-interactivity", "--accept-source-agreements"])
    if r.returncode == 0:
        return Step("done", f"FFmpeg removed (winget uninstall {package})")
    detail = (r.stdout or r.stderr).strip().splitlines()[-1:] or ["unknown error"]
    return Step("failed", f"winget couldn't remove FFmpeg ({package}): {detail[0]}")


def guide_python(open_settings=True) -> Step:
    version = sys.version.split()[0]
    where = sys.base_prefix
    store = "WindowsApps" in sys.executable
    if os.name == "nt":
        if open_settings:
            try:
                os.startfile("ms-settings:appsfeatures")  # type: ignore[attr-defined]
            except OSError:
                pass
        source = " (from the Microsoft Store)" if store else ""
        return Step("next", f"Python {version}{source} is in {where}. Settings > Apps > Installed apps is open: "
                            f"search for \"Python\", click ... next to \"Python {version.rsplit('.', 1)[0]}\" "
                            "and choose Uninstall. Only do this if no other program needs Python.")
    hint = "brew uninstall python" if sys.platform == "darwin" else "your package manager"
    return Step("next", f"Python {version} is in {where}. Remove it with {hint} if no other program needs it.")
