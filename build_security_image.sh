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

VENUS=""
if [ -f "$HERE/guest-mesa/libvulkan_virtio.so" ]; then
  VENUS="--venus"
else
  echo "note: $HERE/guest-mesa/libvulkan_virtio.so not found; building without Venus (run build_guest_mesa.sh first)" >&2
fi
VIRGL=""
if [ -f "$HERE/guest-mesa/gl/libEGL_virgl.so" ]; then
  VIRGL="--virgl"
else
  echo "note: $HERE/guest-mesa/gl/libEGL_virgl.so not found; building without VirGL GLES (run build_guest_mesa.sh first)" >&2
fi

python3 "$HERE/make_lapis_security_port.py" \
  --raw "$RAW" --donor "$DONOR" --out "$OUT" \
  --locksettings --diagnostics --composer --vmcompat $VENUS $VIRGL "$@"

# The captured image ships a ~4 GiB userdata, which fills during first boot (ENOSPC).
case " $* " in *" --no-copy "*) : ;; *)
  python3 "$HERE/expand_lapis_userdata.py" "$OUT/googlebook-security.bin" "${GBOS_USERDATA_GIB:-16}"
  ;;
esac
