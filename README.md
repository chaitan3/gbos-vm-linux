# Googlebook VM — build & run from scratch (x86_64 / Linux host)

Runs the **Intel Googlebook** (Android 17, device `lapis`, ASUS "Googlebook 14") recovery
image under QEMU/KVM, adapted so it boots on a VM:

- hardware security replaced with **software** (software KeyMint, **no StrongBox / Trusty**);
- virtual GPU via QEMU `virtio-gpu` with **Venus (Vulkan)** for apps and **VirGL (GLES)**
  for SurfaceFlinger/HWUI;
- Cuttlefish **DRM hwcomposer** and **software boot control** (no ChromeOS firmware calls);
- the vendor `userdata` grown to **16 GiB** (the download ships ~4 GiB, which ENOSPCs on
  first boot).

The result is a working Android desktop: login/setup UI, keyboard + mouse, and outbound
network.

## Pipeline

| step | script | what it does |
|------|--------|--------------|
| recovery image | `fetch_recovery_image.sh` | download + extract Google's recovery zip |
| fetch modules | `fetch_kernel_modules.sh` | download the `*.ko` in `modules/` from CI |
| fetch donor | `fetch_cuttlefish_donor.sh` + `unpack_cuttlefish_donor.py` | software KeyMint, nonsecure Gatekeeper, DRM composer, software boot service |
| prepare | `prepare.sh` | extract boot images (`firmware/`) and build the ramdisk (`initrd`) |
| guest graphics | `build_guest_mesa.sh` | Mesa 26.2.4 for Android x86_64: Venus Vulkan ICD + VirGL GLES |
| build image | `build_security_image.sh` | rebuild `vendor_a`, expand userdata → `image-security/` |
| run | `boot_vm.sh` | launch QEMU with GPU/input/network → `logs/<name>/serial.log` |

Design/background notes are in `SECURITY_PORT.md`.

## 0. Prerequisites

- **x86_64 Linux host with KVM** (`/dev/kvm`) and a **Vulkan-capable GPU** (or a software
  Vulkan ICD such as lavapipe).
- **QEMU** with virtio-gpu GL + Venus — i.e. built against **virglrenderer** with Vulkan.
- **Mesa** host driver + `virglrenderer`.
- Tools: `python3`, `curl`, `lz4`, `erofs-utils` (`dump.erofs`, `mkfs.erofs`, `fsck.erofs`),
  `e2fsprogs` (`debugfs`), `qemu-img`/`qemu-io`, `meson`, `ninja`, `bison`, `flex`,
  `pkg-config`, a C/C++ toolchain.
- **Android NDK r30** (for the guest Mesa drivers).
- ~100 GB free disk.

Example on Arch (adapt to your distro):

```sh
sudo pacman -S --needed qemu-base qemu-img qemu-ui-gtk virglrenderer mesa \
  vulkan-intel mesa-utils python curl lz4 erofs-utils e2fsprogs meson ninja bison flex
```

Check Venus is available on the host:

```sh
qemu-system-x86_64 -device virtio-gpu-gl-pci,help | grep -E 'xres|yres|venus'
```

## 1. Recovery image

```sh
./fetch_recovery_image.sh
```

Downloads Google's device recovery zip for `lapis-user/16452207` and extracts
`android-desktop_signed_recovery_image.bin` (~19 GB) to the repo root. Override the source
with `GBOS_RECOVERY_URL`, point `GBOS_RAW` at an existing image, or `GBOS_KEEP_ZIP=1` to
keep the archive. (The image is never redistributed by this project.)

## 2. Kernel modules

```sh
./fetch_kernel_modules.sh
```

Downloads the `*.ko` listed in `modules/` (`failover.ko`, `net_failover.ko`,
`virtio_dma_buf.ko`, `virtio-gpu.ko`, `virtio_input.ko`, `virtio_net.ko`, `virtio-rng.ko`,
`virtio_snd.ko`) from `ci.android.com` build `16302624` / `kernel_virt_x86_64`.

## 3. Cuttlefish donor

Needs an x86_64 Cuttlefish image (the lapis image only ships a Trusty-backed KeyMint):

```sh
./fetch_cuttlefish_donor.sh 16373615
python3 unpack_cuttlefish_donor.py aosp_cf_x86_64_only_phone-img-16373615.zip donor
```

## 4. Prepare (offline)

```sh
./prepare.sh
```

Extracts the boot images into `firmware/` and builds the VM ramdisk `initrd`.

## 5. Guest graphics drivers

Download the Android NDK and Mesa source, then build:

```sh
# Android NDK r30: unpack to android-ndk-r30/  (override with ANDROID_NDK)
# Mesa 26.2.4:    unpack to mesa-26.2.4/      (download-only can fetch it)
./build_guest_mesa.sh --download-only   # fetch/cache Mesa + libdrm, no compile
./build_guest_mesa.sh                   # build guest-mesa/{libvulkan_virtio.so,gl/*}
```

