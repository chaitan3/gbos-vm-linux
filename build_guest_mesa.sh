#!/usr/bin/env bash
# Build the Mesa Venus Vulkan driver (libvulkan_virtio.so) for Android x86_64.
#
# This is the guest-side Vulkan ICD the lapis VM needs to talk to QEMU's
# virtio-gpu (venus=on). It is built from Mesa 26.2.4 + the minigbm mapper5 shim
# (patches/mesa-android-mapper5.patch), because the Android NDK does not ship the
# AIDL mapper headers / `ui` pkg-config that upstream Mesa's IMapper5 path needs.
#
# Everything is placed under this script's directory. Re-runnable / idempotent.
#
# Usage:
#   ./build_guest_mesa.sh                 # fetch sources + build
#   ./build_guest_mesa.sh --download-only # only fetch/cache sources, no compile
# Env overrides:
#   ANDROID_NDK   (default: ./android-ndk-r30)
#   ANDROID_API   (default: 37)
#   MESA_VERSION  (default: 26.2.4)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
NDK="${ANDROID_NDK:-$HERE/android-ndk-r30}"
API="${ANDROID_API:-37}"
MESA_VERSION="${MESA_VERSION:-26.2.4}"
MESA_SRC="$HERE/mesa-$MESA_VERSION"
VENUS_SRC="$HERE/mesa-$MESA_VERSION-venus"
BUILD="$HERE/mesa-build/build-venus"
CACHE="$VENUS_SRC/subprojects/packagecache"
PATCH="$HERE/patches/mesa-android-mapper5.patch"
MESA_URL="https://archive.mesa3d.org/mesa-$MESA_VERSION.tar.xz"
MESA_URL_FALLBACK="https://github.com/Mesa3D/mesa/archive/refs/tags/mesa-$MESA_VERSION.tar.xz"
LIBDRM_VERSION=2.4.133
LIBDRM_SHA=fc68f9d0ba2ea63c9432a299e14fea09fad7a8a66e8039fcd7802ca59f77b4f5
LIBDRM_URL="https://dri.freedesktop.org/libdrm/libdrm-$LIBDRM_VERSION.tar.xz"
LIBDRM_URL_FALLBACK="https://wrapdb.mesonbuild.com/v2/libdrm_$LIBDRM_VERSION-1/get_source/libdrm-$LIBDRM_VERSION.tar.xz"

DOWNLOAD_ONLY=0
usage() { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; }

say() { printf '\n== %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "missing tool: $1"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --download-only) DOWNLOAD_ONLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1 (try --download-only or --help)" ;;
  esac
  shift
done
[ "$DOWNLOAD_ONLY" = 1 ] && echo "download-only mode: fetching sources, skipping the build"

for t in curl sha256sum; do need "$t"; done
[ "$DOWNLOAD_ONLY" = 0 ] && { for t in meson ninja patch; do need "$t"; done; }

say "Mesa $MESA_VERSION source"
if [ ! -d "$MESA_SRC" ] && [ ! -f "$HERE/mesa-$MESA_VERSION.tar.xz" ]; then
  say "downloading $MESA_URL"
  curl -fL --retry 3 -o "$HERE/mesa-$MESA_VERSION.tar.xz.part" "$MESA_URL" \
    || curl -fL --retry 3 -o "$HERE/mesa-$MESA_VERSION.tar.xz.part" "$MESA_URL_FALLBACK" \
    || { rm -f "$HERE/mesa-$MESA_VERSION.tar.xz.part"; die "could not download Mesa"; }
  mv "$HERE/mesa-$MESA_VERSION.tar.xz.part" "$HERE/mesa-$MESA_VERSION.tar.xz"
fi
if [ ! -d "$MESA_SRC" ]; then
  [ -f "$HERE/mesa-$MESA_VERSION.tar.xz" ] || die "Mesa source missing at $MESA_SRC"
  tar -xf "$HERE/mesa-$MESA_VERSION.tar.xz" -C "$HERE"
  [ -d "$MESA_SRC" ] || mv "$HERE"/mesa-mesa-$MESA_VERSION "$MESA_SRC" 2>/dev/null || true
fi
[ -f "$MESA_SRC/VERSION" ] || die "Mesa source not found at $MESA_SRC"
echo "Mesa source: $MESA_SRC"

say "Venus source tree + mapper5 patch"
PATCH_ID="$(sha256sum "$PATCH" | cut -d' ' -f1)"
if [ "$(cat "$VENUS_SRC/.gbos-patch" 2>/dev/null || true)" != "$PATCH_ID" ]; then
  rm -rf "$VENUS_SRC" "$BUILD"
  cp -r "$MESA_SRC" "$VENUS_SRC"
  patch -s -p1 -d "$VENUS_SRC" < "$PATCH"
  echo "$PATCH_ID" > "$VENUS_SRC/.gbos-patch"
fi
echo "Venus tree: $VENUS_SRC"

say "libdrm $LIBDRM_VERSION subproject source"
mkdir -p "$CACHE"
LIBDRM_FILE="$CACHE/libdrm-$LIBDRM_VERSION.tar.xz"
libdrm_hash() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1; }
CACHED="$(libdrm_hash "$LIBDRM_FILE" 2>/dev/null || true)"
if [ -f "$LIBDRM_FILE" ] && [ "$CACHED" = "$LIBDRM_SHA" ]; then
  echo "using cached libdrm ($CACHED)"
