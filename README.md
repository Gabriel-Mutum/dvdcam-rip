# dvdcam-rip

Turn the discs from DVD camcorders (Hitachi DZ-MV, Sony DCR-DVD Handycams, Panasonic, Canon…) into **one MP4 file per recording**, either as an exact copy of the disc or as iPhone-friendly H.264.

It reads finalized and **unfinalized** discs, **DVD-VR** discs (DVD-RAM / DVD-RW in VR mode) and **ISO images**. The disc itself or a copy of its files works too. The original files are only ever read, never changed.

**Free and open source (MIT license).** Anyone may use it, copy it, change it, fork it or build on it, including in commercial projects. See [License](#license).

---

## Quick start: everything in one terminal window (Windows)

### 1. Open a terminal

Click **Start**, type **Terminal**, and press **Enter**. Every step below happens in this one window. Type or paste each command and press **Enter**.

### 2. Install Python

```powershell
winget install Python.Python.3.13
```

Answer **Y** if winget asks you to accept its terms. Skip this step if you already have Python.

### 3. Install FFmpeg

```powershell
winget install Gyan.FFmpeg
```

FFmpeg does the actual video copying. Skip this step if you already have it.

### 4. Go to the folder where you want your videos

`ls` lists what's in the current folder, and `cd` moves into another one:

```powershell
ls
cd ~\Videos
```

The videos will be saved here, in `dvdcam-rip output\<disc name>`.

### 5. Install dvdcam-rip

```powershell
py -m pip install https://github.com/Gabriel-Mutum/dvdcam-rip/archive/refs/heads/main.zip
```

This downloads and installs it straight from GitHub (no git needed). `py` is the Python launcher that step 2 installed.

### 6. Run it on your disc

Type `py -m dvdcam_rip`, a space, and where the disc is. That can be the DVD drive, a folder with the copied disc files (`VIDEO_TS` or `DVD_RTAV`), or an `.iso` image:

```powershell
py -m dvdcam_rip "D:\"
```

Tip: type `py -m dvdcam_rip ` (with the space), then drag the disc folder or ISO from File Explorer into the terminal to paste its path.

It lists what's on the disc:

```
dvdcam-rip 0.6.0

Source:  D:\
Type:    DVD-Video
Camera:  DZ-MV750MA (HITACHI)
Video:   MPEG-2 704x480 NTSC 4:3
Audio:   AC-3 2ch 48 kHz
Note:    no recording dates on this disc (DVD-Video has no standard place for them)

Found 12 recordings, 22m38s in total, 983.4 MB:

    #    Length       Size  Source     File
    1        9s     6.2 MB  ch 1.1     Recording 01 (9s).mp4
    2        9s     6.2 MB  ch 1.2     Recording 02 (9s).mp4
    3     1m59s    86.2 MB  ch 1.3     Recording 03 (1m59s).mp4
    ...
   12        3s     2.2 MB  ch 1.12    Recording 12 (3s).mp4
```

### 7. Check the list and answer

```
Does this look right? Convert 12 recordings? [Y/n]
```

- **Enter** or **y**: continue.
- **n**: stop. Nothing is written, and it suggests what to try next:

  ```
  OK, nothing was converted and no files were written.
  If the list isn't what you expected:
    - see every piece of the disc:  py -m dvdcam_rip "D:\" --list --split cell
    - find them from the video:     py -m dvdcam_rip "D:\" --scan
    - convert only some of them:    py -m dvdcam_rip "D:\" --only 3,5-7
    - report a problem:             https://github.com/Gabriel-Mutum/dvdcam-rip/issues
  ```

### 8. Choose the format

```
Which format?
  1  Original (MPEG-2)  Exact copy of the disc, done in seconds. Won't play on iPhone/Mac.
  2  H.264              Plays everywhere, iPhone included. Re-encoded (looks the same), takes minutes.
Choose 1 or 2 [1]:
```

Press **Enter** (or **1**) for the exact copy, or **2** for files that play on iPhones and Macs.

### 9. Wait while it converts

```
Output folder: C:\Users\you\Videos\dvdcam-rip output\DVD_VIDEO
Format: original MPEG-2. The video is copied as-is, the exact quality of the disc.
The original disc files are only read, never changed.

[ 1/12] Recording 01 (9s).mp4  done  6.1 MB  (0.2s)
[ 2/12] Recording 02 (9s).mp4  done  6.1 MB  (0.2s)
[ 3/12] Recording 03 (1m59s).mp4  done  84.4 MB  (0.7s)
...
[12/12] Recording 12 (3s).mp4  done  2.1 MB  (0.2s)

Finished in 11s: 12 converted.

Your videos are in:
  C:\Users\you\Videos\dvdcam-rip output\DVD_VIDEO
A list of the files is in recordings.csv; technical details are in dvdcam-rip.log.
```

If you stop it with **Ctrl+C**, run the same command again: it skips what's already done and continues.

### 10. Choose what's next

```
What next?
  1  Convert another disc
  2  Open the videos folder
  3  Remove dvdcam-rip from this PC...
  4  Exit
```

- **1 · Convert another disc**: drag the next disc folder, DVD drive or ISO into the window and press Enter (or type a path like `D:\`). Steps 7 to 10 repeat.
- **2 · Open the videos folder**: opens it in File Explorer, then shows this menu again.
- **3 · Remove dvdcam-rip from this PC...**: see [Removing dvdcam-rip](#removing-dvdcam-rip). Nothing is removed without asking.
- **4 · Exit**: back to the terminal prompt.

To update dvdcam-rip later, run step 5 again with `--force-reinstall` added after `install`.

---

## Other ways to run it

### Without typing commands: download and drag and drop (Windows)

1. Install Python and FFmpeg (steps 1–3 above, or Python from [python.org](https://www.python.org/downloads/)).
2. On this page, click the green **Code** button → **Download ZIP**, and unzip it, e.g. to `Documents\dvdcam-rip`. (Or `git clone https://github.com/Gabriel-Mutum/dvdcam-rip.git`.)
3. Drag the disc folder, the DVD drive or the ISO onto **`dvdcam-rip.bat`** in that folder. The same list, questions and menu appear in a new window.

The videos are saved **next to the dvdcam-rip folder**, in `dvdcam-rip output\<disc name>` (e.g. `Documents\dvdcam-rip output\Video Camera`), so removing dvdcam-rip never touches them.

### macOS and Linux

Install FFmpeg (`brew install ffmpeg` or `sudo apt install ffmpeg`) and Python 3.9 or newer, then do steps 4–6 using `python3` where they say `py`, with the mounted disc or a copied folder, e.g. `python3 -m dvdcam_rip "/Volumes/DVD_VIDEO"`.

---

## All commands

### Every option

| Option | What it does |
|---|---|
| `SOURCE` | **Required.** The disc (`D:\`), a folder with the copied disc files, a single `.IFO`/`.VOB`/`.VRO` file inside one, or an ISO image. |
| `-o FOLDER`, `--output FOLDER` | Where to put the videos. Default: `dvdcam-rip output\<disc name>` (or `<disc name> (H.264)`) in the current folder, or next to the dvdcam-rip folder when run from a download. See [Where the files go](#where-the-files-go). |
| `--format original` | Exact MPEG-2 copy, without asking. |
| `--format h264` | iPhone-friendly H.264, without asking. |
| `-y`, `--yes` | Ask no questions and show no end menu. Uses the original format unless `--format` is given. |
| `--list` | Only list the recordings, convert nothing. |
| `--json` | Print everything known about the disc as JSON, convert nothing. |
| `--dry-run` | Show the list, output folder and file names, then stop without writing anything. |
| `--only LIST` | Convert only some recordings: `3`, `1,4`, `5-7`, `1,4-6`. |
| `--overwrite` | Redo files that already exist. Without it they're skipped. |
| `--open` | Open the output folder when finished (the end menu also offers this). |
| `--scan` | Ignore the disc map and find recordings by scanning the video. This happens automatically for unfinalized discs. |
| `--split chapter` | Finalized DVD-Video: one file per chapter (default; right for most camcorders). |
| `--split vob` | Finalized DVD-Video: one file per VOB ID. |
| `--split cell` | Finalized DVD-Video: one file per cell, the finest split. |
| `--ffmpeg PATH` | Use this `ffmpeg.exe` if it isn't found automatically. |
| `-q`, `--quiet` | Print less: no progress lines. |
| `-v`, `--verbose` | Print more: segment details and the FFmpeg commands. |
| `--version` | Show the version. |
| `-h`, `--help` | Show the built-in help. |

### Example commands

These use `dvdcam-rip`. If you installed with the quick start, type **`py -m dvdcam_rip`** in its place (e.g. `py -m dvdcam_rip "D:\" --list`). The plain `dvdcam-rip` command only works if Python's scripts folder is on PATH. From a downloaded folder, `dvdcam-rip.bat` takes the same options.

```bash
dvdcam-rip "D:\"                                         # the disc in drive D: (list, ask, convert)
dvdcam-rip "C:\Users\you\Downloads\Video Camera"         # a copied disc folder
dvdcam-rip "C:\Users\you\Downloads\Video Camera\VIDEO_TS"  # the VIDEO_TS folder itself also works
dvdcam-rip "C:\Users\you\Downloads\holiday.iso"          # an ISO image
dvdcam-rip "D:\" --list                                  # just show what's on the disc
dvdcam-rip "D:\" --list --split cell                     # show every segment of the disc
dvdcam-rip "D:\" --json                                  # disc details as JSON
dvdcam-rip "D:\" --dry-run                               # show what would be created
dvdcam-rip "D:\" --only 3                                # only recording 3
dvdcam-rip "D:\" --only 1,4-6                            # recordings 1, 4, 5 and 6
dvdcam-rip "D:\" --format original                       # exact copy, skip the format question
dvdcam-rip "D:\" --format h264                           # iPhone-friendly, skip the format question
dvdcam-rip "D:\" --yes                                   # no questions, exact copy
dvdcam-rip "D:\" --yes --format h264                     # no questions, iPhone-friendly
dvdcam-rip "D:\" -o "E:\Home videos"                     # choose the output folder
dvdcam-rip "D:\" --overwrite                             # redo files that already exist
dvdcam-rip "D:\" --open                                  # open the folder at the end
dvdcam-rip "D:\" --scan                                  # find recordings from the video itself
dvdcam-rip "D:\" --ffmpeg "C:\tools\ffmpeg\bin\ffmpeg.exe"  # use a specific FFmpeg
dvdcam-rip "D:\" --verbose                               # show technical details
dvdcam-rip "D:\" --quiet --yes                           # minimal output (for scripts)
dvdcam-rip --version
dvdcam-rip --help
```

`dvdcam-rip.bat` passes extra options through, so from a terminal `dvdcam-rip.bat "D:\" --format h264` works too.

The end menu appears only when someone is there to answer: never with `--yes`, `--list`, `--json` or `--dry-run`, or when the input isn't a terminal.

### Exit codes (for scripts)

These apply to `py -m dvdcam_rip` and the `dvdcam-rip` command. (`dvdcam-rip.bat` is meant for drag and drop and doesn't pass them on.)

| Code | Meaning |
|---|---|
| 0 | Everything converted (or nothing needed doing) |
| 1 | Some recordings failed; see `recordings.csv` and `dvdcam-rip.log` |
| 2 | Problem with the input or output (disc not found, not enough space, no answer to a question…) |
| 3 | FFmpeg missing, or missing its H.264 encoder |
| 130 | Stopped with Ctrl+C |

---

## Which format?

| | 1 · Original (MPEG-2) | 2 · H.264 |
|---|---|---|
| Quality | Bit-identical to the disc | Re-encoded at visually transparent quality (measured SSIM 0.98, PSNR 43 dB) |
| Plays on | VLC, Windows, Android, most TVs | Everything, including iPhone, iPad, Mac and messaging apps |
| Speed | Seconds per disc | Minutes per disc (about 3× real time on a 16-thread PC) |
| Picture | As recorded: 704×480 or 720×480, interlaced | Deinterlaced to smooth 59.94 fps (50 for PAL), square pixels (640×480 for 4:3), AAC audio |

Keep the original as your master copy and make H.264 copies for sharing. Each format goes in its own folder, and the tool never mixes them.

## Supported discs

| Disc | How recordings are found | Status |
|---|---|---|
| **Finalized DVD-Video** (DVD-R/-RW/+R, plays in DVD players) | The disc map (`VIDEO_TS/*.IFO`); a damaged map falls back to the `.BUP` copy, then to scanning | Verified frame-for-frame on a Hitachi DZ-MV750MA disc |
| **Unfinalized DVD-Video** (no disc map yet) | Scanning the video: navigation packs mark the segments, and the camera's timecode shows where a recording ends | Verified on the same disc with its map removed: identical recordings and output |
| **DVD-VR** (DVD-RAM, DVD-RW in VR mode: `DVD_RTAV/VR_MOVIE.VRO`) | The VR disc map (`VR_MANGR.IFO`), **including recording dates** | Built from the documented layout and tested on a constructed disc; not yet tried on a real recorder disc |
| **ISO images** | Read directly (UDF or ISO 9660), no mounting | Verified with UDF and ISO 9660 images of the Hitachi disc |
| **Unfinalized disc in the drive** (no readable files) | The disc is read sector by sector and scanned | Not yet tried on real hardware. If it fails, make an ISO of the disc and use that |

Copy-protected (CPRM) DVD-VR recordings, usually TV recordings, can't be converted. Camcorder recordings aren't protected.

## Recording dates

When the disc stores the date and time of each recording, it's used to:

- name the files: `2008-07-04 10-15-30 (1m59s).mp4` (two recordings in the same second get `#2`);
- set the MP4's creation time and the file's date, so photo apps sort the videos correctly;
- fill the `Recorded` column in the list and in `recordings.csv`.

**DVD-VR discs store a date for every recording.** DVD-Video discs (the kind that plays in any DVD player) have no standard place for it. The Hitachi DZ-MV750MA, for example, stores no date anywhere on its DVD-Video discs. Those files are named `Recording 01 (9s).mp4` and so on, in recording order.

## Where the files go

Everything goes in a folder called **`dvdcam-rip output`**, with one folder per disc inside: `<disc name>\` for the original format, `<disc name> (H.264)\` for H.264. The disc name is the copied folder's name, the ISO's file name, or the DVD's label.

Where `dvdcam-rip output` is created:

| How you run dvdcam-rip | Where the videos go | Example |
|---|---|---|
| Installed with pip, from a terminal | The folder the terminal is in (where you `cd`'d to) | `C:\Users\you\Videos\dvdcam-rip output\Video Camera` |
| From a downloaded folder (`dvdcam-rip.bat`, ZIP or git clone) | Next to the dvdcam-rip folder | `C:\Users\you\Documents\dvdcam-rip output\Video Camera` |
| Either, if that folder can't be used | Downloads (and it says so) | `C:\Users\you\Downloads\dvdcam-rip output\Video Camera` |

"Can't be used" means it's inside the disc folder itself, a Windows system folder (like `C:\Windows\System32`, where admin terminals start), or read-only (like a DVD drive). `-o FOLDER` always overrides all of this.

Each disc's folder also gets:

- `recordings.csv`: every file with its date, source, length, size and status.
- `dvdcam-rip.log`: technical details (FFmpeg commands and messages) for troubleshooting.

## Safety

- Disc files are opened **read-only**, and nothing is written inside the disc folder. An output folder there is refused.
- **Existing files are never overwritten** unless you pass `--overwrite`. A file already in the chosen format counts as done; a file in the other format stops the run with an explanation.
- Each video is written as `….mp4.part` and renamed only after FFmpeg finishes and the result is checked: video and audio present, the right codec, and a length matching the disc. A crash or Ctrl+C never leaves a broken file that looks complete.
- Free disk space is checked before starting.

## Troubleshooting

| Problem | What to do |
|---|---|
| `py` isn't recognized right after installing Python | Close the terminal, open a new one, and continue from step 4. |
| `dvdcam-rip` isn't recognized | Use `py -m dvdcam_rip` instead (e.g. `py -m dvdcam_rip "D:\"`). It's the same program; Python's scripts folder just isn't on PATH. |
| "FFmpeg is needed but wasn't found" | Run `winget install Gyan.FFmpeg`, or pass `--ffmpeg PATH`. |
| "no DVD video found" | Point it at the folder that contains `VIDEO_TS` or `DVD_RTAV`, at those folders themselves, or at an `.iso`. |
| The list doesn't match what you recorded | Try `--list --split cell` to see every segment, or `--scan` to find recordings from the video. Please [open an issue](https://github.com/Gabriel-Mutum/dvdcam-rip/issues) with the camera model. |
| An unfinalized disc in the drive can't be read | Finalize it in the camera, or make an ISO image of it with a disc imaging tool and use the ISO. |
| The videos don't play on iPhone/Mac | Convert again and choose format **2 · H.264**. |

## Removing dvdcam-rip

dvdcam-rip uses only Python's standard library: it installs no extra Python packages and changes no system settings. The easiest way to remove it is from the end menu: choose **3 · Remove dvdcam-rip from this PC...**

```
Remove what?
  1  dvdcam-rip only
  2  dvdcam-rip and FFmpeg
  3  dvdcam-rip, FFmpeg and Python (Python with your help, in Windows Settings)
  4  Cancel
Choose 1-4: 2

This will remove:
  - the dvdcam-rip folder: C:\Users\you\Documents\dvdcam-rip
  - FFmpeg (winget package Gyan.FFmpeg)
Your converted videos in C:\Users\you\Documents\dvdcam-rip output and your disc files are NOT touched.

Remove these? [y/N] y

Removing...
  [done]  dvdcam-rip folder deleted: C:\Users\you\Documents\dvdcam-rip
  [done]  FFmpeg removed (winget uninstall Gyan.FFmpeg)

dvdcam-rip has been removed from this PC.
Your videos are still in C:\Users\you\Documents\dvdcam-rip output

Press Enter to close this window.
```

What each part does:

- **dvdcam-rip**: deletes the dvdcam-rip folder (from the ZIP or `git clone`), or runs `pip uninstall dvdcam-rip` if it was installed with pip. A git clone is deleted only if it holds nothing of your own. If you've changed or added files, made commits that aren't on GitHub, or stashed changes, the folder is kept, and you're told why so nothing you made is lost:

  ```
  This will be kept:
    - the dvdcam-rip folder C:\Users\you\Documents\dvdcam-rip, because it's a git clone with 2 changed or added files

  There's nothing dvdcam-rip can remove automatically. You can delete what's listed
  above yourself (see "Removing dvdcam-rip" in the README).
  ```
- **FFmpeg**: removed only if it was installed with winget (`winget install Gyan.FFmpeg`, as this README suggests). An FFmpeg installed any other way is left alone, and its location is shown. Choose this only if no other program uses FFmpeg.
- **Python**: never uninstalled automatically, because other programs may need it and it's what runs dvdcam-rip. Windows' *Installed apps* page opens instead, with instructions: search for "Python", click **⋯** next to it, then **Uninstall**.

The confirmation defaults to **No**: pressing Enter by mistake removes nothing. Each step reports `[done]`, `[skipped]` (with the reason) or `[failed]` (with the error). Your videos and disc files are never touched.

**By hand**, if you prefer:

1. dvdcam-rip: delete its folder, or run `py -m pip uninstall dvdcam-rip` if you installed it with pip.
2. FFmpeg: `winget uninstall Gyan.FFmpeg` (only if nothing else uses it).
3. Python: *Settings → Apps → Installed apps → Python 3.x → Uninstall* (only if nothing else uses it).

## How it works

See [docs/research.md](docs/research.md) for the details and measurements. In short:

- **Finding recordings.** The disc map (`IFO`) lists every recording's cells and their sectors. Without a map, the navigation pack at the start of every half second of video gives the same information, and the MPEG timecode shows where one recording ends and the next begins. DVD-VR discs list each recording's sectors and date in `VR_MANGR.IFO`.
- **Lossless copy.** The camera restarts the timestamps at every cell. dvdcam-rip shifts each cell's timestamps to follow on from the previous one, using the timings in the navigation packs (as a DVD player does), and FFmpeg copies the streams into MP4 with `-c copy`. The MP4 has no edit list, which would otherwise hide the two frames that camcorders place before each recording's first keyframe. LPCM audio is stored as FLAC (also lossless).
- **H.264.** The same stream goes through `bwdif` deinterlacing (one frame per field), square-pixel scaling, `libx264 -preset medium -crf 18` and AAC 192 kb/s.

## Development

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
python -m unittest discover -s tests
```

Tests build small synthetic discs (`tests/ifo_builder.py`, `tests/vr_builder.py`, and synthetic NAV packs for scanning), plus ISO images through Windows' IMAPI2 when it's available. Real discs can't be committed. Put test discs in `samples/` (git-ignored); home videos never go in the repo.

## Contributing and forking

You're welcome to fork dvdcam-rip, adapt it to your own needs, or build something new on top of it. Improvements are welcome back too:

- **Your camera works differently?** [Open an issue](https://github.com/Gabriel-Mutum/dvdcam-rip/issues) with the camera model and the output of `dvdcam-rip "<disc>" --list --split cell --verbose`. That's often enough to add support without sharing any video.
- **Pull requests** are welcome. Please keep the safety rules (read-only source, never overwrite) and add a test.

## License

dvdcam-rip is released under the [MIT License](LICENSE): you may use, copy, modify, merge, publish, distribute, sublicense and sell it, for any purpose, as long as the copyright notice and license text are kept. It comes with no warranty.

FFmpeg is a separate program with its own license (LGPL/GPL). dvdcam-rip runs it but doesn't include it.
