# Research: splitting DVD camcorder discs losslessly

## Do we need to reverse engineer the camera?

No. A finalized disc from a DVD camcorder is plain **DVD-Video**: it has to play in any DVD player, so everything needed to split recordings is in documented on-disc structures. Reverse engineering firmware would not give us anything the disc doesn't already say.

The one place that needs format detective work is **recording timestamps** (see below), and that means analysing sample discs per brand, not the firmware.

## Disc formats we'll meet

| Mode | Folder | Files | Notes |
|---|---|---|---|
| DVD-Video (finalized DVD-R/-RW/+R) | `VIDEO_TS/` | `VIDEO_TS.IFO`, `VTS_01_0.IFO` (+`.BUP`), `VTS_01_1.VOB`, `_2.VOB`… | Main target. Plays in any player. |
| DVD-VR (DVD-RW/-RAM in VR mode) | `DVD_RTAV/` | `VR_MANGR.IFO`, `VR_MOVIE.VRO` | Different but documented format; see the open-source `dvd-vr` tool. |
| Unfinalized DVD-Video | — | no IFO yet | Fallback: scan the stream's NAV packs. |

## How DVD-Video maps recordings

- `VTS_xx_0.IFO` holds the title set's **program chains (PGCs)**. Each PGC lists **programs** (chapters) and **cells**, and each cell has a start and end sector and a playback time.
- The `VOB` files are a single MPEG-2 program stream cut at 1 GB. VOB file boundaries do **not** match recordings.
- Each record/stop press usually becomes one cell or program, and so one chapter of a single title. Some models write one title per recording instead. The IFO tells us which.
- Every VOBU (~0.4–1 s) starts with a **NAV pack** (PCI + DSI) that carries its VOB ID and cell ID, so the split points can also be found by scanning the stream.
- Cells start on VOBU boundaries, which start with a closed GOP, so the cuts are clean and need no re-encoding.
- Timestamps (SCR/PTS) restart or jump between cells. That needs handling, either by FFmpeg's `dvdvideo` demuxer or by regenerating timestamps when remuxing.

## What's in the streams

- Video: MPEG-2, 720×480 (NTSC) or 720×576 (PAL), interlaced, 4:3 or 16:9, ~4–9 Mbit/s.
- Audio: usually AC-3 (Dolby Digital) stereo, sometimes LPCM, rarely MPEG-1 Layer II.

## "Without quality loss" = remux, not re-encode

| Target | Lossless? | Plays on |
|---|---|---|
| MP4 with MPEG-2 + AC-3 (`-c copy`) | Yes | VLC, Windows, most TVs and Android. **Not** QuickTime, iOS or Apple Photos. |
| MKV with MPEG-2 + AC-3/LPCM | Yes | VLC, mpv, Plex, most TVs. Holds LPCM, chapters and metadata cleanly. |
| MP4 with H.264/H.265 + AAC | No (visually transparent at high quality) | Everywhere |

The default will be a lossless MP4 remux, with MKV as an option (needed for LPCM audio, unless it's converted to ALAC/FLAC) and an opt-in `--h264` mode for compatibility. Deinterlacing is lossy, so it only belongs in the re-encode mode.

## Building blocks

- **FFmpeg 7.0+ `dvdvideo` demuxer** (libdvdnav/libdvdread): reads the IFO and pulls out one title or chapter range (`-title N -chapter_start A -chapter_end A`), smoothing over cell timestamp jumps. The quickest path to a working v1.
- **Our own IFO parser** (Python `struct`): needed anyway to list recordings, lengths and sector ranges, to make nice output names, and for the DVD-VR and unfinalized fallbacks. The parts we need are small and documented (dvd.sourceforge.net "DVD-Video information", libdvdread's `ifo_types.h`).
- Prior art that confirms the approach: PgcDemux, DVD Decrypter IFO mode, `dvd-vr`.

## Observed on real discs

### Hitachi DZ-MV750MA (finalized DVD-Video, NTSC)

- `VIDEO_TS.IFO` carries the provider ID `DZ-MV750MA (HITACHI)`. That's useful for brand detection.
- One title set, one title, one PGC. **Each recording is one chapter** (program), so `--split chapter` is right.
- Within a recording the camera chops the stream into cells, each with its own VOB ID, in a fixed rhythm of roughly 16 s, 8 s, 21 s, 9 s, 21 s, 9 s… The last cell is whatever is left over.
- The first cell of each recording is non-seamless and the rest are seamless. That's a second, independent signal for where recordings start.
- **Every cell has the STC-discontinuity flag**, including the seamless cells inside a recording. Timestamps restart at every cell, so the remux must rebuild them across cells, not just inside each recording.
- Video is MPEG-2 **704×480** 4:3 (not 720), with AC-3 2.0 audio at 48 kHz.
- Cell sector ranges in the IFO matched the NAV packs (`nv_pck_lbn`, VOB ID, cell ID) for all 99 cells. Following the `vobu_ea` links through the VOB ends exactly at the last cell's last sector.
- No MPEG user data (`00 00 01 B2`) in the first VOBUs, so the recording date (if stored at all) isn't in the obvious place.

## Open questions (need real discs)

1. **Recording date and time.** DVD-Video has no standard field for it. Brands hide it in vendor-specific places (private streams, MPEG user data, or DV-style packs in the NAV/RDI data). Goal: name files `2007-08-14_15-32-10.mp4` and write `creation_time` metadata. Needs sample discs from each brand.
2. **One title per clip vs. chapters per clip**: which pattern each brand uses.
3. Whether any models split one recording across cells (e.g. at a VOB boundary or a seamless-branching point), so that a "recording" is really a program rather than a cell.

## Plan

1. Collect disc images (ISO, or a copy of `VIDEO_TS`) into `samples/` (git-ignored).
2. IFO parser + `--list`: show titles, programs, cells, durations and sector ranges.
3. Remux each recording with FFmpeg's `dvdvideo` demuxer → MP4/MKV, `-c copy`.
4. Check output against the source: frame counts, durations, and stream hashes after demux.
5. DVD-VR (`VR_MANGR.IFO` / `VR_MOVIE.VRO`) support.
6. Timestamp extraction for each brand → file names + metadata.
7. Optional `--h264` re-encode mode; unfinalized-disc fallback by NAV-pack scanning.
