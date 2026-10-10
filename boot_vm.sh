#!/bin/sh
# Boot the security-ported lapis image (software KeyMint, no StrongBox) under QEMU/KVM.
#
#   boot_vm.sh WORKDIR NAME SECONDS [extra kernel args]
#
# WORKDIR holds image-security/ (built by build_security_image.sh) and logs/.
#
# Overrides:
#   GBOS_GPU_ARGS      default: virtio-gpu-gl-pci, VirGL+Venus, blob, 1920x1440
#   GBOS_DISPLAY_ARGS  default: -display sdl,gl=on,show-cursor=on
#                      (the guest cursor sprite is not composited under VirGL, so show the
#                      host cursor). Alternatives:
#                        -display gtk,gl=on,show-cursor=on
#                        -display none -spice unix=on,addr=/tmp/gbos-spice,gl=es  (+ a SPICE client)
#                        -display egl-headless
#                        -display egl-headless -vnc 127.0.0.1:0
#   GBOS_INPUT_ARGS    default: qemu-xhci + usb-kbd + usb-wacom-tablet
#                      (absolute pointer, so the host cursor does not drift; the image's
#                      usr/idc/Vendor_056a_Product_0000.idc makes it a plain pointer, not a stylus)
#   GBOS_NET_ARGS      default: user-mode NAT over virtio-net (host internet, no inbound)
#   GBOS_SNAPSHOT      default: off (disk changes persist in the image; "on" = throwaway disk)
#   GBOS_XRES/GBOS_YRES  default 1920x1440 (4:3, matches the wacom tablet's raw range)
#   GBOS_IMAGE / GBOS_INITRD / GBOS_KERNEL  override the boot artifacts
set -eu
X=$1; NAME=$2; SECS=$3; EXTRA=${4:-}

: "${GBOS_XRES:=1920}"
: "${GBOS_YRES:=1440}"
: "${GBOS_GPU_ARGS:=-device virtio-gpu-gl-pci,hostmem=4G,blob=true,venus=true,addr=0x1,xres=$GBOS_XRES,yres=$GBOS_YRES}"
: "${GBOS_DISPLAY_ARGS:=-display sdl,gl=on,show-cursor=on}"
: "${GBOS_INPUT_ARGS:=-device qemu-xhci,id=xhci -device usb-kbd,id=keyboard,bus=xhci.0 -device usb-wacom-tablet,id=tablet,bus=xhci.0}"
: "${GBOS_NET_ARGS:=-netdev user,id=net0,ipv6=off -device virtio-net-pci,netdev=net0}"
: "${GBOS_SNAPSHOT:=off}"

SEC=$X/image-security
IMAGE=${GBOS_IMAGE:-$SEC/googlebook-security.bin}
INITRD=${GBOS_INITRD:-$SEC/initrd}
KERNEL=${GBOS_KERNEL:-$X/firmware/kernel}
if [ ! -f "$IMAGE" ] || [ ! -f "$INITRD" ]; then
  echo "missing $IMAGE or $INITRD; run build_security_image.sh first" >&2
  exit 1
fi

LOG=$X/logs/$NAME; mkdir -p "$LOG"

CMD="console=ttyS0,115200 earlycon=uart8250,io,0x3f8,115200 panic=0 root=/dev/ram0 loglevel=7 \
8250.nr_uarts=4 console_msg_format=syslog log_buf_len=1M cgroup_disable=pressure firmware_class.path=/vendor/firmware \
binder.impl=rust ia32_emulation=false vsyscall=none printk.devkmsg=on \
androidboot.hardware=android-desktop androidboot.hardware.platform=android-desktop androidboot.slot_suffix=_a \
androidboot.boot_devices=pci0000:00/0000:00:04.0 androidboot.vbmeta.size=6720 androidboot.vbmeta.hash_alg=sha256 \
androidboot.vbmeta.digest=bc270dbed03f9068d69d3da5dcbfcf80a947186d36b5dd4c5a6bcc5f7dc45ec2 \
androidboot.vbmeta.device_state=unlocked androidboot.verifiedbootstate=orange androidboot.veritymode=enforcing \
androidboot.vendor.apex.com.android.hardware.keymint.strongbox.desktop=none $EXTRA"

timeout "$SECS" qemu-system-x86_64 -enable-kvm -cpu host -smp 4 -m 6G -machine q35 -nodefaults -no-reboot \
  -kernel "$KERNEL" -initrd "$INITRD" -append "$CMD" \
  -drive if=none,id=disk,format=raw,file="$IMAGE",snapshot="$GBOS_SNAPSHOT" \
  -device nvme,drive=disk,serial=gb0,addr=0x4 \
  $GBOS_GPU_ARGS $GBOS_INPUT_ARGS $GBOS_NET_ARGS $GBOS_DISPLAY_ARGS -serial file:"$LOG/serial.log" -monitor none || true
wc -l "$LOG/serial.log"
