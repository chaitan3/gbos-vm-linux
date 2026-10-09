#!/usr/bin/env python3
"""Read and update AOSP dynamic-partition (LP) metadata inside a super partition.

Mirrors the liblp on-disk layout so the lapis security port can relocate the vendor_a
logical partition when the overlaid EROFS no longer fits its original extent, and fix
up every primary/backup metadata copy plus its header/tables checksums.

  lp_tools.py IMAGE SUPER_START [PARTITION]   # probe / verify
"""
import hashlib
import struct
import sys
from pathlib import Path

GEOMETRY_MAGIC = 0x616c4467
METADATA_MAGIC = 0x414c5030
GEOMETRY_OFFSET = 4096
METADATA_BASE = 12288  # first metadata copy, relative to the super start


def _u32(b, o):
    return struct.unpack_from('<I', b, o)[0]


def _align(n, a):
    return (n + a - 1) // a * a


class Super:
    def __init__(self, image, base):
        self.image = Path(image)
        self.base = base
        with self.image.open('rb') as f:
            f.seek(self.base + GEOMETRY_OFFSET)
            g = f.read(GEOMETRY_OFFSET)
        if _u32(g, 0) != GEOMETRY_MAGIC:
            raise ValueError('LP geometry magic missing (not a super partition?)')
        self.geometry = g
        self.max_size = _u32(g, 40)
        self.slot_count = _u32(g, 44)
        self.logical_block = _u32(g, 48)
        self.copies = []
        with self.image.open('rb') as f:
            for s in range(self.slot_count * 2):
                off = self.base + METADATA_BASE + s * self.max_size
                f.seek(off)
                m = bytearray(f.read(self.max_size))
                if _u32(m, 0) == METADATA_MAGIC:
                    self.copies.append([off, m])
        if not self.copies:
            raise ValueError('no LP metadata copies found')
        self._parse(self.copies[0][1])
        # validate every copy
        for off, m in self.copies:
            if not self._valid(m):
                raise ValueError(f'LP metadata copy at {off} failed checksum')

    def _parse(self, m):
        self.header_size = _u32(m, 8)
        self.tables_size = _u32(m, 44)
        self.tables = bytes(m[self.header_size:self.header_size + self.tables_size])
        self.part_off, self.part_count, self.part_entry = struct.unpack_from('<III', m, 80)
        self.ext_off, self.ext_count, self.ext_entry = struct.unpack_from('<III', m, 92)

    def _valid(self, m):
        header = bytearray(m[:self.header_size])
        header[12:44] = bytes(32)
        if hashlib.sha256(bytes(header)).digest() != bytes(m[12:44]):
            return False
        return hashlib.sha256(bytes(m[self.header_size:self.header_size + self.tables_size])).digest() == bytes(m[48:80])

    def _part(self, m, i):
        o = self.header_size + self.part_off + i * self.part_entry
        name = bytes(m[o:o + 36]).split(b'\0')[0].decode()
        attr, first, num, group = struct.unpack_from('<IIII', m, o + 36)
        return name, attr, first, num, group

    def _ext(self, m, i):
        o = self.header_size + self.ext_off + i * self.ext_entry
        return list(struct.unpack_from('<QIQI', m, o))

    def index(self, name):
        m = self.copies[0][1]
        for i in range(self.part_count):
            if self._part(m, i)[0] == name:
                return i
        raise KeyError(name)

    def names(self):
        m = self.copies[0][1]
        return [self._part(m, i)[0] for i in range(self.part_count)]

    def layout(self, name):
        """Absolute [(byte_offset, byte_size)] for a logical partition's extents."""
        m = self.copies[0][1]
        _, _, first, num, _ = self._part(m, self.index(name))
        return [(self.base + self._ext(m, first + k)[2] * 512,
                 self._ext(m, first + k)[0] * 512) for k in range(num)]

    def used_ranges(self):
        m = self.copies[0][1]
        out = []
        for i in range(self.ext_count):
            n, _t, data, _s = self._ext(m, i)
            if n:
                out.append((self.base + data * 512, self.base + (data + n) * 512))
        return out

    def free_offset(self, length, align=1024 * 1024):
        """First aligned offset >= 1MiB past every used extent with room for `length`."""
        used = sorted(self.used_ranges())
        start = _align(max(e for _s, e in used), align)
        for s, e in used:
            if start < e:
                start = _align(e, align)
        return start

    def set_single_extent(self, name, num_sectors, target_sector):
        idx = self.index(name)
        for off, m in self.copies:
            _, _, first, num, _ = self._part(m, idx)
            if num != 1:
                raise ValueError(f'{name} has {num} extents; only single-extent supported')
            struct.pack_into('<QIQI', m, self.header_size + self.ext_off + first * self.ext_entry,
                             num_sectors, 0, target_sector, 0)
            self._resign(m)
            with self.image.open('r+b') as f:
                f.seek(off)
                f.write(m)
                f.flush()

    def _resign(self, m):
        m[48:80] = hashlib.sha256(bytes(m[self.header_size:self.header_size + self.tables_size])).digest()
        header = bytearray(m[:self.header_size])
        header[12:44] = bytes(32)
        m[12:44] = hashlib.sha256(bytes(header)).digest()


def main():
    image, base = Path(sys.argv[1]), int(sys.argv[2])
    s = Super(image, base)
    print(f'super base={base} max_size={s.max_size} slots={s.slot_count} copies={len(s.copies)}')
    want = sys.argv[3] if len(sys.argv) > 3 else None
    for name in s.names():
        lay = s.layout(name)
        total = sum(sz for _, sz in lay)
        if want is None or name == want:
            print(f'{name:24s} extents={len(lay)} size={total} {lay}')


if __name__ == '__main__':
    main()
