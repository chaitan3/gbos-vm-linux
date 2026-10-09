#!/usr/bin/env python3
"""Read EROFS inode metadata (mode/uid/gid/mtime) and short-name xattrs.

Read-only helper used when an overlay needs to reuse an existing file's mode and
SELinux label (e.g. /build.prop). Adapted from the on-disk erofs_inode layout;
needs `dump.erofs` on PATH.
"""
import re
import shutil
import struct
import subprocess


def _tool(name='dump.erofs'):
    found = shutil.which(name)
    if not found:
        raise SystemExit(f'missing tool: {name} (install erofs-utils)')
    return found


DUMP = None


def metadata(image, path, offset=0):
    global DUMP
    DUMP = DUMP or _tool()
    text = subprocess.check_output([DUMP, f'--offset={offset}', '--path=' + path, str(image)]).decode()
    nid = int(re.search(r'NID: (\d+)', text)[1])
    with open(image, 'rb') as f:
        def rd(pos, n):
            f.seek(offset + pos)
            return f.read(n)
        sb = rd(1024, 128)
        bs = 1 << sb[12]
        meta, xbase = struct.unpack_from('<II', sb, 40)
        inode = rd(meta * bs + nid * 32, 64)
        fmt, ic, mode = struct.unpack_from('<HHH', inode)
        uid, gid = struct.unpack_from('<II' if fmt & 1 else '<HH', inode, 24)
        epoch = struct.unpack_from('<Q', sb, 24)[0]
        mtime = struct.unpack_from('<Q', inode, 32)[0] if fmt & 1 else epoch + struct.unpack_from('<I', inode, 12)[0]
        x = {}

        def entry(pos):
            nl, idx, vl = struct.unpack('<BBH', rd(pos, 4))
            b = rd(pos + 4, nl + vl)
            prefix = {1: 'user.', 4: 'trusted.', 6: 'security.'}.get(idx)
            assert prefix is not None, idx
            x[prefix + b[:nl].decode()] = b[nl:]
            return (4 + nl + vl + 3) & ~3

        if ic:
            p = (meta * bs + nid * 32) + (64 if fmt & 1 else 32)
            h = rd(p, 12)
            count = h[4]
            for j in range(count):
                entry(xbase * bs + 4 * struct.unpack('<I', rd(p + 12 + j * 4, 4))[0])
            end = p + 12 + (ic - 1) * 4
            p += 12 + count * 4
            while p < end:
                p += entry(p)
    return {'mode': mode, 'uid': uid, 'gid': gid, 'mtime': mtime, 'xattrs': x}


if __name__ == '__main__':
    import sys
    print(metadata(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 0))
