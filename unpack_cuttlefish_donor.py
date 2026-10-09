#!/usr/bin/env python3
"""Extract the software KeyMint / nonsecure Gatekeeper donor files from a Cuttlefish image.

Input: an x86_64 Cuttlefish build zip (e.g. aosp_cf_x86_64_only_phone-img-<build>.zip
from ci.android.com). The VM port never redistributes it.

  unpack_cuttlefish_donor.py CUTTLEFISH.zip DONOR_DIR

Produces the layout expected by make_lapis_security_port.py. Everything is read out of
the image without mounting it; needs dump.erofs (erofs-utils), debugfs (e2fsprogs) and lz4.
"""
import argparse
import hashlib
import io
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

MAGIC_EROFS = bytes.fromhex('e2e1f5e0')


def tool(name):
    found = shutil.which(name)
    if not found:
        sys.exit(f'missing tool: {name} (install erofs-utils / e2fsprogs)')
    return found


def unsparse(source, dest):
    """Expand an Android sparse image into a regular file."""
    with open(source, 'rb') as f, open(dest, 'xb') as g:
        magic, major, _hdr, hs, cs, bs, blocks, chunks, _ = struct.unpack('<IHHHHIIII', f.read(28))
        assert magic == 0xed26ff3a and major == 1 and hs >= 28 and cs >= 12
        f.read(hs - 28)
        for _ in range(chunks):
            kind, _reserved, count, total = struct.unpack_from('<HHII', f.read(cs))
            n = count * bs
            if kind == 0xcac1:
                assert total == cs + n
                while n:
                    b = f.read(min(n, 8 << 20))
                    assert b
                    g.write(b)
                    n -= len(b)
            elif kind == 0xcac2:
                fill = f.read(4)
                if fill == bytes(4):
                    g.seek(n, 1)
                else:
                    b = fill * (1 << 18)
                    while n:
                        k = min(n, len(b))
                        g.write(b[:k])
                        n -= k
            elif kind == 0xcac3:
                g.seek(n, 1)
            elif kind == 0xcac4:
                f.read(4)
            else:
                raise ValueError(hex(kind))
        assert g.tell() == blocks * bs
        g.truncate()


def super_partitions(raw):
    """{name: byte offset} for single-extent logical partitions in a super image."""
    u = lambda b, o: struct.unpack_from('<I', b, o)[0]
    with open(raw, 'rb') as f:
        f.seek(4096)
        g = f.read(4096)
        assert u(g, 0) == 0x616c4467, 'not an LP super image'
        max_size = u(g, 40)
        slots = u(g, 44)
        meta = None
        for slot in range(slots * 2):
            f.seek(12288 + slot * max_size)
            m = f.read(max_size)
            if u(m, 0) == 0x414c5030:
                meta = m
                break
        assert meta is not None, 'no LP metadata'
        hs = u(meta, 8)
        tables = meta[hs:hs + u(meta, 44)]
        def table(off):
            s, c, sz = struct.unpack_from('<III', meta, off)
            return [tables[s + i * sz:s + (i + 1) * sz] for i in range(c)]
        extents = [struct.unpack_from('<QIQI', e) for e in table(92)]
        out = {}
        for p in table(80):
            name = p[:36].split(b'\0')[0].decode()
            _, first, num, _ = struct.unpack_from('<IIII', p, 36)
            if num == 1:
                out[name] = extents[first][2] * 512
        return out


def read_fs(image, path):
    """Read one file from an erofs or ext4 filesystem image."""
    with open(image, 'rb') as f:
        f.seek(1024)
        magic = f.read(4)
    if magic == MAGIC_EROFS:
        return subprocess.check_output([tool('dump.erofs'), '--path=' + path, '--cat', str(image)])
    p = subprocess.run([tool('debugfs'), '-R', 'cat ' + path, str(image)], capture_output=True, check=True)
    assert p.stdout, path
    return p.stdout


def apex_payload(package):
    with zipfile.ZipFile(io.BytesIO(package)) as z:
        return z.read('apex_payload.img')


