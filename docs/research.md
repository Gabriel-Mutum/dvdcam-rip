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
- Each cell's timestamps restart at the same value (vobu_s_ptm ≈ 0.2145 s), and the first GOP of each cell is closed, starting I, B, B (the B-frames display before the I-frame).

## Remux approach: tested options

All measured on a 9-cell recording (3,571 video frames in the raw cell bytes):

| Approach | Frames kept | Length | Notes |
|---|---|---|---|
| FFmpeg `dvdvideo` demuxer (`-title 1 -chapter_start N -chapter_end N`) | 3,569 | 119.05 s, exact timing | Drops the 2 leading B-frames of the recording's first closed GOP. `-trim 0` and `-preindex 1` don't help. It also logs libdvdread warnings about this IFO. |
| Raw cell bytes piped to `-f mpeg` | 3,571 | 126.8 s | FFmpeg guesses at each timestamp reset and leaves gaps. |
| One file per cell + concat demuxer | 3,571 | 119.87 s | Small gaps at each cell join. |
| **Our stitching**: shift each cell's SCR/PTS/DTS by the NAV-pack timings, pipe to `-f mpeg`, `-c copy` | **3,571** | **119.14 s** | Video packets and audio identical to the source, no gaps, A/V in sync. **Used.** |

On the whole disc, all 12 outputs matched the source packet-for-packet (40,735 frames). The cells tile the VOB from sector 0 to the end with no gaps.

### MP4 edit list hides the leading B-frames

FFmpeg's MP4 muxer starts the track's edit list at the first stored sample's composition time. Here the first sample is the I-frame, and the two B-frames that display before it are marked as discarded (`_D_`). The packets were in the file, but players skipped them (3,568 frames shown of 3,571 stored). Writing with `-use_editlist 0` shows them, at the cost of moving the video by under 10 ms against the audio. After the fix each file shows exactly as many frames as decoding the raw disc bytes does (one fewer than stored, which is a property of the camera's stream).

## H.264 mode

`bwdif=mode=send_field:deint=interlaced` → `scale=trunc(iw*sar/2)*2:ih` (square pixels) → `libx264 -preset medium -crf 18`, AAC 192k, `-fps_mode passthrough`.

- On the 2-minute test recording, `slow` took 65 s and `medium` 36 s, for nearly the same size (45.5 vs 46.5 MB). `medium` is used.
- Quality against the same deinterlaced and scaled source: SSIM 0.979, PSNR 43.4 dB on average (minimum 37.5).
- Without `-fps_mode passthrough`, MP4's constant-frame-rate mode duplicated frames at the start.

## Recording dates on the Hitachi DZ-MV750MA (DVD-Video)

Searched the whole disc for anything that could hold the recording date:

- NAV packs: the PCI ISRC field and everything after PCI_GI, and the DSI area after SYNCI (where vendors could put data), are all zero in every cell.
- Streams: only video (0xE0), AC-3 (0xBD/0x80), NAV (0xBF), padding (0xBE) and system headers. No MPEG user data, and no other private streams.
- GOP timecode: elapsed time within the recording (restarts at 00:00:00.00 at each recording), not the time of day.
- IFO and menu VOB: the only text is the provider ID `DZ-MV750MA (HITACHI)`.

**Conclusion:** this camera stores no recording date on DVD-Video discs. Other brands may; their discs would have to be checked the same way.

## Unfinalized discs: scanning without a disc map

`scan.py` finds recordings from the video alone:

1. Find every NAV pack: the PCI header `00 00 01 BF 03 D4 00` at sector offset 0x26, checked with the DSI header at 0x400.
2. Group consecutive VOBUs with the same VOB/cell ID that follow each other (`vobu_ea`) into cells.
3. Start a new recording where the GOP timecode doesn't continue from the previous cell (`|tc − (prev_tc + prev_duration)| > 1.5 s`), or where the sectors aren't contiguous. This works for elapsed-time timecodes (Hitachi) and time-of-day ones. Without any timecode, every cell becomes a recording.
4. Skip VOBUs with button highlights (PCI HLI status ≠ 0): those are menus, which camcorder recordings never have.

**Verified:** on the Hitachi disc with its IFOs removed, scanning finds exactly the disc map's 12 recordings with identical sector ranges (in 2.4 s), and the converted files are identical. Scanning a whole ISO image (menu VOB included) gives the same 12.

## DVD-VR

`dvdvr.py` follows the VR_MANGR.IFO layout as implemented by Pádraig Brady's GPL tool [dvd-vr](https://github.com/pixelb/dvd-vr) (an independent implementation from its description):

- Header `DVD_RTR_VMG0`; program table offset at 256; text encoding at 67; disc info text at 98.
- Program table: VOB formats (60 bytes each), then the program count and one offset per program.
- Each program: a 5-byte recording timestamp (year 14 bits, month 4, day 5, hour 5, minute 6, second 6), the format ID, start/end PTM, then a VOBU map giving its first sector in VR_MOVIE.VRO and the size of each VOBU (low 10 bits).
- A program is one continuous program stream, so it's copied as-is. Lengths come from the PTMs, checked against a plausible bitrate; otherwise the length is unknown until the file is converted.

**Tested on a constructed disc:** three Hitachi recordings were written as a VR_MOVIE.VRO with a matching VR_MANGR.IFO. Dates, names, file times and `creation_time` came out right, and the video was identical to the DVD-Video conversion. This proves the pipeline and that parser and builder agree. A real DVD-RAM/-RW disc is still needed to confirm the layout against an actual recorder.

## ISO images

`media.py` reads UDF (anchor at sector 256, partition and logical volume descriptors, file set descriptor, file entries and extended file entries, short/long allocation descriptors, embedded data) and ISO 9660 (primary volume descriptor, directory records, multi-extent files) without mounting. UDF is tried first, since DVD-RAM discs often have no ISO 9660 bridge.

**Verified** with UDF-only and ISO 9660-only images of the Hitachi disc built by Windows IMAPI2: same recordings, and converting from the ISO gives files identical to converting from the folder.

## Open questions (need real discs)

1. **Recording dates from other brands' DVD-Video discs** (Sony, Panasonic, Canon), if they store them at all.
2. **A real DVD-VR disc**, to confirm the VR_MANGR.IFO layout and the "2 unexplained bytes" before the VOBU map.
3. **Reading an unfinalized disc straight from the drive** (`\\.\D:`); the ISO route works.
4. **One title per clip vs. chapters per clip**: which pattern each brand uses.

## Plan (original)

1. Collect disc images (ISO, or a copy of `VIDEO_TS`) into `samples/` (git-ignored).
2. IFO parser + `--list`: show titles, programs, cells, durations and sector ranges.
3. Remux each recording with FFmpeg's `dvdvideo` demuxer → MP4/MKV, `-c copy`.
4. Check output against the source: frame counts, durations, and stream hashes after demux.
5. DVD-VR (`VR_MANGR.IFO` / `VR_MOVIE.VRO`) support.
6. Timestamp extraction for each brand → file names + metadata.
7. Optional `--h264` re-encode mode; unfinalized-disc fallback by NAV-pack scanning.
