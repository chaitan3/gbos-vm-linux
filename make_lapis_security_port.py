#!/usr/bin/env python3
"""Port the gbos-vm-mac "security port" to the x86_64 lapis recovery image.

Rebuilds the vendor_a logical partition with a pure-software (AOSP/Cuttlefish) KeyMint and,
optionally, nonsecure Gatekeeper with no Weaver. It also disables the StrongBox vendor APEX
and patches the first-stage fstab so the modified vendor is not dm-verity verified.

  make_lapis_security_port.py --raw RAW --donor DONOR --out OUT
                              [--locksettings] [--quiet-absent-hardware]
                              [--no-copy] [--no-initrd] [--donor-mode]

Never edits RAW unless --no-copy is given, in which case RAW is patched in place (useful for
sparse test images). Donor layout:

  keymint-service                                   software KeyMint HAL binary (ELF)
  keymint-libcrypto.so                              private libcrypto for it
  vintf/android.hardware.security.keymint-service.xml
  vintf/android.hardware.security.secureclock-service.xml
  vintf/android.hardware.security.sharedsecret-service.xml
  gatekeeper.nonsecure.apex                         (only with --locksettings)
"""
import argparse
import hashlib
import io
import json
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import cpio_tools
import erofs_metadata
import lp_tools

ROOT = Path(__file__).resolve().parent
BLOCK = 4096
FSTAB = 'first_stage_ramdisk/fstab.android-desktop'
STRONGBOX_APEX = 'com.android.hardware.keymint.strongbox.desktop'
STRONGBOX_PROP = f'ro.vendor.apex.{STRONGBOX_APEX}=none'
STRONGBOX_BOOT = f'androidboot.vendor.apex.{STRONGBOX_APEX}=none'
VINTF = ['keymint', 'secureclock', 'sharedsecret']

SOFTWARE_KEYMINT_RC = b'''# Software KeyMint for the VM: not hardware-backed, no StrongBox/Trusty.
service vendor.keymint-default /vendor/bin/hw/android.hardware.security.keymint-service
    class early_hal
    user nobody
    setenv LD_LIBRARY_PATH /vendor/lib64/vm_keymint:/vendor/lib64
'''

ABSENT_HARDWARE_RC = b'''# The VM has no GSC/Trusty: stop these services after their first failure instead of
# letting init restart them (and dump a tombstone) every 5 seconds.
on property:init.svc.android.system.desktop.security.gscd=restarting
    stop android.system.desktop.security.gscd

on property:init.svc.vendor.secretkeeper.trusty=restarting
    stop vendor.secretkeeper.trusty
'''

NEUTERED = {
    'etc/init/android.hardware.gatekeeper-service.trusty.rc':
        b'# Replaced by the AOSP/Cuttlefish nonsecure Gatekeeper APEX (VM has no Trusty).\n',
    'etc/vintf/manifest/android.hardware.gatekeeper-service.trusty.xml':
        b'<manifest version="9.0" type="device"/>\n',
    'etc/init/android.hardware.weaver-service.android-desktop.rc':
        b'# No Weaver hardware in a VM.\n',
    'etc/vintf/manifest/android.hardware.weaver-service.android-desktop.xml':
        b'<manifest version="9.0" type="device"/>\n',
}

# QEMU's virtual GPU lands on PCI 0000:00:01.0 (see boot_vm.sh). Its sysfs nodes must be
# labelled sysfs_gpu or surfaceflinger/gpuservice get denied reading uevent.
GPU_SYSFS = '/sys/devices/pci0000:00/0000:00:01.0'
GRAPHICS_FC_RULE = (r'/sys/devices/pci0000:00/0000:00:01\.0(/.*)? u:object_r:sysfs_gpu:s0' + '\n').encode()
CONSOLE_FC_RULE = b'/dev/ttyS0 u:object_r:console_device:s0\n'

VM_GRAPHICS_RC = b'''# Relabel the emulated GPU subtree (uevent etc.) using the existing sysfs_gpu type.
on init
    restorecon_recursive /sys/devices/pci0000:00/0000:00:01.0
'''

# The stock lapis build.prop targets Intel and enables the DRM color pipeline, which makes the
# (Cuttlefish) drm_hwcomposer reject virtio-gpu planes that lack COLOR_PIPELINE. Turn it off.
# Keep overlay planes ENABLED (the mac port's default): drm_hwcomposer assigns the mouse cursor
# to a device/overlay plane when the cursor plane is unavailable; with overlays disabled that
# falls back to client composition, which is the path that renders the cursor invisible here.
COMPOSER_PROPS = {
    'vendor.hwc.drm.enable_color_pipeline': 'false',
    'ro.vendor.hwc.use_overlay_planes': 'true',
}

