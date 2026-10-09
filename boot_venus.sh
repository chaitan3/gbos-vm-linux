#!/bin/sh
# Boot the security-ported image with QEMU's virtual GPU in Venus mode, so the guest's
# Vulkan loader uses the Mesa Venus ICD (libvulkan_virtio.so) we install with --venus.
# SurfaceFlinger/HWUI run on VirGL GLES (--virgl); app Vulkan uses Venus.
#
#   boot_venus.sh WORKDIR NAME SECONDS [extra kernel args]
#
# Overrides:
#   GBOS_GPU_ARGS      default: virtio-gpu-gl-pci (Venus, 1920x1200, blob)
#   GBOS_DISPLAY_ARGS  default: -display gtk,gl=on,grab-on-hover=on (auto-grabs the mouse)
#                      headless without a render node: -display egl-headless
#                      view over VNC (needs a render node): -display egl-headless -vnc 127.0.0.1:0
#   GBOS_INPUT_ARGS    default: qemu-xhci + usb-kbd + usb-mouse
#   GBOS_NET_ARGS      default: user-mode NAT over virtio-net (host internet, no inbound)
#   GBOS_SNAPSHOT      default: on (throwaway disk). 0 persists /data for fast reboots.
#   GBOS_XRES/GBOS_YRES  default 1920x1200
set -eu
X=$1; NAME=$2; SECS=$3; EXTRA=${4:-}
: "${GBOS_XRES:=1920}"
: "${GBOS_YRES:=1200}"
: "${GBOS_GPU_ARGS:=-device virtio-gpu-gl-pci,hostmem=4G,blob=true,venus=true,addr=0x1,xres=$GBOS_XRES,yres=$GBOS_YRES}"
: "${GBOS_DISPLAY_ARGS:=-display gtk,gl=on,grab-on-hover=on}"
: "${GBOS_INPUT_ARGS:=-device qemu-xhci,id=xhci -device usb-kbd,id=keyboard,bus=xhci.0 -device usb-mouse,id=mouse,bus=xhci.0}"
: "${GBOS_NET_ARGS:=-netdev user,id=net0,ipv6=off -device virtio-net-pci,netdev=net0}"
: "${GBOS_SNAPSHOT:=on}"
export GBOS_GPU_ARGS GBOS_DISPLAY_ARGS GBOS_INPUT_ARGS GBOS_NET_ARGS GBOS_SNAPSHOT
exec "$X/boot_security.sh" "$X" "$NAME" "$SECS" "$EXTRA"
