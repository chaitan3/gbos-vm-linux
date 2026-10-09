#!/bin/sh
# First-boot probe: Intel Googlebook image under KVM, serial console only, disk opened as a throwaway snapshot.
#   boot_serial.sh WORKDIR NAME SECONDS [extra kernel args]   (WORKDIR holds lapis-recovery.raw and work/boot)
set -eu
X=$1; NAME=$2; SECS=$3; EXTRA=${4:-}
LOG=$X/logs/$NAME; mkdir -p "$LOG"
CMD="console=ttyS0,115200 earlycon=uart8250,io,0x3f8,115200 panic=0 root=/dev/ram0 loglevel=7 \
8250.nr_uarts=4 console_msg_format=syslog log_buf_len=1M cgroup_disable=pressure firmware_class.path=/vendor/firmware \
binder.impl=rust ia32_emulation=false vsyscall=none printk.devkmsg=on \
androidboot.hardware=android-desktop androidboot.hardware.platform=android-desktop androidboot.slot_suffix=_a \
androidboot.boot_devices=pci0000:00/0000:00:04.0 androidboot.vbmeta.size=6720 androidboot.vbmeta.hash_alg=sha256 \
androidboot.vbmeta.digest=bc270dbed03f9068d69d3da5dcbfcf80a947186d36b5dd4c5a6bcc5f7dc45ec2 \
androidboot.vbmeta.device_state=unlocked androidboot.verifiedbootstate=orange androidboot.veritymode=enforcing $EXTRA"
timeout "$SECS" qemu-system-x86_64 -enable-kvm -cpu host -smp 4 -m 6G -machine q35 -nodefaults -no-reboot \
  -kernel $X/firmware/kernel -initrd $X/initrd -append "$CMD" \
  -drive if=none,id=disk,format=raw,file=$X/android-desktop_signed_recovery_image.bin,snapshot=on \
  -device nvme,drive=disk,serial=gb0,addr=0x4 \
  -display none -serial file:$LOG/serial.log -monitor none || true
wc -l $LOG/serial.log