# Run SurfaceFlinger/HWUI on Mesa VirGL GLES instead of Vulkan-on-Venus: SF's Vulkan
# render engine imports gralloc buffers through Venus, which aborts on the host's
# vkGetMemoryResourcePropertiesMESA. GLES (VirGL) avoids that path; Venus stays for apps.
VIRGL_PROPS = {
    'ro.hardware.egl': 'virgl',
    'ro.hwui.use_vulkan': 'false',
    'debug.renderengine.backend': 'skiaglthreaded',
    'debug.renderengine.vulkan': 'false',
    'debug.renderengine.graphite_desktop_optin': 'false',
    'debug.hwui.renderer': 'skiagl',
    'ro.gfx.angle.supported': 'false',
    'ro.surface_flinger.has_wide_color_display': 'false',
    'ro.surface_flinger.has_HDR_display': 'false',
}
VIRGL_LIBS = ['libEGL_virgl.so', 'libGLESv1_CM_virgl.so', 'libGLESv2_virgl.so']

# QEMU's usb-wacom-tablet enumerates as a pen/stylus, which Android routes down the stylus path
# (ink trails). Force it to a plain absolute pointer: pointer deviceType, no size/pressure/
# orientation calibration, and no orientation awareness for the fixed desktop display.
WACOM_IDC = b'''# QEMU Wacom PenPartner (usb-wacom-tablet) -> plain absolute pointer, not a stylus.
touch.deviceType = pointer
touch.orientationAware = 0
touch.size.calibration = none
touch.pressure.calibration = none
touch.orientation.calibration = none
'''

DIAGNOSTIC_RC = b'''# Stream a graphics/input-focused logcat to the serial console so crashes are visible.
service vm-diagnostic-log /system/bin/sh -c "exec /system/bin/logcat -b all -v threadtime *:W surfaceflinger:V SurfaceFlinger:V EGL:V libEGL:V RenderEngine:V gralloc:V hwcomposer:V vulkan:V venus:V MESA:V ANGLE:V InputReader:V InputDispatcher:V WindowManager:V DEBUG:F libc:F"
    class core
    user shell
    group shell log readproc
    seclabel u:r:shell:s0
    console ttyS0

on init
    start vm-diagnostic-log
'''


def run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def dump_cat(image, path, offset=0):
    out = subprocess.run(['dump.erofs', f'--offset={offset}', '--path=' + path, '--cat', str(image)],
                         check=True, capture_output=True).stdout
    if not out:
        raise SystemExit(f'empty file in image: {path}')
    return out


def compress_option(superblock):
    algs = superblock[84]  # erofs_super_block u1.available_compr_algs
    if algs & (1 << 0):
        return '-zlz4hc'
    if algs & (1 << 3):
        return '-zzstd'
    return None


def overlay_tar(path, dirs, files):
    with tarfile.open(path, 'w', format=tarfile.PAX_FORMAT) as t:
        for name, (mode, uid, gid, label) in dirs.items():
            ti = tarfile.TarInfo(name)
            ti.type = tarfile.DIRTYPE
            ti.mode = mode & 0o7777
            ti.uid, ti.gid, ti.mtime = uid, gid, 1230768000
            ti.pax_headers = {'SCHILY.xattr.security.selinux': f'u:object_r:{label}:s0'}
            t.addfile(ti)
        for name, (data, label, mode, uid, gid) in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = mode
            ti.uid, ti.gid, ti.mtime = uid, gid, 1230768000
            ti.pax_headers = {'SCHILY.xattr.security.selinux': f'u:object_r:{label}:s0'}
            t.addfile(ti, io.BytesIO(data))


def _label(m):
    return m['xattrs']['security.selinux'].decode().split(':')[2]


def dir_entries(src_fs, files, new_dirs):
    """Every ancestor directory of an added/changed file, with its original metadata.

    mkfs.erofs --tar recreates any path prefix it sees in the tarball; without an explicit
    directory entry that prefix is created with default mode (0777) and no SELinux label,
    which makes the mounted vendor root unsearchable. Re-add every ancestor with the
    mode/uid/gid/label it has in the original image.

    The root '.' is deliberately NOT included: adding it with an xattr trips an
    erofs-utils 1.9.4 incremental bug (spurious ENOSPC). restore_root_xattr() fixes the
    root separately.
    """
    names = set(new_dirs)
    for path in files:
        parts = path.split('/')
        for i in range(1, len(parts)):
            names.add('/'.join(parts[:i]))
    out = {}
    for d in sorted(names, key=lambda s: (s.count('/'), s)):
        if d in new_dirs:
            out[d] = new_dirs[d]
            continue
        md = erofs_metadata.metadata(src_fs, '/' + d)
        out[d] = (md['mode'] & 0o7777, md['uid'], md['gid'], _label(md))
    return out


