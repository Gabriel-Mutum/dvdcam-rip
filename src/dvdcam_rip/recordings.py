"""Turn a parsed disc into a list of recordings (one output file each).

How a camcorder maps record/stop presses onto the DVD structure varies by
brand, so there are three ways to split:

- "chapter" (default): one recording per chapter (program) of each title.
  Most camcorders write each clip as a chapter of one title, or as its own
  one-chapter title; both come out right this way.
- "vob": one recording per run of cells sharing a VOB ID. Useful if a model
  puts several clips in one chapter but gives each its own VOB ID.
- "cell": one recording per cell, the finest split possible.

Which one is right for each brand is an open question until we have sample
discs; `--list --split cell` shows the raw structure to decide.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .ifo import SECTOR, Cell, Disc, Vts

SplitMode = Literal["chapter", "vob", "cell"]
SPLIT_MODES: tuple[SplitMode, ...] = ("chapter", "vob", "cell")


@dataclass
class Recording:
    index: int  # 1-based, disc order
    title: int  # disc-wide title number
    chapter: int  # chapter within the title (first chapter covered)
    vts: int
    pgc: int
    cells: list[Cell]

    @property
    def duration_seconds(self) -> float:
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


def list_recordings(disc: Disc, split: SplitMode = "chapter") -> list[Recording]:
    if split not in SPLIT_MODES:
        raise ValueError(f"split must be one of {SPLIT_MODES}")
    out: list[Recording] = []
    for title in disc.vmg.titles:
        vts = disc.title_sets[title.vts]
        for chapter, pgc_no, cells in _chapters(vts, title.vts_title):
            for group in _split(cells, split):
                out.append(
                    Recording(
                        index=len(out) + 1,
                        title=title.number,
                        chapter=chapter,
                        vts=title.vts,
                        pgc=pgc_no,
                        cells=group,
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
