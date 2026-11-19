# dvdcam-rip

Turn the discs from DVD camcorders (Sony DCR-DVD Handycams, Panasonic, Hitachi, Canon…) into **one video file per recording**, with **no quality loss**.

A camcorder disc stores all your clips as one long MPEG-2 stream cut into 1 GB `VOB` pieces. `dvdcam-rip` reads the disc's `IFO` map to find where each recording starts and ends, and remuxes each one into its own MP4 (or MKV) without re-encoding.

> Status: early. The research and plan are in [docs/research.md](docs/research.md).

## Planned usage

```bash
dvdcam-rip D:\ --list              # show the recordings on the disc
dvdcam-rip D:\ -o output           # one lossless file per recording
dvdcam-rip disc.iso -o output --h264   # optional: re-encode for maximum compatibility
```

## Requirements

- Python 3.11+
- FFmpeg 7.0+ on `PATH` (built with libdvdnav/libdvdread for the `dvdvideo` demuxer)

## Development

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

Put test discs in `samples/` (git-ignored). Home videos never go in the repo.