`mesa-26.2.4-venus/` gets the `patches/mesa-android-mapper5.patch` applied automatically.
Outputs land in `guest-mesa/`.

## 6. Build the image

```sh
./build_security_image.sh
```

Produces `image-security/`:

- `googlebook-security.bin` — copy of the recovery image with the rebuilt `vendor_a`
  (software KeyMint, nonsecure Gatekeeper, no StrongBox) and a 16 GiB `userdata`;
- `initrd` — first-stage fstab adjusted so the modified vendor is not dm-verified;
- `manifest.json` — what changed.

The original download is never modified.

> **Rebuilding resets the VM.** `build_security_image.sh` regenerates
> `image-security/googlebook-security.bin` from the recovery image, so any state the VM
> saved (it persists `/data` by default) is discarded. It therefore **refuses to overwrite
> an existing image** unless you set `GBOS_FORCE=1`; use `GBOS_SECURITY_DIR=<dir>` to build
> a separate image instead of clobbering one you're using.

## 7. Run

```sh
./boot_vm.sh "$PWD" gb-venus 240
```

Opens an SDL window (1920×1440), grabs the mouse, and logs the serial console to
`logs/<name>/serial.log`. Useful greps:

```sh
grep -c boot_completed logs/gb-venus/serial.log
grep -aE 'SurfaceFlinger|drmhwc|EthernetTracker' logs/gb-venus/serial.log | tail
```

Disk changes are **persisted by default** (`GBOS_SNAPSHOT=off`), so the first boot is slow
(fresh 16 GiB userdata → dexopt/package scan) but later boots are fast. For a throwaway run
that discards `/data` when the VM stops, use `GBOS_SNAPSHOT=on`:

```sh
GBOS_SNAPSHOT=on ./boot_vm.sh "$PWD" gb-venus 300
```

## Environment overrides

| var | default | effect |
|-----|---------|--------|
| `GBOS_RAW` | `./android-desktop_signed_recovery_image.bin` | recovery image input |
| `GBOS_DONOR` | `./donor` | Cuttlefish donor dir |
| `GBOS_SECURITY_DIR` | `./image-security` | image output dir |
| `GBOS_USERDATA_GIB` | `16` | userdata partition size |
| `GBOS_XRES` / `GBOS_YRES` | `1920` / `1440` | guest display size (4:3 matches the wacom tablet) |
| `GBOS_SNAPSHOT` | `off` | `off` = persist `/data` between boots; `on` = throwaway disk |
| `GBOS_FORCE` | `0` | `1` lets `build_security_image.sh` overwrite an existing image |
| `GBOS_DISPLAY_ARGS` | `-display sdl,gl=on` | e.g. `-display none -spice unix=on,addr=/tmp/gbos-spice,gl=es` + a SPICE client (viewer-drawn cursor), `-display egl-headless` (headless), `-display egl-headless -vnc 127.0.0.1:0` (VNC) |
| `GBOS_GPU_ARGS` | virtio-gpu-gl-pci, Venus, 1920×1200 | full GPU device line |
| `GBOS_INPUT_ARGS` | xhci + usb-kbd + usb-mouse | input devices |
| `GBOS_NET_ARGS` | user-mode NAT over virtio-net | set empty for offline |
| `ANDROID_NDK` / `ANDROID_API` | `./android-ndk-r30` / `37` | NDK for `build_guest_mesa.sh` |

## Troubleshooting

- **No display / blank window, `drmhwc: Could not find a suitable encoder/crtc`** — the
  image must be built with the Cuttlefish composer (`--composer`, on by default in
  `build_security_image.sh`).
- **`surfaceflinger` aborts importing gralloc buffers (Venus)** — SurfaceFlinger must run
  on VirGL GLES; ensure `guest-mesa/gl/` exists so `--virgl` is applied, and that
  `build.prop` has `ro.hardware.egl=virgl` + `ro.hwui.use_vulkan=false`.
- **Reboot with `Checkpoint commit failed` / `Unable to set fw boot successful status`** —
  the software boot-control service is needed (`--vmcompat`, on by default).
- **`ENOSPC` flooding the log, black screen after the boot logo** — userdata too small;
  `expand_lapis_userdata.py` runs automatically, or bump `GBOS_USERDATA_GIB`.
- **No network** — the host must have internet; the guest gets it via QEMU slirp NAT
  (`GBOS_NET_ARGS`). If virtio-net isn't picked up, try `usb-net` on xhci.
- **No mouse cursor** — the guest's own cursor sprite is not composited under VirGL (SurfaceFlinger
  draws it, but the pixels never land), so `boot_vm.sh` shows a **host-side cursor** instead:
  `-display sdl,gl=on,show-cursor=on` keeps QEMU's cursor visible. To stop it drifting, the pointer
  is made **absolute** with `usb-wacom-tablet`, and the image ships
  `usr/idc/Vendor_056a_Product_0000.idc` forcing it to a plain pointer (no stylus ink trails). The
  default 1920×1440 (4:3) resolution matches the tablet's raw range so the pointer maps 1:1.