else
  [ -f "$LIBDRM_FILE" ] && echo "cached libdrm hash ${CACHED:-empty} != $LIBDRM_SHA; re-downloading"
  say "downloading $LIBDRM_URL"
  # Download to a temp name and verify before installing, so a partial/blocked
  # download is never treated as cached.
  rm -f "$LIBDRM_FILE.part"
  curl -fL --retry 3 -o "$LIBDRM_FILE.part" "$LIBDRM_URL" \
    || curl -fL --retry 3 -o "$LIBDRM_FILE.part" "$LIBDRM_URL_FALLBACK" \
    || { rm -f "$LIBDRM_FILE.part"; die "could not download libdrm"; }
  GOT="$(libdrm_hash "$LIBDRM_FILE.part")"
  [ "$GOT" = "$LIBDRM_SHA" ] || { rm -f "$LIBDRM_FILE.part"; die "libdrm checksum mismatch (got $GOT, expected $LIBDRM_SHA)"; }
  mv "$LIBDRM_FILE.part" "$LIBDRM_FILE"
fi
echo "libdrm: $LIBDRM_FILE"

if [ "$DOWNLOAD_ONLY" = 1 ]; then
  say "download-only: done (no compile)"
  echo "  Mesa source : $MESA_SRC"
  echo "  Venus tree  : $VENUS_SRC (patched)"
  echo "  libdrm      : $LIBDRM_FILE"
  echo "Run '$0' (without --download-only) to build guest-mesa/libvulkan_virtio.so."
  exit 0
fi

TB="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin"
TBROOT="${TB%/bin}"
[ -x "$TB/x86_64-linux-android${API}-clang" ] \
  || die "NDK not found at $NDK (expected $TB/x86_64-linux-android${API}-clang). Set ANDROID_NDK."

say "meson cross file"
mkdir -p "$HERE/mesa-build/android-pkgconfig"
cat > "$HERE/mesa-build/cross-x86_64-android.ini" <<EOF
[constants]
ndk = '$TBROOT'
[binaries]
c = ndk / 'bin/x86_64-linux-android${API}-clang'
cpp = [ndk / 'bin/x86_64-linux-android${API}-clang++', '-fno-exceptions', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '--start-no-unused-arguments', '-static-libstdc++', '--end-no-unused-arguments']
ar = ndk / 'bin/llvm-ar'
strip = ndk / 'bin/llvm-strip'
c_ld = 'lld'
cpp_ld = 'lld'
pkg-config = 'pkg-config'
[host_machine]
system = 'android'
cpu_family = 'x86_64'
cpu = 'x86_64'
endian = 'little'
[properties]
needs_exe_wrapper = true
pkg_config_libdir = '$HERE/mesa-build/android-pkgconfig'
EOF

