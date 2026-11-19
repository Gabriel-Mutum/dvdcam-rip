"""Read-only access to disc contents: a folder, an ISO image, or a raw disc.

Everything above this module asks for two things:
- the files in a directory (VIDEO_TS or DVD_RTAV), as FileRefs;
- sector ranges of those files (or of a whole image/disc), via SpanReader.

ISO images are read without mounting them: UDF (what DVD-RAM/-RW camcorders
write, and the main file system of DVD-Video discs) is tried first, then the
ISO 9660 bridge. Every file is only ever opened with mode "rb".
"""

from __future__ import annotations

import os
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

SECTOR = 2048
CHUNK_SECTORS = 512  # 1 MB reads


class MediaError(ValueError):
    pass


@dataclass(frozen=True)
class FileRef:
    """A file inside a folder or an image: where its bytes live."""

    name: str
    container: Path  # the file to open (the file itself, or the image)
    extents: tuple[tuple[int, int], ...]  # (byte offset in container, length)

    @property
    def size(self) -> int:
        return sum(length for _, length in self.extents)

    def read(self, limit: int | None = None) -> bytes:
        out = bytearray()
        with open(self.container, "rb") as f:
            for offset, length in self.extents:
                f.seek(offset)
                out += f.read(length)
                if limit is not None and len(out) >= limit:
                    break
        return bytes(out[:limit] if limit is not None else out)


@dataclass(frozen=True)
class Span:
    container: Path
    offset: int  # bytes
    sectors: int


