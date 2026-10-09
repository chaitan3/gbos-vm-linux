#!/bin/sh
# Boot the security-ported lapis image (software KeyMint, StrongBox disabled) under KVM.
#   boot_security.sh WORKDIR NAME SECONDS [extra kernel args]
set -eu
X=$1; NAME=$2; SECS=$3; EXTRA=${4:-}
SEC=$X/image-security
if [ ! -f "$SEC/googlebook-security.bin" ] || [ ! -f "$SEC/initrd" ]; then
  echo "missing $SEC/googlebook-security.bin or $SEC/initrd; run build_security_image.sh first" >&2
  exit 1
fi
GBOS_IMAGE="$SEC/googlebook-security.bin" GBOS_INITRD="$SEC/initrd" \
  "$X/boot_serial.sh" "$X" "$NAME" "$SECS" \
  "androidboot.vendor.apex.com.android.hardware.keymint.strongbox.desktop=none $EXTRA"
