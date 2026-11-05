#!/bin/bash
# Preserve the *selected* board DTB and firmware across tar and deb updates.
# Arguments are explicit so the same transaction can be tested off-device.
set -euo pipefail
mode="${1:?save or restore}"
boot="${2:?Boot path}"
firmware="${3:?Broadcom firmware path}"
backup="${4:?Snapshot directory}"
fdt="${5:?Selected DTB basename}"
[[ "${fdt}" == meson-gxm-dangbei-b1s*.dtb && "${fdt}" != */* ]] || exit 1
files=(brcmfmac43455-sdio.bin brcmfmac43455-sdio.txt brcmfmac43455-sdio.clm_blob BCM4345C0.hcd)
case "${mode}" in
    save)
        # Snapshot directory must be new; old snapshots are never overwritten.
        mkdir -- "${backup}"
        mkdir "${backup}/firmware"
        cp -L -- "${boot}/dtb/amlogic/${fdt}" "${backup}/${fdt}"
        for file in "${files[@]}"; do
            [[ ! -f "${firmware}/${file}" ]] || cp -L -- "${firmware}/${file}" "${backup}/firmware/${file}"
        done
        (cd "${backup}" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum >SHA256SUMS)
        ;;
    restore)
        (cd "${backup}" && sha256sum -c SHA256SUMS)
        mkdir -p "${boot}/dtb/amlogic" "${firmware}"
        # Keep the kernel's replacement for comparison without losing the active DTB.
        if [[ -f "${boot}/dtb/amlogic/${fdt}" && ! -e "${backup}/kernel-provided.dtb" ]]; then
            cp -L -- "${boot}/dtb/amlogic/${fdt}" "${backup}/kernel-provided.dtb"
        fi
        cp -- "${backup}/${fdt}" "${boot}/dtb/amlogic/${fdt}.new"
        mv -f -- "${boot}/dtb/amlogic/${fdt}.new" "${boot}/dtb/amlogic/${fdt}"
        for file in "${files[@]}"; do
            if [[ -f "${backup}/firmware/${file}" ]]; then
                cp -- "${backup}/firmware/${file}" "${firmware}/${file}.new"
                mv -f -- "${firmware}/${file}.new" "${firmware}/${file}"
            fi
        done
        ;;
    *) echo "Unknown preservation operation: ${mode}" >&2; exit 1 ;;
esac
