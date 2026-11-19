"""Recordings: what becomes one output file each.

DVD-Video discs: how a camcorder maps record/stop presses onto the DVD
structure varies by brand, so there are three ways to split:

- "chapter" (default): one recording per chapter (program) of each title.
  Most camcorders write each clip as a chapter of one title, or as its own
  one-chapter title; both come out right this way.
- "vob": one recording per run of cells sharing a VOB ID.
- "cell": one recording per cell, the finest split possible.

DVD-VR discs list their recordings ("programs") directly, and unfinalized
discs are split by scanning (see scan.py), so --split only applies to
finalized DVD-Video discs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from .ifo import SECTOR, Cell, Disc, Vts
from .media import SpanReader

SplitMode = Literal["chapter", "vob", "cell"]
SPLIT_MODES: tuple[SplitMode, ...] = ("chapter", "vob", "cell")


@dataclass
class Recording:
    index: int  # 1-based, disc order
    cells: list[Cell]
    reader: SpanReader | None = None  # where the cells' sectors are read from
    stitch: bool = True  # cells have NAV packs and restart their timestamps
    date: datetime | None = None  # when it was recorded, if the disc says
    origin: str = ""  # where on the disc it came from, for the list
    seconds: float | None = None  # overrides the cells' durations
    title: int = 0
    chapter: int = 0
    vts: int = 0
    pgc: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        """Length in seconds; 0 when unknown."""
        if self.seconds is not None:
            return self.seconds
        return sum(c.duration.total_seconds for c in self.cells)

    @property
    def size_bytes(self) -> int:
        return sum(c.sectors for c in self.cells) * SECTOR

    @property
    def vob_ids(self) -> list[int]:
        return sorted({c.vob_id for c in self.cells})

    @property
    def first_sector(self) -> int:
        return self.cells[0].first_sector

    @property
    def last_sector(self) -> int:
        return self.cells[-1].last_sector


def list_recordings(disc: Disc, split: SplitMode = "chapter",
                    readers: dict[int, SpanReader] | None = None) -> list[Recording]:
    if split not in SPLIT_MODES:
        raise ValueError(f"split must be one of {SPLIT_MODES}")
    readers = readers or {}
    out: list[Recording] = []
    for title in disc.vmg.titles:
        vts = disc.title_sets[title.vts]
        for chapter, pgc_no, cells in _chapters(vts, title.vts_title):
            for group in _split(cells, split):
                out.append(
                    Recording(
                        index=len(out) + 1,
                        cells=group,
                        reader=readers.get(title.vts),
                        origin=f"ch {title.number}.{chapter}",
                        title=title.number,
                        chapter=chapter,
                        vts=title.vts,
                        pgc=pgc_no,
                    )
                )
    return out


def _chapters(vts: Vts, vts_title: int):
    """Yield (chapter number, PGC number, cells) for each chapter of a title."""
    chapters = vts.chapters.get(vts_title)
    if chapters:
        for ch in chapters:
            pgc = vts.pgcs[ch.pgc - 1]
            program = pgc.programs[ch.program - 1]
            yield ch.number, pgc.number, program.cells
        return
    # No chapter table: fall back to the programs of the title's entry PGC.
    for pgc in vts.pgcs:
        if pgc.entry and pgc.title == vts_title:
            for program in pgc.programs:
                yield program.number, pgc.number, program.cells
            return


def _split(cells: list[Cell], split: SplitMode) -> list[list[Cell]]:
    if not cells:
        return []
    if split == "chapter":
        return [cells]
    if split == "cell":
        return [[c] for c in cells]
    groups: list[list[Cell]] = [[cells[0]]]
    for c in cells[1:]:
        if c.vob_id == groups[-1][-1].vob_id:
            groups[-1].append(c)
        else:
            groups.append([c])
    return groups
