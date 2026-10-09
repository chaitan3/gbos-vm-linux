#!/usr/bin/env bash
# Download and extract the Googlebook (lapis) recovery image.
#
#   fetch_recovery_image.sh [OUT]
#     OUT   default ./android-desktop_signed_recovery_image.bin
#
# Env: GBOS_RECOVERY_URL (default the pinned lapis build), GBOS_RECOVERY_ZIP (cache path),
#      GBOS_KEEP_ZIP=1 to keep the archive after extraction (default: remove it).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HERE/android-desktop_signed_recovery_image.bin}"
URL="${GBOS_RECOVERY_URL:-https://dl.google.com/device/recovery/lapis-user/16452207/recovery.zip}"
ZIP="${GBOS_RECOVERY_ZIP:-$HERE/recovery.zip}"
ENTRY=android-desktop_signed_recovery_image.bin

if [ -f "$OUT" ]; then
  echo "already present: $OUT"
  exit 0
fi

if [ ! -f "$ZIP" ]; then
  echo "== downloading $URL"
  rm -f "$ZIP.part"
  curl -fL --retry 3 -C - -o "$ZIP.part" "$URL"
  mv "$ZIP.part" "$ZIP"
fi

echo "== extracting $ENTRY -> $OUT"
python3 - "$ZIP" "$OUT" "$ENTRY" <<'PY'
import hashlib
import os
import sys
import zipfile

zip_path, out, entry = sys.argv[1:4]
h = hashlib.sha256()
with zipfile.ZipFile(zip_path) as z:
    names = z.namelist()
    name = entry if entry in names else next((n for n in names if n.endswith('.bin')), None)
    if not name:
        sys.exit(f'{entry} not found in {zip_path}; entries: {names[:10]}')
    with z.open(name) as f, open(out + '.part', 'wb') as g:
        while True:
            b = f.read(8 << 20)
            if not b:
                break
            g.write(b)
            h.update(b)
os.replace(out + '.part', out)
print('sha256', h.hexdigest())
PY
ls -l "$OUT"

if [ "${GBOS_KEEP_ZIP:-0}" != 1 ]; then
  rm -f "$ZIP"
fi
echo "done: $OUT"
