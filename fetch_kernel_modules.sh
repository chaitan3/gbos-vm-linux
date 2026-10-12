#!/usr/bin/env bash
# Download the kernel modules in ./modules from an Android CI kernel build.
#
#   fetch_kernel_modules.sh [BUILD] [TARGET] [OUTDIR]
#     BUILD   default 16302624
#     TARGET  default kernel_virt_x86_64
#     OUTDIR  default ./modules   (also supplies the list of *.ko names to fetch)
#
# Resolved directly via Google CI REST API raw endpoints to bypass frontend changes.
# Re-runnable; needs access to ci.android.com.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD="${1:-16302624}"
TARGET="${2:-kernel_virt_x86_64}"
OUT="${3:-$HERE/modules}"
mkdir -p "$OUT"
exec python3 - "$BUILD" "$TARGET" "$OUT" <<'PY'
import io
import json
import re
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

build, target, out = sys.argv[1], sys.argv[2], Path(sys.argv[3])
base = f'https://ci.android.com/builds/submitted/{build}/{target}/latest/'
ARCHIVE = re.compile(r'\.(zip|tar\.gz|tgz|tar\.xz|txz|tar\.bz2|tbz2?)$', re.I)
DEFAULT = ['failover.ko', 'net_failover.ko', 'virtio_dma_buf.ko', 'virtio-gpu.ko',
           'virtio_input.ko', 'virtio_net.ko', 'virtio-rng.ko', 'virtio_snd.ko']

want = sorted(p.name for p in out.glob('*.ko')) or DEFAULT
print(f'build {build} target {target}: {len(want)} modules -> {out}')
print('  ' + ' '.join(want))


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'gbos-vm'})
    return urllib.request.urlopen(req, timeout=180).read()


def artifact_url(token):
    """Generate direct signed endpoint URL to bypass dynamically rendered viewer."""
    return f'https://ci.android.com/builds/submitted/{build}/{target}/latest/raw/{token}'


def iter_members(data):
    """Yield (name, bytes) for a zip or compressed tarball."""
    if data[:2] == b'PK':
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for i in z.infolist():
                if not i.is_dir():
                    yield i.filename, z.read(i)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as t:
            for m in t.getmembers():
                if m.isfile():
                    f = t.extractfile(m)
                    yield m.name, f.read()


# --- discover the artifacts listed on the build page ---
tokens = want
remaining = set(want)

# 1) modules published individually
for name in sorted(remaining):
    t = name
    url = artifact_url(t)
    try:
        out.joinpath(name).write_bytes(fetch(url))
        remaining.discard(name)
        print(f'  {name}')
    except Exception:
        # Fallback to archive check if individual file isn't directly at root
        continue

if not remaining:
    print(f'done: {len(want)} modules in {out}')
    sys.exit(0)

# 2) modules bundled in an archive (fallback processing)
archives = [t for t in tokens if ARCHIVE.search(t)]
if archives:
    print(f'  remaining {len(remaining)}; trying archives: {", ".join(archives)}')
for t in archives:
    url = artifact_url(t)
    try:
        data = fetch(url)
        found = {}
        for member, blob in iter_members(data):
            bn = member.rsplit('/', 1)[-1]
            if bn in remaining:
                found[bn] = blob
        for bn, blob in found.items():
            out.joinpath(bn).write_bytes(blob)
            remaining.discard(bn)
        if found:
            print(f'  {t}: extracted {len(found)}')
        if not remaining:
            break
    except Exception:
        continue

if remaining:
    sys.exit('missing modules: ' + ', '.join(sorted(remaining)))
print(f'done: {len(want)} modules in {out}')
PY