def debugfs_ls(image, path):
    """List entry names in an ext4 filesystem image via debugfs."""
    out = subprocess.run([tool('debugfs'), '-R', 'ls -l ' + path, str(image)],
                         capture_output=True, check=True).stdout.decode()
    names = []
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) >= 7 and parts[-1] not in ('.', '..'):
            names.append(parts[-1])
    return names


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('zip')
    ap.add_argument('out')
    ap.add_argument('--target', default='aosp_cf_x86_64_only_phone-userdebug',
                    help='informational: which Cuttlefish target the zip came from')
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=out) as td:
        td = Path(td)
        with zipfile.ZipFile(a.zip) as z:
            names = z.namelist()
            super_name = next((n for n in names if n.endswith('super.img')), None)
            if not super_name:
                sys.exit('no super.img in the zip; is this a Cuttlefish image?')
            z.extract(super_name, td)
            super_img = td / super_name
        raw = td / 'super.raw'
        unsparse(super_img, raw)
        parts = super_partitions(raw)
        if 'vendor_a' not in parts:
            sys.exit(f'no vendor_a in super (found {sorted(parts)})')
        vend = parts['vendor_a']

        def vendor_file(path):
            return subprocess.check_output([tool('dump.erofs'), f'--offset={vend}', '--path=' + path, '--cat', str(raw)])

        def vendor_ls(path):
            text = subprocess.check_output([tool('dump.erofs'), f'--offset={vend}', '--path=' + path, '--ls', str(raw)]).decode()
            return [ln.split()[-1] for ln in text.splitlines() if ln.strip().startswith(tuple('0123456789')) and ln.split()[-1] not in ('.', '..')]

        apexes = vendor_ls('/apex')
        keymint = next((n for n in apexes if n == 'com.android.hardware.keymint.rust_nonsecure.apex'), None)
        if keymint is None:
            keymint = next((n for n in apexes if 'keymint' in n and 'nonsecure' in n
                            and not any(b in n for b in ('trusty', 'remote', 'guest'))), None)
        gatekeeper = next((n for n in apexes if n == 'com.android.hardware.gatekeeper.nonsecure.apex'), None)
        if keymint is None:
            sys.exit(f'no pure-software KeyMint apex on vendor; apex list: {apexes}')

        # --- KeyMint payload ---
        package = vendor_file('/apex/' + keymint)
        payload = apex_payload(package)
        payload_img = td / 'keymint_payload.img'
        payload_img.write_bytes(payload)
        binary = read_fs(payload_img, '/bin/hw/android.hardware.security.keymint-service.nonsecure')
        if binary[:4] != b'\x7fELF':
            sys.exit('extracted KeyMint service is not an ELF')
        (out / 'keymint-service').write_bytes(binary)
        # Bundle the payload's own libraries so the HAL runs against the exact versions it
        # was built with, regardless of what the target vendor partition ships.
        ldir = out / 'lib64'
        ldir.mkdir(exist_ok=True)
        for lib in debugfs_ls(payload_img, '/lib64'):
            (ldir / lib).write_bytes(read_fs(payload_img, '/lib64/' + lib))
        vdir = out / 'vintf'
        vdir.mkdir(exist_ok=True)
        for name in ['android.hardware.security.keymint-service.xml',
                     'android.hardware.security.secureclock-service.xml',
                     'android.hardware.security.sharedsecret-service.xml']:
            (vdir / name).write_bytes(read_fs(payload_img, '/etc/vintf/' + name))

        # --- Gatekeeper package (optional) ---
        if gatekeeper:
            (out / 'gatekeeper.nonsecure.apex').write_bytes(vendor_file('/apex/' + gatekeeper))

    manifest = {}
    for p in sorted(out.rglob('*')):
        if p.is_file() and p.name != 'donor.json':
            manifest[str(p.relative_to(out))] = hashlib.sha256(p.read_bytes()).hexdigest()
    (out / 'donor.json').write_text(json.dumps({'target': a.target, 'keymint_apex': keymint,
                                                'gatekeeper_apex': gatekeeper, 'files': manifest}, indent=1) + '\n')
    print('donor written to', out)
    for n, h in manifest.items():
        print(f'  {n}  {h[:16]}')


if __name__ == '__main__':
    main()
