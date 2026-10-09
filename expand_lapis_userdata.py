#!/usr/bin/env python3
"""Grow the userdata partition of a cloned lapis image so the first boot has room.

The captured recovery image ships a ~4 GiB userdata partition, which fills up during a
first boot (dexopt, APEX, overlay state) and stalls system_server with ENOSPC. This moves
the userdata GPT mapping to fresh space past the old image end and relocates the backup
GPT. All other partitions are preserved; run it only on a copy, never the download.

  expand_lapis_userdata.py IMAGE [GiB]   (default 16)

Adapted from the gbos-vm-mac project's expand_mica_userdata.py.
"""
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path


def main():
    p = Path(sys.argv[1]).resolve()
    gib = int(sys.argv[2]) if len(sys.argv) > 2 else 16
    with p.open('r+b') as f:
        oldsize = p.stat().st_size
        f.seek(512)
        h = bytearray(f.read(512))
        assert h[:8] == b'EFI PART', 'not a GPT image'
        hs = struct.unpack_from('<I', h, 12)[0]
        saved = struct.unpack_from('<I', h, 16)[0]
        struct.pack_into('<I', h, 16, 0)
        assert zlib.crc32(bytes(h[:hs])) == saved, 'primary GPT header checksum'
        entries_lba, n, es, crc = struct.unpack_from('<QIII', h, 72)
        assert entries_lba == 2 and es == 128 and n == 128, (entries_lba, n, es)
        f.seek(entries_lba * 512)
        entries = bytearray(f.read(n * es))
        assert zlib.crc32(bytes(entries)) == crc, 'primary GPT entries checksum'
        oldbackup = struct.unpack_from('<Q', h, 32)[0]
        f.seek(oldbackup * 512)
        bh = bytearray(f.read(512))
        assert bh[:8] == b'EFI PART', 'backup GPT missing'
        blba = struct.unpack_from('<Q', bh, 72)[0]
        f.seek(blba * 512)
        assert f.read(n * es) == bytes(entries), 'backup GPT entries differ'

        targets = [i for i in range(n) if entries[i * es + 56:(i + 1) * es].decode('utf-16le').rstrip('\0') == 'userdata']
        assert len(targets) == 1, f'expected one userdata partition, found {len(targets)}'
        i = targets[0]
        oldrange = struct.unpack_from('<QQ', entries, i * es + 32)

        # Place userdata just past the (1 MiB aligned) old image end.
        start = (oldsize + 1048575) // 1048576 * 2048
        size = gib * 1024 ** 3 // 512
        end = start + size - 1
        backup_entries = end + 1
        newlast = backup_entries + n * es // 512
        for k in range(n):
            if k == i or not any(entries[k * es:k * es + 16]):
                continue
            a, b = struct.unpack_from('<QQ', entries, k * es + 32)
            assert b < start, f'partition {k} overlaps new userdata'
        struct.pack_into('<QQ', entries, i * es + 32, start, end)
        struct.pack_into('<Q', h, 32, newlast)
        struct.pack_into('<Q', h, 48, end)
        struct.pack_into('<I', h, 88, zlib.crc32(bytes(entries)))

        def checksum(header):
            struct.pack_into('<I', header, 16, 0)
            struct.pack_into('<I', header, 16, zlib.crc32(bytes(header[:hs])))
            return header

        checksum(h)
        bh = bytearray(h)
        struct.pack_into('<QQ', bh, 24, newlast, 1)
        struct.pack_into('<Q', bh, 72, backup_entries)
        checksum(bh)

        f.truncate((newlast + 1) * 512)
        f.seek(512)
        f.write(h)
        f.seek(1024)
        f.write(entries)
        f.seek(backup_entries * 512)
        f.write(entries)
        f.write(bh)
        f.seek(446)
        mbr = bytearray(f.read(16))
        assert mbr[4] == 0xee, 'protective MBR missing'
        struct.pack_into('<I', mbr, 12, min(newlast, 0xffffffff))
        f.seek(446)
        f.write(mbr)
        f.flush()
        for lba, expected in [(1, h), (newlast, bh)]:
            f.seek(lba * 512)
            assert f.read(512) == expected, f'GPT header at LBA {lba} mismatch'
        f.seek(start * 512)
        assert f.read(4096) == bytes(4096), 'new userdata area not empty'

    report = {'image': str(p), 'old_size': oldsize, 'new_size': p.stat().st_size,
              'old_userdata_lbas': list(oldrange), 'fresh_userdata_lbas': [start, end],
              'userdata_bytes': size * 512,
              'partition_table_sha256': hashlib.sha256(bytes(entries)).hexdigest(),
              'preserved': 'all other partition mappings and original partition contents'}
    (p.parent / 'userdata-expansion.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