def _sb_fields(image):
    with image.open('rb') as f:
        f.seek(1024)
        sb = f.read(128)
    return sb, 1 << sb[12], struct.unpack_from('<I', sb, 40)[0], struct.unpack_from('<I', sb, 44)[0], struct.unpack_from('<H', sb, 14)[0]


def _shared_xattr_offset(image, name=b'selinux', value=b'u:object_r:vendor_file:s0'):
    _sb, bs, _meta, xbase, _root = _sb_fields(image)
    with image.open('rb') as f:
        f.seek(xbase * bs)
        region = f.read(1 << 16)
    i = 0
    while i < len(region) - 4:
        nl, idx, vl = struct.unpack_from('<BBH', region, i)
        if idx == 6 and 1 <= nl < 16 and 1 <= vl < 128:
            if region[i + 4:i + 4 + nl] == name and region[i + 4 + nl:i + 4 + nl + vl] == value:
                return i
        i += 4
    raise ValueError('shared vendor_file xattr not found in rebuilt image')


def _crc32c(data, crc=0xFFFFFFFF):
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0x82F63B78 if crc & 1 else 0)
    return crc & 0xFFFFFFFF


def update_sb_checksum(image):
    """Recompute the EROFS superblock CRC32C (covers [1024, blksize) of block 0)."""
    sb, bs, _meta, _xbase, _root = _sb_fields(image)
    feature_compat = struct.unpack_from('<I', sb, 8)[0]
    with image.open('r+b') as f:
        f.seek(1024)
        buf = bytearray(f.read(bs - 1024))
        buf[4:8] = bytes(4)
        crc = _crc32c(bytes(buf))
        if feature_compat & 1:  # EROFS_FEATURE_COMPAT_SB_CHKSUM
            f.seek(1028)
            f.write(struct.pack('<I', crc))


