#!/usr/bin/env python3
"""Build the x86 VM ramdisk: the Googlebook's own ramdisks plus virtio modules.

  make_initrd_x86.py LAPIS_RAW BOOT_DIR VIRT_MODULES_DIR OUT

virtio-pci and virtio-blk come from the Googlebook's own system_dlkm partition; the GPU, input
and network modules come from Android CI's kernel_virt_x86_64 build of the same kernel.
"""
import hashlib, json, stat, struct, subprocess, sys
from pathlib import Path
import cpio_tools

raw, boot, virt, out = map(Path, sys.argv[1:5])
u = lambda b, o: struct.unpack_from('<I', b, o)[0]
gpt = json.loads((boot / 'gpt.json').read_text())


def lp_partitions(base):
    with raw.open('rb') as f:
        f.seek(base + 4096); g = f.read(4096); assert u(g, 0) == 0x616c4467
        f.seek(base + 12288); m = f.read(u(g, 40)); assert u(m, 0) == 0x414c5030
    hs = u(m, 8); tables = m[hs:hs + u(m, 44)]
    def table(off):
        start, count, size = struct.unpack_from('<III', m, off)
        return [tables[start + i * size:start + (i + 1) * size] for i in range(count)]
    extents = [struct.unpack_from('<QIQI', e) for e in table(92)]; res = {}
    for p in table(80):
        name = p[:36].split(b'\0')[0].decode(); _, start, num, _ = struct.unpack_from('<IIII', p, 36)
        if num == 1: res[name] = base + extents[start][2] * 512
    return res


def ls(offset, path):
    p = subprocess.run(['dump.erofs', f'--offset={offset}', '--path=' + path, '--ls', str(raw)], capture_output=True, text=True)
    rows = [l.split() for l in p.stdout.splitlines()]
    return [(r[2], r[1]) for r in rows if len(r) == 3 and r[0].isdigit() and r[2] not in ('.', '..')]


def cat(offset, path):
    return subprocess.run(['dump.erofs', f'--offset={offset}', '--path=' + path, '--cat', str(raw)], capture_output=True, check=True).stdout


def find(offset, root, wanted, depth=0):
    res = {}
    for name, kind in ls(offset, root):
        if kind == '2' and depth < 6: res.update(find(offset, root + '/' + name, wanted, depth + 1))
        elif name in wanted: res[name] = root + '/' + name
    return res


dlkm = lp_partitions(gpt['super'][0])['system_dlkm_a']
own = ['virtio_pci_legacy_dev.ko', 'virtio_pci_modern_dev.ko', 'virtio_pci.ko', 'virtio_blk.ko', 'virtio_console.ko']
added = ['virtio_dma_buf.ko', 'virtio-gpu.ko', 'virtio_input.ko', 'failover.ko', 'net_failover.ko', 'virtio_net.ko', 'virtio-rng.ko']
deps = {'virtio_pci.ko': ['virtio_pci_legacy_dev.ko', 'virtio_pci_modern_dev.ko'], 'virtio-gpu.ko': ['virtio_dma_buf.ko'],
        'net_failover.ko': ['failover.ko'], 'virtio_net.ko': ['net_failover.ko', 'failover.ko']}
paths = find(dlkm, '/lib/modules', set(own)); assert set(paths) == set(own), paths
e = {'lib': (stat.S_IFDIR | 0o755, b''), 'lib/modules': (stat.S_IFDIR | 0o755, b'')}; manifest = {}
for n in own + added:
    b = cat(dlkm, paths[n]) if n in own else (virt / n).read_bytes(); assert b[:4] == b'\x7fELF', n
    e['lib/modules/' + n] = (stat.S_IFREG | 0o644, b)
    manifest[n] = {'source': 'Googlebook system_dlkm ' + paths[n] if n in own else 'Android CI kernel_virt_x86_64 16302624', 'sha256': hashlib.sha256(b).hexdigest()}
order = own + added
e['lib/modules/modules.dep'] = (stat.S_IFREG | 0o644, ''.join(
    f'/lib/modules/{n}:' + ''.join(' /lib/modules/' + d for d in deps.get(n, [])) + '\n' for n in order).encode())
for n in ['modules.alias', 'modules.softdep', 'modules.options']: e['lib/modules/' + n] = (stat.S_IFREG | 0o644, b'')
e['lib/modules/modules.load'] = (stat.S_IFREG | 0o644, ('\n'.join(order) + '\n').encode())
e['lib/modules/modules.load.recovery'] = e['lib/modules/modules.load']
data = (boot / 'vendor_0_platform.cpio').read_bytes() + (boot / 'init_ramdisk.cpio').read_bytes() + cpio_tools.write(e)
blob = subprocess.check_output(['lz4', '-l', '-c'], input=data)
out.write_bytes(blob); out.with_suffix('.json').write_text(json.dumps(manifest, indent=1) + '\n')
v = (boot / 'vbmeta_a.bin').read_bytes(); assert v[:4] == b'AVB0'
size = 256 + struct.unpack_from('>QQ', v, 12)[0] + struct.unpack_from('>QQ', v, 12)[1]
print(out, len(blob)); print('vbmeta size', size, 'sha256', hashlib.sha256(v[:size]).hexdigest())

