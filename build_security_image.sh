#!/usr/bin/env bash
# Build the security-ported lapis image: software KeyMint, no StrongBox, no Trusty.
#   build_security_image.sh [extra make_lapis_security_port.py args]
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
RAW=${GBOS_RAW:-$HERE/android-desktop_signed_recovery_image.bin}
DONOR=${GBOS_DONOR:-$HERE/donor}
OUT=${GBOS_SECURITY_DIR:-$HERE/image-security}

if [ ! -f "$DONOR/keymint-service" ]; then
  cat >&2 <<EOF
donor is missing: $DONOR/keymint-service

A Cuttlefish x86_64 image is needed for the pure-software KeyMint binary (the image
under test only has a Trusty-backed one). On a host that can reach ci.android.com:

  1. download aosp_cf_x86_64_only_phone-img-<build>.zip
  2. run: python3 "$HERE/unpack_cuttlefish_donor.py" <zip> "$DONOR"

See SECURITY_PORT.md for details.
EOF
  exit 1
fi

exec python3 "$HERE/make_lapis_security_port.py" \
  --raw "$RAW" --donor "$DONOR" --out "$OUT" \
  --locksettings "$@"
