#!/usr/bin/env bash
# Offline preparation: extract the boot images from the recovery image and build the VM
# ramdisk. Run after placing the recovery image and fetching the kernel modules.
#
#   prepare.sh
# Env: GBOS_RAW (default ./android-desktop_signed_recovery_image.bin)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RAW="${GBOS_RAW:-$HERE/android-desktop_signed_recovery_image.bin}"

if [ ! -f "$RAW" ]; then
  echo "error: recovery image not found: $RAW" >&2
  echo "Run ./fetch_recovery_image.sh (or set GBOS_RAW)." >&2
  exit 1
fi

if [ ! -f "$HERE/firmware/kernel" ]; then
  echo "== extracting boot images -> firmware/"
  python3 "$HERE/extract_lapis.py" "$RAW" "$HERE/firmware"
fi

if [ ! -f "$HERE/initrd" ]; then
  if ! ls "$HERE/modules/"*.ko >/dev/null 2>&1; then
    echo "error: no kernel modules in $HERE/modules" >&2
    echo "Run ./fetch_kernel_modules.sh first, or place the *.ko files there." >&2
    exit 1
  fi
  echo "== building VM ramdisk -> initrd"
  python3 "$HERE/make_initrd_x86.py" "$RAW" "$HERE/firmware" "$HERE/modules" "$HERE/initrd"
fi

echo "prepare: $HERE/firmware and $HERE/initrd ready"
