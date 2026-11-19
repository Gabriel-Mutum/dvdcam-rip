# dvdcam-rip

Turn the discs from DVD camcorders (Hitachi DZ-MV, Sony DCR-DVD Handycams, Panasonic, Canon…) into **one MP4 file per recording**, with **no quality loss**.

A camcorder disc stores all your clips as one long MPEG-2 stream cut into 1 GB `VOB` pieces. `dvdcam-rip` reads the disc's `IFO` map to find where each recording starts and ends, and copies each one into its own MP4. Nothing is re-encoded: every video frame and every bit of audio is exactly as the camera recorded it.

## Quick start (Windows)

1. Install [Python 3.11+](https://www.python.org/downloads/) and FFmpeg:
   ```bash
   winget install Gyan.FFmpeg
   ```
2. Download this repository.
3. **Drag your disc folder (the one with `VIDEO_TS.IFO` / `VTS_01_1.VOB`, or the DVD drive itself) onto `dvdcam-rip.bat`.**

It lists the recordings it found and asks before converting anything:

```
Camera:  DZ-MV750MA (HITACHI)
Video:   MPEG-2 704x480 NTSC 4:3
Audio:   AC-3 2ch 48 kHz

Found 12 recordings, 22m38s in total, 983.4 MB:

    #    Length       Size  Chapter  File
    1        9s     6.2 MB      1.1  Recording 01 (9s).mp4
    3     1m59s    86.2 MB      1.3  Recording 03 (1m59s).mp4
    ...

Does this look right? Convert 12 recordings? [Y/n] y

Which format?
  1  Original (MPEG-2)  Exact copy of the disc, done in seconds. Won't play on iPhone/Mac.
  2  H.264              Plays everywhere, iPhone included. Re-encoded (looks the same), takes minutes.
Choose 1 or 2 [1]: 1

Output folder: C:\Users\you\Downloads\dvdcam-rip\Video Camera
Format: original MPEG-2. The video is copied as-is, the exact quality of the disc.
The original disc files are only read, never changed.

[ 3/12] Recording 03 (1m59s).mp4  done  84.4 MB  (0.7s)
...
Finished in 10s: 12 converted.

Your videos are in:
  C:\Users\you\Downloads\dvdcam-rip\Video Camera
```

Answering **n** writes nothing and suggests how to look closer (`--list --split cell`) or pick recordings (`--only`).

## Which format?

| | Original (MPEG-2) | H.264 |
|---|---|---|
| Quality | Bit-identical to the disc | Re-encoded at visually transparent quality (measured SSIM 0.98, PSNR 43 dB) |
| Plays on | VLC, Windows, Android, most TVs | Everything, including iPhone, iPad, Mac and messaging apps |
| Speed | Seconds per disc | Minutes per disc (about 3× real time on a 16-thread PC) |
| Picture | 704×480 interlaced, as recorded | Deinterlaced to smooth 59.94 fps, square pixels (640×480 for 4:3), AAC audio |

Keep the original as your master copy and make H.264 copies for sharing. Each format gets its own folder (`…\Video Camera` and `…\Video Camera (H.264)`), and the tool never mixes them in one folder.

## Command line

```bash
python -m pip install -e .              # once, from the repository folder
dvdcam-rip "D:\"                        # list, confirm, convert
dvdcam-rip "D:\" --list                 # only list the recordings
dvdcam-rip "D:\" --dry-run              # show what would be created
dvdcam-rip "D:\" --only 3,5-7           # convert some recordings
dvdcam-rip "D:\" -o "E:\Home videos"    # choose the output folder
dvdcam-rip "D:\" --format h264          # skip the format question
dvdcam-rip "D:\" --yes --open           # no questions (original format); open the folder at the end
```

Other options: `--overwrite`, `--split chapter|vob|cell`, `--ffmpeg PATH`, `--quiet`, `--verbose`, `--json` (with `--list`). See `dvdcam-rip --help`.

## Where the files go

`Downloads\dvdcam-rip\<disc name>\` (or `<disc name> (H.264)\`), where the disc name is the copied folder's name (or the DVD's volume label). That folder also gets:

- `recordings.csv`: every file with its length, size, source chapter and status.
- `dvdcam-rip.log`: technical details (FFmpeg commands and messages) for troubleshooting.

## Safety

- The disc files are opened **read-only**, and nothing is ever written inside the disc folder. The tool refuses an output folder there.
- **Existing files are never overwritten** (unless you pass `--overwrite`). They count as already converted, so you can stop with Ctrl+C and run the same command again to continue.
- Each video is written as `….mp4.part` and only gets its real name once FFmpeg has finished and the result has been checked (video and audio present, length matches the disc). An interrupted run never leaves a broken file that looks complete.
- Free disk space is checked before starting.

## How it works

See [docs/research.md](docs/research.md). In short: the IFO lists every recording's cells and their sectors. The camera restarts the timestamps at every cell, so dvdcam-rip shifts each cell's timestamps to follow on from the previous one, using the cell timings in the disc's NAV packs (as a DVD player does), then lets FFmpeg copy the streams into MP4 with `-c copy`. The MP4 is written without an edit list, because FFmpeg's default one would hide the two frames camcorders place before each recording's first keyframe. LPCM audio, which MP4 can't hold as-is, is stored as FLAC (also lossless).

The H.264 mode feeds the same stitched stream to FFmpeg with `bwdif` deinterlacing (one frame per field), square-pixel scaling, `libx264 -preset medium -crf 18` and AAC 192 kb/s.

## Status

- Finalized DVD-Video discs: working, in both formats. The original mode is verified frame-for-frame on a Hitachi DZ-MV750MA disc.
- Not yet: DVD-VR discs (`DVD_RTAV`), unfinalized discs, ISO images (mount them first), recording dates in file names.

## Development

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
python -m unittest discover -s tests
```

Tests build small synthetic IFO files (`tests/ifo_builder.py`), since real discs can't be committed. Put test discs in `samples/` (git-ignored). Home videos never go in the repo.
