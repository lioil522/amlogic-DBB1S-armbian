#!/bin/bash
# Called after the kernel DTB archive has been unpacked, before B1S cleanup.
set -euo pipefail
board_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
bootfs="${1:?Boot filesystem is required}"
rootfs="${2:?Root filesystem is required}"
kernel="${3:?Kernel release is required}"
profile="${4:-stage1}"
target="${bootfs}/dtb/amlogic/meson-gxm-dangbei-b1s.dtb"
bash "${board_dir}/build-dtb.sh" "${profile}" "${target}" "${bootfs}/config-${kernel}"
manifest_dir="${rootfs}/usr/lib/b1s"
mkdir -p "${manifest_dir}"
{
    printf 'profile=%s\nkernel=%s\n' "${profile}" "${kernel}"
    printf 'repository_commit=%s\n' "$(git -C "${board_dir}" rev-parse HEAD 2>/dev/null || echo unknown)"
    printf 'dtc=%s\n' "$(dtc --version)"
    printf 'dtb_sha256=%s\n' "$(sha256sum "${target}" | cut -d' ' -f1)"
    printf 'config_sha256=%s\n' "$(sha256sum "${bootfs}/config-${kernel}" | cut -d' ' -f1)"
    echo '# Source hashes (also identify uncommitted changes):'
    cd "${board_dir}"
    find dts -type f -print0 | sort -z | xargs -0 sha256sum
    find rootfs -type f -print0 | sort -z | xargs -0 sha256sum
    sha256sum build-dtb.sh check-kernel-config.sh install-dtb.sh install-runtime.sh install-runtime-dependencies.sh
    sha256sum ../../../../tools/b1s/check_runtime.py
} >"${manifest_dir}/build-manifest.txt"
cp "${manifest_dir}/build-manifest.txt" "${bootfs}/b1s-build-manifest.txt"
