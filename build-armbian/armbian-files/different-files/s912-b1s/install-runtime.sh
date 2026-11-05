#!/bin/bash
# Offline finalization of the staged image, after module/firmware cleanup.
# No chroot, apt, systemctl start, sysfs writes or changes to the running host.
set -euo pipefail
board_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${board_dir}/../../../.." && pwd)"
rootfs="$(realpath -e -- "${1:?Staged rootfs is required}")"
bootfs="$(realpath -e -- "${2:?Staged bootfs is required}")"
kernel="${3:?Kernel release is required}"
[[ "${rootfs}" != / && "${bootfs}" != / && "${bootfs}" != /boot ]] || {
    echo 'B1S runtime installer refuses live root/boot filesystems.' >&2; exit 1;
}
[[ "${kernel}" != */* && "${kernel}" != .* ]] || exit 1
config="${bootfs}/config-${kernel}"
bash "${board_dir}/check-kernel-config.sh" "${config}" stage1
assets=(etc/rc_keymaps/b1s.toml etc/systemd/system/b1s-ir-keymap.service
        etc/systemd/system/b1s-hdmi-audio.service usr/local/sbin/b1s-ir-keymap.sh
        usr/local/sbin/b1s-hdmi-audio.sh usr/local/sbin/b1s-usb-role)
for relative in "${assets[@]}"; do
    mode=0644
    [[ "${relative}" != usr/local/sbin/* ]] || mode=0755
    install -D -m "${mode}" "${board_dir}/rootfs/${relative}" "${rootfs}/${relative}"
done
wants="${rootfs}/etc/systemd/system/multi-user.target.wants"
mkdir -p "${wants}" "${rootfs}/usr/lib/b1s"
for name in b1s-ir-keymap.service b1s-hdmi-audio.service; do
    ln -sfn "../${name}" "${wants}/${name}"
done
audit="${rootfs}/usr/lib/b1s/runtime-audit.json"
python3 "${repo_dir}/tools/b1s/check_runtime.py" "${rootfs}" "${config}" "${board_dir}/rootfs" >"${audit}" || {
    cat "${audit}" >&2; exit 1;
}
(cd "${rootfs}" && sha256sum "${assets[@]}") >"${rootfs}/usr/lib/b1s/runtime-assets.sha256"
cp "${audit}" "${bootfs}/b1s-runtime-audit.json"
{
    echo '# Final runtime configuration (after cleanup):'
    printf 'default_usb_role=host\ngadget_autoload=no\n'
    sha256sum "${audit}" "${rootfs}/usr/lib/b1s/runtime-assets.sha256" | sed "s|${rootfs}/||g"
} >>"${rootfs}/usr/lib/b1s/build-manifest.txt"
cp "${rootfs}/usr/lib/b1s/build-manifest.txt" "${bootfs}/b1s-build-manifest.txt"