def restore_root_xattr(image, source):
    """Repair the rebuilt root directory: restore its mode/uid/gid and point its inode
    back at the shared SELinux 'vendor_file' entry, then fix the superblock checksum.

    mkfs.erofs --incremental (1.9.4) rewrites the EROFS root inode and drops its shared
    xattr reference (leaving /vendor unlabeled, so vendor_init/ueventd/apexd cannot search
    the mounted root), and resets the root mode to 0777. The shared xattr table itself is
    preserved. The root inode lives in block 0, which the superblock checksum covers, so
    recompute that too.
    """
    sb, bs, meta, _xbase, root_nid = _sb_fields(source)
    src_pos = meta * bs + root_nid * 32
    with source.open('rb') as f:
        f.seek(src_pos)
        src_inode = f.read(96)
    src_fmt = struct.unpack_from('<H', src_inode, 0)[0]
    src_md = erofs_metadata.metadata(source, '/')
    with source.open('rb') as f:
        f.seek(src_pos + (64 if src_fmt & 1 else 32))
        header = bytearray(f.read(12))
    header[4] = 1  # one shared xattr

    _sb2, bs2, meta2, _x2, root_nid2 = _sb_fields(image)
    pos = meta2 * bs2 + root_nid2 * 32
    with image.open('r+b') as f:
        f.seek(pos)
        inode = bytearray(f.read(96))
        fmt = struct.unpack_from('<H', inode, 0)[0]
        struct.pack_into('<H', inode, 4, src_md['mode'] & 0xFFFF)
        if fmt & 1:
            struct.pack_into('<I', inode, 24, src_md['uid'])
            struct.pack_into('<I', inode, 28, src_md['gid'])
        else:
            struct.pack_into('<H', inode, 24, src_md['uid'])
            struct.pack_into('<H', inode, 26, src_md['gid'])
        f.seek(pos)
        f.write(inode)
        off = _shared_xattr_offset(image)
        if off % 4:
            raise ValueError('shared xattr offset not 4-byte aligned')
        f.seek(pos + (64 if fmt & 1 else 32))
        f.write(bytes(header) + struct.pack('<I', off // 4))
    update_sb_checksum(image)


def build_initrd(src, dst):
    cpio = subprocess.run(['lz4', '-d', '-c', str(src)], check=True, capture_output=True).stdout
    entries = cpio_tools.read(cpio)
    if FSTAB not in entries:
        raise SystemExit(f'initrd has no {FSTAB}')
    mode, data = entries[FSTAB]
    lines = []
    for ln in data.decode().splitlines():
        if ln.startswith('vendor '):
            ln = ln.replace(',avb=vbmeta', '')
        lines.append(ln)
    entries[FSTAB] = (mode, ('\n'.join(lines) + '\n').encode())
    blob = subprocess.run(['lz4', '-l', '-c'], input=cpio_tools.write(entries),
                          check=True, capture_output=True).stdout
    Path(dst).write_bytes(blob)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--raw', default=str(ROOT / 'android-desktop_signed_recovery_image.bin'))
    ap.add_argument('--donor', default=str(ROOT / 'donor'))
    ap.add_argument('--out', default=str(ROOT / 'image-security'))
    ap.add_argument('--initrd', default=str(ROOT / 'initrd'))
    ap.add_argument('--image-name', default='googlebook-security.bin')
    ap.add_argument('--locksettings', action='store_true')
    ap.add_argument('--quiet-absent-hardware', action='store_true')
    ap.add_argument('--venus', action='store_true',
                    help='install the Mesa Venus Vulkan ICD and set ro.hardware.vulkan=virtio')
    ap.add_argument('--venus-lib', default=str(ROOT / 'guest-mesa' / 'libvulkan_virtio.so'))
    ap.add_argument('--diagnostics', action='store_true',
                    help='stream a graphics-focused logcat to the serial console')
    ap.add_argument('--composer', action='store_true',
                    help='replace the vendor DRM hwcomposer with the Cuttlefish one (virtio-gpu)')
    ap.add_argument('--virgl', action='store_true',
                    help='install Mesa VirGL GLES (EGL) for SurfaceFlinger/HWUI')
    ap.add_argument('--virgl-dir', default=str(ROOT / 'guest-mesa' / 'gl'))
    ap.add_argument('--vmcompat', action='store_true',
                    help='use the Cuttlefish software boot-control service (no firmware calls)')
    ap.add_argument('--no-copy', action='store_true', help='patch RAW in place instead of copying')
    ap.add_argument('--no-initrd', action='store_true')
    a = ap.parse_args()

    raw, donor, out = Path(a.raw), Path(a.donor), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    gpt = json.loads((ROOT / 'firmware' / 'gpt.json').read_text())
    super_base, super_end = gpt['super']

    need = ['keymint-service'] + [f'vintf/android.hardware.security.{n}-service.xml' for n in VINTF]
    if a.locksettings:
        need.append('gatekeeper.nonsecure.apex')
    libs = sorted((donor / 'lib64').glob('*.so')) if (donor / 'lib64').is_dir() else []
    missing = [n for n in need if not (donor / n).is_file()]
    if not libs:
        missing.append('lib64/*.so')
    if missing:
        raise SystemExit('donor is incomplete, missing: ' + ', '.join(missing) +
                         '\nFetch a Cuttlefish x86_64 image and run:'
                         f'\n  python3 {ROOT / "unpack_cuttlefish_donor.py"} <cuttlefish-x86_64.zip> {donor}')

    # --- locate vendor_a and its EROFS ---
    sup = None
    target = raw
    if not a.no_copy:
        target = out / a.image_name
        if target.exists():
            target.unlink()
        run(['cp', '--reflink=auto', '--sparse=always', str(raw), str(target)])
    sup = lp_tools.Super(target, super_base)
    vendor = sup.layout('vendor_a')
    if len(vendor) != 1:
        raise SystemExit(f'vendor_a is not single-extent: {vendor}')
    vendor_off, vendor_size = vendor[0]

    tmp = Path(tempfile.mkdtemp(prefix='lapis-security-', dir=str(out)))
    src_fs = tmp / 'vendor.erofs'
    with target.open('rb') as f:
        f.seek(vendor_off + 1024)
        sb = f.read(128)
        if sb[:4] != bytes.fromhex('e2e1f5e0'):
            raise SystemExit('vendor_a is not EROFS')
        build_time = struct.unpack_from('<Q', sb, 24)[0]
        blocks = struct.unpack_from('<I', sb, 36)[0]
        fs_size = blocks << sb[12]
        zopt = compress_option(sb)
        f.seek(vendor_off)
        remaining = fs_size
        with src_fs.open('wb') as g:
            while remaining:
                b = f.read(min(remaining, 1 << 22))
                if not b:
                    break
                g.write(b)
                remaining -= len(b)
    if remaining:
        raise SystemExit(f'short read of vendor_a: {remaining} bytes missing')

    # --- overlay contents ---
    files = {}
    dirs = {}

    def add(path, data, label, mode=0o644, uid=0, gid=0):
        files[path] = (data, label, mode, uid, gid)

    add('bin/hw/android.hardware.security.keymint-service', (donor / 'keymint-service').read_bytes(),
        'hal_keymint_default_exec', 0o755)
    dirs['lib64/vm_keymint'] = (0o755, 0, 2000, 'vendor_file')
    for lib in libs:
        add('lib64/vm_keymint/' + lib.name, lib.read_bytes(), 'vendor_file')
    add('etc/init/vm-software-keymint.rc', SOFTWARE_KEYMINT_RC, 'vendor_configs_file')
    for n in VINTF:
        add(f'etc/vintf/manifest/vm-android.hardware.security.{n}-service.xml',
            (donor / 'vintf' / f'android.hardware.security.{n}-service.xml').read_bytes(), 'vendor_configs_file')
    add('usr/idc/Vendor_056a_Product_0000.idc', WACOM_IDC, 'vendor_idc_file')

    if a.venus:
        venus = Path(a.venus_lib)
        if not venus.is_file():
            raise SystemExit(f'--venus needs {venus}; build it with ./build_guest_mesa.sh')
        add('lib64/hw/vulkan.virtio.so', venus.read_bytes(), 'same_process_hal_file')

    if a.virgl:
        gd = Path(a.virgl_dir)
        for n in VIRGL_LIBS + ['libgallium_dri.so']:
            pth = gd / n
            if not pth.is_file():
                raise SystemExit(f'--virgl needs {pth}; build it with ./build_guest_mesa.sh')
        if not _exists(src_fs, 'lib64/egl'):
            dirs['lib64/egl'] = (0o755, 0, 2000, 'same_process_hal_file')
        for n in VIRGL_LIBS:
            add('lib64/egl/' + n, (gd / n).read_bytes(), 'same_process_hal_file')
        add('lib64/libgallium_dri.so', (gd / 'libgallium_dri.so').read_bytes(), 'same_process_hal_file')

    prop = dump_cat(src_fs, '/build.prop').decode()
    overrides = {}
    if a.venus:
        # Point the Vulkan loader at the Venus ICD (it loads hw/vulkan.<ro.hardware.vulkan>.so).
        overrides['ro.hardware.vulkan'] = 'virtio'
    if a.composer:
        overrides.update(COMPOSER_PROPS)
    if a.virgl:
        overrides.update(VIRGL_PROPS)
    lines = [l for l in prop.splitlines() if l.split('=', 1)[0] not in overrides]
    lines += [f'{k}={v}' for k, v in overrides.items()]
    prop = '\n'.join(lines) + '\n'
    prop += '\n# The VM has no StrongBox hardware.\n' + STRONGBOX_PROP + '\n'
    m = erofs_metadata.metadata(src_fs, '/build.prop')
    add('build.prop', prop.encode(), _label(m), m['mode'] & 0o7777, m['uid'], m['gid'])

    # GPU sysfs label, Cuttlefish composer, and optional serial logcat diagnostics.
    fc_extra = b''
    if a.venus:
        fc_extra += GRAPHICS_FC_RULE
        add('etc/init/vm-graphics.rc', VM_GRAPHICS_RC, 'vendor_configs_file')
    if a.composer:
        cad = donor / 'composer'
        if not (cad / 'hwc3-service.drm').is_file():
            raise SystemExit(f'--composer needs {cad}/hwc3-service.drm; re-run unpack_cuttlefish_donor.py')
        add('bin/hw/android.hardware.composer.hwc3-service.drm',
            (cad / 'hwc3-service.drm').read_bytes(), 'hal_graphics_composer_default_exec', 0o755)
        rp = 'lib64/drm_hwcomposer_atom_reporter.so'
        rp_data = (cad / 'drm_hwcomposer_atom_reporter.so').read_bytes()
        if _exists(src_fs, rp):
            rm = erofs_metadata.metadata(src_fs, '/' + rp)
            add(rp, rp_data, _label(rm), rm['mode'] & 0o7777, rm['uid'], rm['gid'])
        else:
            add(rp, rp_data, 'same_process_hal_file')
    if a.vmcompat:
        bs = donor / 'boot-service'
        if not bs.is_file():
            raise SystemExit(f'--vmcompat needs {bs}; re-run unpack_cuttlefish_donor.py')
        bp = 'bin/hw/android.hardware.boot-service.android-desktop'
        if _exists(src_fs, bp):
            bm = erofs_metadata.metadata(src_fs, '/' + bp)
            add(bp, bs.read_bytes(), _label(bm), bm['mode'] & 0o7777, bm['uid'], bm['gid'])
        else:
            add(bp, bs.read_bytes(), 'hal_bootctl_default_exec', 0o755)
    if a.diagnostics:
        fc_extra += CONSOLE_FC_RULE
        add('etc/init/vm-diagnostic-log.rc', DIAGNOSTIC_RC, 'vendor_configs_file')
    if fc_extra:
        fm = erofs_metadata.metadata(src_fs, '/etc/selinux/vendor_file_contexts')
        add('etc/selinux/vendor_file_contexts',
            dump_cat(src_fs, '/etc/selinux/vendor_file_contexts') + fc_extra,
            _label(fm), fm['mode'] & 0o7777, fm['uid'], fm['gid'])

    if a.locksettings:
        add('apex/com.android.hardware.gatekeeper.nonsecure.apex',
            (donor / 'gatekeeper.nonsecure.apex').read_bytes(), 'vendor_apex_file')
        for path, data in NEUTERED.items():
            if _exists(src_fs, path):
                mm = erofs_metadata.metadata(src_fs, '/' + path)
                add(path, data, _label(mm), mm['mode'] & 0o7777, mm['uid'], mm['gid'])
            else:
                add(path, data, 'vendor_configs_file')
    if a.quiet_absent_hardware:
        add('etc/init/vm-absent-hardware.rc', ABSENT_HARDWARE_RC, 'vendor_configs_file')

    tar = tmp / 'overlay.tar'
    overlay_tar(tar, dir_entries(src_fs, files, dirs), files)

    # --- incremental EROFS build ---
    new_fs = tmp / 'vendor.new.erofs'
    shutil.copyfile(src_fs, new_fs)
    cmd = ['mkfs.erofs', '--incremental=data', '--tar=f', '-x0', f'-b{BLOCK}', f'-T{build_time}']
    if zopt:
        cmd.append(zopt)
    cmd += [str(new_fs), str(tar)]
    run(cmd, stdout=subprocess.DEVNULL)
    restore_root_xattr(new_fs, src_fs)
    run(['fsck.erofs', str(new_fs)], stdout=subprocess.DEVNULL)
    new_size = new_fs.stat().st_size
    new_bytes = new_fs.read_bytes()

    # --- place vendor ---
    placement = {'mode': 'in-place', 'offset': vendor_off, 'bytes': new_size}
    if new_size <= vendor_size:
        with target.open('r+b') as f:
            f.seek(vendor_off)
            f.write(new_bytes)
            f.write(bytes(vendor_size - new_size))
    else:
        start = sup.free_offset(new_size)
        length = (new_size + 511) // 512 * 512
        if start + length > super_end:
            raise SystemExit('not enough free space in super to relocate vendor_a')
        with target.open('r+b') as f:
            f.seek(start)
            f.write(new_bytes)
            f.write(bytes(length - new_size))
        sup.set_single_extent('vendor_a', length // 512, (start - super_base) // 512)
        placement = {'mode': 'relocated', 'offset': start, 'bytes': length,
                     'sector': (start - super_base) // 512}

    # --- first-stage fstab ---
    if not a.no_initrd:
        build_initrd(Path(a.initrd), out / 'initrd')

    manifest = {
        'raw_source': str(raw),
        'image': str(target),
        'image_name': target.name if not a.no_copy else str(target),
        'vendor': placement,
        'strongbox_boot_arg': STRONGBOX_BOOT,
        'locksettings': a.locksettings,
        'quiet_absent_hardware': a.quiet_absent_hardware,
        'files': {n: {'sha256': hashlib.sha256(d).hexdigest(), 'mode': oct(mode), 'label': label}
                  for n, (d, label, mode, _u, _g) in sorted(files.items())},
    }
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=1) + '\n')
    shutil.rmtree(tmp, ignore_errors=True)
    print(f'wrote {target}')
    print(json.dumps(manifest['vendor'], indent=1))


def _exists(image, path):
    try:
        erofs_metadata.metadata(image, '/' + path)
        return True
    except Exception:
        return False


if __name__ == '__main__':
    main()
