#!/usr/bin/env bash
# Download an x86_64 Cuttlefish build zip from ci.android.com for use as the security-port
# donor. Needs access to Google's CI (blocked in some sandboxes). Then run
# unpack_cuttlefish_donor.py on the result.
#
#   fetch_cuttlefish_donor.sh BUILD [TARGET] [OUTDIR]
#
# Find BUILD on https://ci.android.com (pick a target/BUILD whose aosp_cf_x86_64_only_phone
# image exists); TARGET defaults to aosp_cf_x86_64_only_phone-userdebug.
set -euo pipefail
BUILD=${1:?usage: fetch_cuttlefish_donor.sh BUILD [TARGET] [OUTDIR]}
TARGET=${2:-aosp_cf_x86_64_only_phone-userdebug}
OUT=${3:-.}
STEM=${TARGET%-userdebug}
ZIP="$STEM-img-$BUILD.zip"
URL="https://ci.android.com/builds/submitted/$BUILD/$TARGET/latest/$ZIP"
echo "resolving $URL"
ART=$(python3 - "$URL" <<'PY'
import json, re, sys, urllib.request
html = urllib.request.urlopen(sys.argv[1], timeout=60).read(1 << 20).decode()
print(json.loads(re.search(r'var JSVariables = (\{.*?\});', html).group(1))['artifactUrl'])
PY
)
echo "artifact: $ART"
mkdir -p "$OUT"
curl -fL --progress-bar "$ART" -o "$OUT/$ZIP"
echo "downloaded $OUT/$ZIP"
echo "now run: python3 unpack_cuttlefish_donor.py $OUT/$ZIP <donor-dir>"
