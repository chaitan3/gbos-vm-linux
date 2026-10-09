# Lapis security port (software KeyMint, no Trusty)

This ports the `gbos-vm-mac` "security port" idea to the x86_64 Android 17 Googlebook
(*lapis*) recovery image. It replaces the hardware-backed security stack that a VM cannot
provide with Google's own software/Cuttlefish components, so `keystore2` can come up.

The build does **not** emulate a TPM. It:

- installs a pure-software AOSP/Cuttlefish KeyMint at `/vendor/bin/hw/android.hardware.security.keymint-service`;
- disables the StrongBox vendor APEX `com.android.hardware.keymint.strongbox.desktop`;
- optionally installs the nonsecure Gatekeeper APEX and drops the Trusty Gatekeeper/Weaver;
- optionally stops the GSC/Trusty daemons that would otherwise crash-loop;
- patches the first-stage fstab so the modified vendor partition is not dm-verity checked.

Everything is done by rebuilding the **vendor_a** logical partition inside `super`; the
system partitions are left untouched and keep their original verity.

## Why a donor is required

The lapis image only ships Trusty-backed KeyMint:

- `com.android.hardware.keymint.strongbox.desktop` (vendor APEX, needs the Trusty VM);
- `/vendor/bin/hw/android.hardware.security.keymint-service.trusty_tee.nonsecure`, which
  unconditionally connects to the Trusty TA at `/dev/trusty-ipc-dev0` *before* registering.

Neither works in a plain QEMU/KVM guest, so the software KeyMint binary comes from an
x86_64 **Cuttlefish** build (the same place the mac project took its ARM64 binaries
from). Google's CI is not reachable from every environment, so the donor is fetched
separately.

## Donor

Fetch an x86_64 Cuttlefish image on a host that can reach `ci.android.com`:

```sh
fetch_cuttlefish_donor.sh 16373615          # default target aosp_cf_x86_64_only_phone-userdebug
python3 unpack_cuttlefish_donor.py aosp_cf_x86_64_only_phone-img-16373615.zip donor
```

(That is the Cuttlefish build the mac project pins; it contains the pure-software
`com.android.hardware.keymint.rust_nonsecure.apex` and `com.android.hardware.gatekeeper.nonsecure.apex`.)

`unpack_cuttlefish_donor.py` reads the super image out of the zip without mounting it and
produces:

```
donor/
  keymint-service                software KeyMint HAL (x86_64 ELF)
  lib64/*.so                     the payload's own libcrypto/libssl/libbase/libc++/libcutils
  vintf/android.hardware.security.{keymint,secureclock,sharedsecret}-service.xml
  gatekeeper.nonsecure.apex      (with --locksettings)
```

If you already have the files, just create the `donor/` directory by hand.

## Build

```sh
./build_security_image.sh
```

Outputs (`image-security/` by default):

- `googlebook-security.bin` – a copy of the recovery image with a rebuilt vendor_a;
- `initrd` – the original initrd with `vendor … avb=vbmeta` removed from the fstab;
- `manifest.json` – what was added, where vendor_a ended up.

The builder:

1. locates `vendor_a` via the AOSP LP metadata;
2. extracts the vendor EROFS and merges an overlay EROFS onto it (`mkfs.erofs --incremental=data`);
3. fits it back in place, or relocates `vendor_a` into free space in `super` and updates
   all six LP metadata copies (primary + backup) with recomputed checksums;
4. rewrites the initrd.

Rewrite in place without a 19 GB copy (RAM allowing) with:

```sh
python3 make_lapis_security_port.py --donor donor --out image-security --no-copy
```

`--no-copy` is also how the mechanics are tested against a sparse image.

## Boot

```sh
./boot_security.sh "$PWD" <run-name> 120
```

This uses `image-security/googlebook-security.bin` + `image-security/initrd` and passes

```
androidboot.vendor.apex.com.android.hardware.keymint.strongbox.desktop=none
```

`boot_serial.sh` now also honours `GBOS_IMAGE`, `GBOS_INITRD` and `GBOS_KERNEL`, so the
stock image still boots with no overrides.

Success looks like `keystore2` registering `android.system.keystore2.IKeystoreService/default`
instead of `vold` looping on it; `grep -E 'keystore2|keymint|avc' logs/<run>/serial.log`.

## Status / caveats

Validated locally (no VM boot possible in this sandbox – no `/dev/kvm`):

- LP parse/relocate with all six metadata copies re-checksummed;
- EROFS incremental overlay: new files added, existing files preserved, `fsck.erofs` clean;
- correct SELinux labels (`hal_keymint_default_exec`, `vendor_file`, `vendor_configs_file`,
  `vendor_apex_file`) — lapis policy already maps
  `/vendor/bin/hw/android.hardware.security.keymint-service` to `hal_keymint_default_exec`;
- initrd fstab edit.

Not yet validated (needs a boot):

- whether the software KeyMint needs any shared library beyond those bundled (its payload's
  own libcrypto/libssl/libbase/libc++/libcutils are installed under `lib64/vm_keymint/`);
- Gatekeeper/Weaver behaviour (secondary: keystore2 needs KeyMint, not Gatekeeper).

Checked against this image: the donor KeyMint declares AIDL v5 and RPC v3, and lapis ships
`android.hardware.security.keymint-V5-ndk.so` and `android.hardware.security.rkp-V3-ndk.so`,
so the versions line up. With build 16373615 the overlay fits the original `vendor_a`
extent in place; the builder relocates the partition automatically if a larger donor is used.
