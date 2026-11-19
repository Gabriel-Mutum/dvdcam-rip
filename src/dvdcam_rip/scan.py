"""Find recordings without a disc map, by scanning the video itself.

Unfinalized discs (and discs whose IFO files are lost or damaged) have no
usable IFO, but the video still carries everything needed:

1. Every VOBU starts with a NAV pack at a sector boundary, holding its VOB
   and cell IDs, its length (vobu_ea) and its presentation times.
2. Consecutive VOBUs with the same VOB/cell ID form a cell.
3. Cells are grouped into recordings using the MPEG GOP timecode: within a
   recording it runs on from cell to cell, so a cell whose timecode doesn't
   continue from the previous cell starts a new recording. Camcorders that
   write elapsed time (like Hitachi, which restarts at 00:00:00) and ones
   that write the time of day both work this way. A gap in the sectors
   also starts a new recording.

If the timecode is missing (all zero), every cell becomes its own recording,
which errs on the side of splitting too much rather than merging clips.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ifo import Cell, Duration
from .media import SECTOR, SpanReader
from .recordings import Recording
from .vob import PCI_HEADER, PTS_WRAP, Nav, describe_stream, gop_timecode, parse_nav

TIMECODE_TOLERANCE = 1.5  # seconds
MIN_RECORDING = 0.5  # seconds; shorter ones are menus or stubs


@dataclass
class _Vobu:
    sector: int
    nav: Nav


@dataclass
class ScanResult:
    recordings: list[Recording]
    video: str
    audio: list[str]
    notes: list[str]
    vobus: int


def find_vobus(reader: SpanReader, progress=None) -> list[_Vobu]:
    """Every NAV pack in the data, in order."""
    out: list[_Vobu] = []
    base = 0
    chunk_sectors = 2048  # 4 MB
    while base < reader.total_sectors:
        n = min(chunk_sectors, reader.total_sectors - base)
        data = b"".join(reader.read(base, n))
        pos = 0
        while True:
            i = data.find(PCI_HEADER, pos)
            if i < 0:
                break
            pos = i + 1
            start = i - 0x26
            if start < 0 or start % SECTOR:
                continue
            nav = parse_nav(data[start:start + SECTOR])
            # Menus carry button highlights (PCI HLI status); recordings never do.
            menu = (data[start + 0x2D + 96 + 1] & 0x03) != 0
            if nav is not None and not menu:
                out.append(_Vobu(base + start // SECTOR, nav))
        base += n
        if progress:
            progress(base / reader.total_sectors)
    return out


def _cells(vobus: list[_Vobu]) -> list[list[_Vobu]]:
    cells: list[list[_Vobu]] = []
    for v in vobus:
        if cells:
            prev = cells[-1][-1]
            same = (v.nav.vob_id, v.nav.cell_id) == (prev.nav.vob_id, prev.nav.cell_id)
            adjacent = v.sector == prev.sector + prev.nav.vobu_ea + 1
            if same and adjacent:
                cells[-1].append(v)
                continue
        cells.append([v])
    return cells


def scan(reader: SpanReader, progress=None) -> ScanResult:
    vobus = find_vobus(reader, progress)
    notes: list[str] = []
    if not vobus:
        return ScanResult([], "", [], ["no DVD video found in the data"], 0)

    video, audio = describe_stream(reader, vobus[0].sector)
    fps = 25 if " PAL" in video else 30
    rate = 25.0 if fps == 25 else 29.97

    groups = _cells(vobus)
    cells: list[Cell] = []
    starts_tc: list[float | None] = []
    for g in groups:
        first, last = g[0], g[-1]
        end = min(last.sector + last.nav.vobu_ea, reader.total_sectors - 1)
        seconds = ((last.nav.e_ptm - first.nav.s_ptm) % PTS_WRAP) / 90000
        cells.append(Cell(
            number=len(cells) + 1, vob_id=first.nav.vob_id, cell_id=first.nav.cell_id,
            duration=Duration.from_seconds(seconds, rate),
            first_sector=first.sector, last_sector=end, last_vobu_sector=last.sector,
            seamless=False, stc_discontinuity=True,
        ))
        starts_tc.append(gop_timecode(reader, first.sector, fps))

    has_timecode = any(tc for tc in starts_tc if tc)
    if not has_timecode:
        notes.append("the video has no timecode, so each segment is listed as its own recording")

    runs: list[list[Cell]] = []
    for i, cell in enumerate(cells):
        new = not runs
        if not new:
            prev = cells[i - 1]
            if cell.first_sector != prev.last_sector + 1:
                new = True
            elif not has_timecode:
                new = True
            else:
                tc, prev_tc = starts_tc[i], starts_tc[i - 1]
                if tc is None or prev_tc is None:
                    new = False  # can't tell; keep together
                else:
                    expected = prev_tc + prev.duration.total_seconds
                    new = abs(tc - expected) > TIMECODE_TOLERANCE
        if new:
            runs.append([cell])
        else:
            runs[-1].append(cell)

    recordings = []
    skipped = 0
    for run in runs:
        seconds = sum(c.duration.total_seconds for c in run)
        if seconds < MIN_RECORDING:
            skipped += 1
            continue
        recordings.append(Recording(
            index=len(recordings) + 1, cells=run, reader=reader, stitch=True,
            origin=f"scan {len(recordings) + 1}",
        ))
    if skipped:
        notes.append(f"skipped {skipped} piece(s) shorter than {MIN_RECORDING} s (menus or stubs)")
    return ScanResult(recordings, video, audio, notes, len(vobus))
