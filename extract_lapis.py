#!/usr/bin/env python3
"""Read the Android boot images out of the Intel Googlebook recovery image (read-only).

  extract_lapis.py LAPIS_RAW OUT
"""
import gzip, hashlib, json, struct, subprocess, sys, zlib
from pathlib import Path
import cpio_tools

raw, out = Path(sys.argv[1]), Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
u = lambda b, o: struct.unpack_from('<I', b, o)[0]
align = lambda n, p: (n + p - 1) // p * p
manifest = {}


def save(name, data):
    (out / name).write_bytes(data)
    manifest[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def unpack(blob):
    if blob.startswith(b'\x1f\x8b'): return gzip.decompress(blob)
    if blob[:4] in (bytes.fromhex('02214c18'), bytes.fromhex('04224d18')):
        return subprocess.check_output(['lz4', '-d', '-c'], input=blob)
    raise ValueError(blob[:8].hex())


with raw.open('rb') as f:
    f.seek(512); h = f.read(512); assert h[:8] == b'EFI PART'
    lba, count, size = struct.unpack_from('<QII', h, 72); f.seek(lba * 512); es = f.read(count * size)
    assert zlib.crc32(es) == u(h, 88)
    parts = {}
    for i in range(count):
        e = es[i * size:(i + 1) * size]
        if not any(e[:16]): continue
        s, t = struct.unpack_from('<QQ', e, 32)
        parts[e[56:128].decode('utf-16le').rstrip('\0')] = [s * 512, (t + 1) * 512]
    blobs = {}
    for n in ['boot_a', 'init_boot_a', 'vendor_boot_a', 'vbmeta_a']:
        s, t = parts[n]; f.seek(s); blobs[n] = f.read(t - s); save(n + '.bin', blobs[n])
(out / 'gpt.json').write_text(json.dumps(parts, indent=1) + '\n')

boot, init, v = [blobs[n] for n in ['boot_a', 'init_boot_a', 'vendor_boot_a']]
assert boot[:8] == init[:8] == b'ANDROID!' and u(boot, 40) == u(init, 40) == 4
kernel = boot[4096:4096 + u(boot, 8)]
save('kernel', kernel)
print('kernel', len(kernel), 'head', kernel[:16].hex(), 'bzImage' if kernel[0x202:0x206] == b'HdrS' else 'not bzImage')
i = 4096 + align(u(init, 8), 4096); save('init_ramdisk.bin', init[i:i + u(init, 12)])
assert v[:8] == b'VNDRBOOT' and u(v, 8) == 4
page, vs, hs, dtb = u(v, 12), u(v, 24), u(v, 2096), u(v, 2100)
ts, count, es, bs = struct.unpack_from('<IIII', v, 2112)
start = align(hs, page); table = start + align(vs, page) + align(dtb, page)
info = []
for j in range(count):
    e = v[table + j * es:table + (j + 1) * es]; size, off, kind = struct.unpack_from('<III', e)
    name = e[12:44].split(b'\0')[0].decode() or 'platform'
    n = f'vendor_{j}_{name}.bin'; save(n, v[start + off:start + off + size]); info.append({'file': n, 'kind': kind, 'bytes': size})
save('vendor_bootconfig.txt', v[table + align(ts, page):table + align(ts, page) + bs])
save('cmdline.txt', boot[44:1580].split(b'\0')[0] + b'\n' + v[28:2076].split(b'\0')[0] + b'\n')
for n in ['init_ramdisk.bin'] + [e['file'] for e in info]:
    data = unpack((out / n).read_bytes()); save(n.replace('.bin', '.cpio'), data)
    entries = cpio_tools.read(data)
    (out / n.replace('.bin', '.list')).write_text(''.join(f'{m:o} {len(b)} {k}\n' for k, (m, b) in entries.items()))
(out / 'fragments.json').write_text(json.dumps(info, indent=1) + '\n')
(out / 'manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')
print(json.dumps(info)); print('dtb bytes', dtb)
print((out / 'cmdline.txt').read_text()); print((out / 'vendor_bootconfig.txt').read_text())