say "meson setup (venus only, expat disabled on Android)"
if [ ! -f "$BUILD/build.ninja" ]; then
  meson setup "$BUILD" "$VENUS_SRC" \
    --cross-file "$HERE/mesa-build/cross-x86_64-android.ini" --buildtype=release \
    --wrap-mode=nodownload \
    -Dforce_fallback_for=libdrm -Dallow-fallback-for=libdrm -Dlibdrm:default_library=static \
    -Dexpat=disabled \
    -Dplatforms=android -Dplatform-sdk-version="$API" -Dandroid-stub=true \
    -Dandroid-libbacktrace=disabled -Dllvm=disabled -Dglx=disabled -Dgbm=disabled -Dvideo-codecs= \
    -Dgallium-va=disabled -Dzstd=disabled \
    -Dgallium-drivers= -Dvulkan-drivers=virtio -Degl=disabled -Dgles1=disabled -Dgles2=disabled -Dopengl=false
fi

say "ninja: libvulkan_virtio.so"
ninja -C "$BUILD" src/virtio/vulkan/libvulkan_virtio.so

say "strip + install"
mkdir -p "$HERE/guest-mesa"
"$TB/llvm-strip" --strip-unneeded -o "$HERE/guest-mesa/libvulkan_virtio.so" \
  "$BUILD/src/virtio/vulkan/libvulkan_virtio.so"
file "$HERE/guest-mesa/libvulkan_virtio.so"
ls -l "$HERE/guest-mesa/libvulkan_virtio.so"

say "meson setup (VirGL GLES for SurfaceFlinger/HWUI)"
GLES_BUILD="$HERE/mesa-build/build-gles"
mkdir -p "$MESA_SRC/subprojects/packagecache"
[ -f "$MESA_SRC/subprojects/packagecache/libdrm-$LIBDRM_VERSION.tar.xz" ] \
  || cp "$LIBDRM_FILE" "$MESA_SRC/subprojects/packagecache/"
if [ ! -f "$GLES_BUILD/build.ninja" ]; then
  meson setup "$GLES_BUILD" "$MESA_SRC" \
    --cross-file "$HERE/mesa-build/cross-x86_64-android.ini" --buildtype=release \
    --wrap-mode=nodownload \
    -Dforce_fallback_for=libdrm -Dallow-fallback-for=libdrm -Dlibdrm:default_library=static \
    -Dexpat=disabled \
    -Dplatforms=android -Dplatform-sdk-version="$API" -Dandroid-stub=true \
    -Dandroid-libbacktrace=disabled -Dllvm=disabled -Dglx=disabled -Dgbm=disabled -Dvideo-codecs= \
    -Dgallium-va=disabled -Dzstd=disabled \
    -Dgallium-drivers=virgl -Dvulkan-drivers= -Degl=enabled -Dgles1=enabled -Dgles2=enabled -Dopengl=true \
    -Degl-lib-suffix=_virgl -Dgles-lib-suffix=_virgl
fi

say "ninja: VirGL GLES libs"
ninja -C "$GLES_BUILD" \
  src/egl/libEGL_virgl.so \
  src/mesa/glapi/es1api/libGLESv1_CM_virgl.so \
  src/mesa/glapi/es2api/libGLESv2_virgl.so \
  src/gallium/targets/dri/libgallium_dri.so

say "strip + install (GLES)"
mkdir -p "$HERE/guest-mesa/gl"
for f in src/egl/libEGL_virgl.so src/mesa/glapi/es1api/libGLESv1_CM_virgl.so \
         src/mesa/glapi/es2api/libGLESv2_virgl.so src/gallium/targets/dri/libgallium_dri.so; do
  "$TB/llvm-strip" --strip-unneeded -o "$HERE/guest-mesa/gl/$(basename "$f")" "$GLES_BUILD/$f"
done
ls -l "$HERE/guest-mesa/gl"

say "done: guest-mesa/{libvulkan_virtio.so,gl/*}"