class SpanReader:
    """Several byte ranges read as one sector-addressed, read-only range."""

    def __init__(self, spans: list[Span]):
        self.spans = [s for s in spans if s.sectors > 0]
        self.total_sectors = sum(s.sectors for s in self.spans)

    @classmethod
    def of_files(cls, refs: list[FileRef]) -> "SpanReader":
        spans = []
        for ref in refs:
            for offset, length in ref.extents:
                spans.append(Span(ref.container, offset, length // SECTOR))
        return cls(spans)

    def read(self, first: int, count: int) -> Iterator[bytes]:
        if first < 0 or first + count > self.total_sectors:
            raise MediaError(
                f"sectors {first}-{first + count - 1} are past the end of the data "
                f"({self.total_sectors} sectors); the disc copy may be incomplete"
            )
        sector, remaining, start = first, count, 0
        for span in self.spans:
            if remaining == 0:
                break
            if sector >= start + span.sectors:
                start += span.sectors
                continue
            with open(span.container, "rb") as f:  # read-only, always
                f.seek(span.offset + (sector - start) * SECTOR)
                n = min(remaining, start + span.sectors - sector)
                while n:
                    take = min(n, CHUNK_SECTORS)
                    data = f.read(take * SECTOR)
                    if len(data) != take * SECTOR:
                        raise MediaError(f"{span.container.name} ended early")
                    yield data
                    sector += take
                    remaining -= take
                    n -= take
            start += span.sectors

    def sector(self, n: int) -> bytes:
        return b"".join(self.read(n, 1))


# ------------------------------------------------------------- volumes


class Volume:
    """Something with directories of files: a folder tree or an image."""

    label = ""

    def directory(self, name: str) -> dict[str, FileRef] | None:
        """Files in the top-level directory `name` (case-insensitive), keyed
        by upper-case file name, or None if there is no such directory."""
        raise NotImplementedError


class FolderVolume(Volume):
    def __init__(self, root: Path):
        self.root = root
        self.label = root.name

    def directory(self, name: str) -> dict[str, FileRef] | None:
        candidates = [self.root] if self.root.name.upper() == name else []
        if self.root.is_dir():
            candidates += [p for p in self.root.iterdir() if p.is_dir() and p.name.upper() == name]
        for folder in candidates:
            files = {
                p.name.upper(): FileRef(p.name, p, ((0, p.stat().st_size),))
                for p in folder.iterdir()
                if p.is_file()
            }
            return files
        return None

    def loose_files(self) -> dict[str, FileRef]:
        """Files directly in the folder (copied discs without a VIDEO_TS folder)."""
        return {
            p.name.upper(): FileRef(p.name, p, ((0, p.stat().st_size),))
            for p in self.root.iterdir()
            if p.is_file()
        }


def _u16le(b: bytes, o: int) -> int:
    return struct.unpack_from("<H", b, o)[0]


def _u32le(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


def _u64le(b: bytes, o: int) -> int:
    return struct.unpack_from("<Q", b, o)[0]


class _Image:
    def __init__(self, path: Path):
        self.path = path
        self.f = open(path, "rb")
        self.f.seek(0, os.SEEK_END)
        self.size = self.f.tell()

    def read(self, sector: int, count: int = 1) -> bytes:
        self.f.seek(sector * SECTOR)
        return self.f.read(count * SECTOR)

    def close(self) -> None:
        self.f.close()


class Iso9660Volume(Volume):
    """The ISO 9660 file system (DVD-Video discs carry one as a bridge)."""

    def __init__(self, path: Path):
        self.path = path
        img = _Image(path)
        try:
            for s in range(16, 32):
                d = img.read(s)
                if len(d) < SECTOR or d[1:6] != b"CD001":
                    raise MediaError("no ISO 9660 file system")
                if d[0] == 1:
                    break
                if d[0] == 255:
                    raise MediaError("no ISO 9660 primary volume descriptor")
            self.label = d[40:72].decode("ascii", "replace").strip()
            root = d[156:190]
            self._root = (_u32le(root, 2), _u32le(root, 10))
            self._img = img
            self._top = self._list(*self._root)
        except BaseException:
            img.close()
            raise

    def _list(self, lba: int, size: int) -> dict[str, tuple[bool, list[tuple[int, int]]]]:
        data = self._img.read(lba, (size + SECTOR - 1) // SECTOR)[:size]
        out: dict[str, tuple[bool, list[tuple[int, int]]]] = {}
        o = 0
        while o < len(data):
            n = data[o]
            if n == 0:
                o = (o // SECTOR + 1) * SECTOR  # records never span sectors
                continue
            rec = data[o:o + n]
            flags = rec[25]
            name_len = rec[32]
            raw = rec[33:33 + name_len]
            o += n
            if raw in (b"\x00", b"\x01"):
                continue
            name = raw.decode("ascii", "replace").split(";")[0].rstrip(".").upper()
            extent = (_u32le(rec, 2) * SECTOR, _u32le(rec, 10))
            if name in out and not out[name][0]:
                out[name][1].append(extent)  # multi-extent file
            else:
                out[name] = (bool(flags & 2), [extent])
        return out

    def directory(self, name: str) -> dict[str, FileRef] | None:
        entry = self._top.get(name)
        if not entry or not entry[0]:
            return None
        lba, size = entry[1][0][0] // SECTOR, entry[1][0][1]
        return {
            n: FileRef(n, self.path, tuple(ext))
            for n, (is_dir, ext) in self._list(lba, size).items()
            if not is_dir
        }

    def close(self) -> None:
        self._img.close()


class UdfVolume(Volume):
    """UDF 1.02-2.01 (no metadata partition), as written by DVD recorders."""

    def __init__(self, path: Path):
        self.path = path
        img = _Image(path)
        self._img = img
        try:
            avdp = img.read(256)
            if len(avdp) < SECTOR or _u16le(avdp, 0) != 2:
                raise MediaError("no UDF anchor")
            vds_len, vds_loc = _u32le(avdp, 16), _u32le(avdp, 20)
            self.partitions: dict[int, int] = {}
            fsd = None
            self.block = SECTOR
            for s in range(vds_loc, vds_loc + max(1, vds_len // SECTOR)):
                d = img.read(s)
                tag = _u16le(d, 0)
                if tag == 5:  # partition descriptor
                    self.partitions[_u16le(d, 22)] = _u32le(d, 188)
                elif tag == 6:  # logical volume descriptor
                    self.block = _u32le(d, 212)
                    self.label = _dstring(d[84:212])
                    fsd = (_u32le(d, 252), _u16le(d, 256))  # long_ad: lbn, partition
                    maps = d[440:440 + _u32le(d, 264)]
                    self._maps = _partition_maps(maps)
                elif tag == 8:
                    break
            if self.block != SECTOR or fsd is None or not self.partitions:
                raise MediaError("unsupported UDF layout")
            fsd_data = self._block(fsd[1], fsd[0])
            if _u16le(fsd_data, 0) != 256:
                raise MediaError("no UDF file set descriptor")
            root = fsd_data[400:416]
            self._root_icb = (_u32le(root, 4), _u16le(root, 8))
            self._top = self._dir(self._root_icb)
        except BaseException:
            img.close()
            raise

    def _abs(self, part_ref: int, lbn: int) -> int:
        number = self._maps.get(part_ref, part_ref)
        if number not in self.partitions:
            raise MediaError("UDF partition not found")
        return self.partitions[number] + lbn

    def _block(self, part_ref: int, lbn: int, count: int = 1) -> bytes:
        return self._img.read(self._abs(part_ref, lbn), count)

    def _entry(self, icb: tuple[int, int]) -> tuple[bool, int, list[tuple[int, int]], bytes]:
        """Read a (extended) file entry: (is_dir, size, extents, embedded data)."""
        lbn, part = icb
        d = self._block(part, lbn)
        tag = _u16le(d, 0)
        if tag == 261:  # file entry
            size, l_ea, l_ad, ad0 = _u64le(d, 56), _u32le(d, 168), _u32le(d, 172), 176
        elif tag == 266:  # extended file entry
            size, l_ea, l_ad, ad0 = _u64le(d, 56), _u32le(d, 208), _u32le(d, 212), 216
        else:
            raise MediaError(f"unexpected UDF descriptor {tag}")
        is_dir = d[16 + 11] == 4  # ICB tag file type 4 = directory
        ad_type = _u16le(d, 16 + 18) & 7
        ads = d[ad0 + l_ea:ad0 + l_ea + l_ad]
        extents: list[tuple[int, int]] = []
        if ad_type == 3:
            return is_dir, size, [], ads[:size]
        step = 8 if ad_type == 0 else 16
        for o in range(0, len(ads) - step + 1, step):
            length = _u32le(ads, o) & 0x3FFFFFFF
            kind = _u32le(ads, o) >> 30
            if length == 0:
                break
            if kind == 3:
                raise MediaError("fragmented UDF directories aren't supported")
            pos = _u32le(ads, o + 4)
            ref = part if ad_type == 0 else _u16le(ads, o + 8)
            extents.append((self._abs(ref, pos) * SECTOR, length))
        return is_dir, size, extents, b""

    def _dir(self, icb: tuple[int, int]) -> dict[str, tuple[bool, tuple[int, int]]]:
        is_dir, size, extents, embedded = self._entry(icb)
        data = embedded or b"".join(_read_extent(self._img, e) for e in extents)
        out = {}
        o = 0
        while o + 38 <= len(data):
            if _u16le(data, o) != 257:
                break
            chars, l_fi, l_iu = data[o + 18], data[o + 19], _u16le(data, o + 36)
            child = (_u32le(data, o + 24), _u16le(data, o + 28))
            name = _dstring_raw(data[o + 38 + l_iu:o + 38 + l_iu + l_fi])
            o += (38 + l_iu + l_fi + 3) & ~3
            if chars & 0x0C or not name:  # deleted or parent
                continue
            out[name.upper()] = (bool(chars & 0x02), child)
        return out

    def directory(self, name: str) -> dict[str, FileRef] | None:
        entry = self._top.get(name)
        if not entry or not entry[0]:
            return None
        files = {}
        for n, (is_dir, icb) in self._dir(entry[1]).items():
            if is_dir:
                continue
            _, size, extents, _ = self._entry(icb)
            # trim the last extent to the file size
            trimmed, left = [], size
            for off, length in extents:
                take = min(length, left)
                if take > 0:
                    trimmed.append((off, take))
                left -= take
            files[n] = FileRef(n, self.path, tuple(trimmed))
        return files

    def close(self) -> None:
        self._img.close()


def _read_extent(img: _Image, extent: tuple[int, int]) -> bytes:
    off, length = extent
    img.f.seek(off)
    return img.f.read(length)


def _partition_maps(maps: bytes) -> dict[int, int]:
    """Partition reference -> partition number (type 1 maps only)."""
    out, o, ref = {}, 0, 0
    while o + 2 <= len(maps):
        kind, length = maps[o], maps[o + 1]
        if length == 0:
            break
        if kind == 1:
            out[ref] = _u16le(maps, o + 4)
        ref += 1
        o += length
    return out


def _dstring_raw(b: bytes) -> str:
    if not b:
        return ""
    if b[0] == 8:
        return b[1:].decode("latin-1")
    if b[0] == 16:
        return b[1:].decode("utf-16-be", "replace")
    return ""


def _dstring(field: bytes) -> str:
    n = field[-1]
    return _dstring_raw(field[:n]).strip("\x00 ") if n else ""


def open_image(path: Path) -> Volume:
    errors = []
    for cls in (UdfVolume, Iso9660Volume):
        try:
            vol = cls(path)
        except (MediaError, struct.error, OSError) as e:
            errors.append(f"{cls.__name__}: {e}")
            continue
        if vol.directory("VIDEO_TS") is not None or vol.directory("DVD_RTAV") is not None:
            return vol
        vol.close()  # type: ignore[attr-defined]
    raise MediaError("no VIDEO_TS or DVD_RTAV folder in the image (" + "; ".join(errors) + ")")


def raw_device(path: str) -> Path | None:
    """On Windows, "D:\\" -> "\\\\.\\D:" (the whole disc, read sector by sector)."""
    p = Path(path)
    if os.name == "nt" and p.anchor and str(p) in (p.anchor, p.anchor.rstrip("\\")) and len(p.drive) == 2:
        return Path("\\\\.\\" + p.drive)
    return None


def whole_file(path: Path) -> SpanReader:
    """The whole image (or raw disc) as sectors, for scanning."""
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    if size <= 0:  # raw devices don't report a size: find it by probing
        size = _probe_size(path)
    return SpanReader([Span(path, 0, size // SECTOR)])


def _probe_size(path: Path) -> int:
    with open(path, "rb") as f:
        lo, hi = 0, 1
        while _readable(f, hi):
            lo, hi = hi, hi * 2
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if _readable(f, mid):
                lo = mid
            else:
                hi = mid
    return (lo + 1) * SECTOR if lo or _readable_at0(path) else 0


def _readable(f, sector: int) -> bool:
    try:
        f.seek(sector * SECTOR)
        return len(f.read(SECTOR)) == SECTOR
    except OSError:
        return False


def _readable_at0(path: Path) -> bool:
    with open(path, "rb") as f:
        return _readable(f, 0)
