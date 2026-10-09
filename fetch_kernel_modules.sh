#!/usr/bin/env bash
# Download the kernel modules in ./modules from an Android CI kernel build.
#
#   fetch_kernel_modules.sh [BUILD] [TARGET] [OUTDIR]
#     BUILD   default 16302624
#     TARGET  default kernel_virt_x86_64
#     OUTDIR  default ./modules   (also supplies the list of *.ko names to fetch)
#
# Handles both CI layouts: each module published as its own artifact (common for
# kernel builds), or a single zip/tarball containing them. The build page is
# resolved to a signed URL via its JSVariables blob (as gbos-vm-mac/fetch.sh does).
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
    """Resolve .../latest/<token> to the real (signed) download URL."""
    html = fetch(base + token).decode('utf-8', 'replace')
    m = re.search(r'var JSVariables = (\{.*?\});', html)
    if m:
        j = json.loads(m.group(1))
        for key in ('artifactUrl', 'archiveUrl', 'downloadUrl'):
            if j.get(key):
                return j[key]
    m = re.search(r'href="(https?://[^"]+' + re.escape(token.rsplit('/', 1)[-1]) + r')"', html)
    return m.group(1) if m else None


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
page = fetch(base).decode('utf-8', 'replace')
tokens = sorted({t.rstrip('/') for t in re.findall(r'latest/([A-Za-z0-9._/+-]+)', page)})
m = re.search(r'var JSVariables = (\{.*?\});', page)
if m:
    for key in ('artifactName', 'artifactUrl'):
        v = json.loads(m.group(1)).get(key)
        if v and v not in tokens:
            tokens.append(v.rsplit('/', 1)[-1])
if not tokens:
    sys.exit(f'no artifacts found on {base} (is the build/target correct?)')

def token_for(name):
    return next((t for t in tokens if t.rsplit('/', 1)[-1] == name), None)

remaining = set(want)

# 1) modules published individually
for name in sorted(remaining):
    t = token_for(name)
    if not t:
        continue
    url = artifact_url(t)
    if not url:
        continue
    out.joinpath(name).write_bytes(fetch(url))
    remaining.discard(name)
    print(f'  {name}')
if not remaining:
    print(f'done: {len(want)} modules in {out}')
    sys.exit(0)

# 2) modules bundled in an archive
archives = [t for t in tokens if ARCHIVE.search(t)]
print(f'  remaining {len(remaining)}; trying archives: {", ".join(archives) or "(none)"}')
for t in archives:
    url = artifact_url(t)
    if not url:
        continue
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
if remaining:
    sys.exit('missing modules: ' + ', '.join(sorted(remaining)))
print(f'done: {len(want)} modules in {out}')
PY
