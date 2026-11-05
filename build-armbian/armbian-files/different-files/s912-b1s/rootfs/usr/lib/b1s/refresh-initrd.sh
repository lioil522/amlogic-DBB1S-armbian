#!/bin/bash
# Refresh an embedded firmware copy after the selected B1S files are restored.
set -euo pipefail
kernel="${1:?Installed kernel release is required}"
initrd="/boot/initrd.img-${kernel}"
[[ -s "${initrd}" ]] || { echo "Missing initrd: ${initrd}" >&2; exit 1; }
listing="$(lsinitramfs "${initrd}")"
if ! grep -Eq '(^|/)(brcmfmac43455-sdio\.(bin|txt|clm_blob)|BCM4345C0\.hcd)$' <<<"${listing}"; then
    # No early firmware copy: the driver loads the preserved rootfs files.
    exit 0
fi
update-initramfs -u -k "${kernel}"
mkimage -A arm64 -O linux -T ramdisk -C none -n uInitrd \
    -d "${initrd}" "/boot/uInitrd-${kernel}.new"
mv -f "/boot/uInitrd-${kernel}.new" "/boot/uInitrd-${kernel}"
cp "/boot/uInitrd-${kernel}" /boot/uInitrd.new
mv -f /boot/uInitrd.new /boot/uInitrd
