#!/bin/bash
# Explicit CI-only preparation of a staged ARM64 rootfs. Offline audit is separate.
set -euo pipefail
board_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "${board_dir}/../../../.." && pwd)"
rootfs="$(realpath -e -- "${1:?Staged rootfs is required}")"
case "${rootfs}" in
    /|/boot|/etc|/usr|/var|/root) echo 'B1S dependency installer refuses live system paths.' >&2; exit 1 ;;
esac
# Parent directory symlinks must never redirect writes or mounts into the host.
# Leaf resolv.conf/policy-rc.d links are deliberately saved without following them.
python3 - "${rootfs}" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
for relative in ("etc", "usr", "usr/bin", "usr/sbin", "dev", "proc", "var", "var/lib", "var/lib/dpkg"):
    path = root / relative
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise SystemExit("Unsafe staged rootfs directory: " + relative)
    live = Path("/") / relative
    if path.exists() and live.exists() and path.samefile(live):
        raise SystemExit("Staged rootfs aliases a live directory: " + relative)
PY
missing="$(python3 "${repo_dir}/tools/b1s/check_runtime.py" --missing-packages "${rootfs}")"
[[ -n "${missing}" ]] || { echo 'B1S runtime packages already installed; no chroot needed.'; exit 0; }
mapfile -t packages <<<"${missing}"
[[ "$(id -u)" == 0 ]] || { echo 'Staged package installation requires root.' >&2; exit 1; }
[[ -x "${rootfs}/usr/bin/apt-get" ]] || { echo 'Target apt-get is missing.' >&2; exit 1; }
for directory in dev proc; do
    if mountpoint -q "${rootfs}/${directory}"; then
        echo "Refusing pre-mounted target ${directory}." >&2
        exit 1
    fi
done
case "$(uname -m)" in
    aarch64|arm64) ;;
    *)
        registration=/proc/sys/fs/binfmt_misc/qemu-aarch64
        [[ -r "${registration}" ]] && grep -qx enabled "${registration}" &&
            grep -Eq '^flags:.*F' "${registration}" || {
            echo 'Enable persistent ARM64 binfmt first: install qemu-user-static binfmt-support; update-binfmts --enable qemu-aarch64' >&2
            exit 1
        }
        ;;
esac
work="$(mktemp -d "$(dirname -- "${rootfs}")/.b1s-deps.XXXXXX")"
dev_mounted=no
proc_mounted=no
policy_changed=no
dns_changed=no
cleanup() {
    status=$?
    trap - EXIT
    if [[ "${proc_mounted}" == yes ]]; then umount "${rootfs}/proc" || status=1; fi
    if [[ "${dev_mounted}" == yes ]]; then umount "${rootfs}/dev" || status=1; fi
    if [[ "${policy_changed}" == yes ]]; then
        rm -f -- "${rootfs}/usr/sbin/policy-rc.d" || status=1
        if [[ -e "${work}/policy-rc.d" || -L "${work}/policy-rc.d" ]]; then
            mv -- "${work}/policy-rc.d" "${rootfs}/usr/sbin/policy-rc.d" || status=1
        fi
    fi
    if [[ "${dns_changed}" == yes ]]; then
        rm -f -- "${rootfs}/etc/resolv.conf" || status=1
        if [[ -e "${work}/resolv.conf" || -L "${work}/resolv.conf" ]]; then
            mv -- "${work}/resolv.conf" "${rootfs}/etc/resolv.conf" || status=1
        fi
    fi
    rmdir -- "${work}" || status=1
    exit "${status}"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -p "${rootfs}/usr/sbin" "${rootfs}/etc" "${rootfs}/dev" "${rootfs}/proc"
if [[ -e "${rootfs}/usr/sbin/policy-rc.d" || -L "${rootfs}/usr/sbin/policy-rc.d" ]]; then
    mv -- "${rootfs}/usr/sbin/policy-rc.d" "${work}/policy-rc.d"
fi
policy_changed=yes
printf '#!/bin/sh\nexit 101\n' >"${rootfs}/usr/sbin/policy-rc.d"
chmod 0755 "${rootfs}/usr/sbin/policy-rc.d"
if [[ -e "${rootfs}/etc/resolv.conf" || -L "${rootfs}/etc/resolv.conf" ]]; then
    mv -- "${rootfs}/etc/resolv.conf" "${work}/resolv.conf"
fi
dns_changed=yes
cp -L -- /etc/resolv.conf "${rootfs}/etc/resolv.conf"
# These mounts exist only inside the CI staging directory; never bind /sys or /boot.
mount --bind /dev "${rootfs}/dev"
dev_mounted=yes
mount --make-private "${rootfs}/dev"
mount -t proc proc "${rootfs}/proc"
proc_mounted=yes
printf 'Installing missing B1S target packages: %s\n' "${packages[*]}"
chroot "${rootfs}" /usr/bin/env DEBIAN_FRONTEND=noninteractive \
    /usr/bin/apt-get -o Acquire::Retries=3 -o APT::Update::Error-Mode=any update
chroot "${rootfs}" /usr/bin/env DEBIAN_FRONTEND=noninteractive \
    /usr/bin/apt-get -o Acquire::Retries=3 -o DPkg::Use-Pty=0 -y --no-install-recommends install "${packages[@]}"
remaining="$(python3 "${repo_dir}/tools/b1s/check_runtime.py" --missing-packages "${rootfs}")"
[[ -z "${remaining}" ]] || { printf 'Target packages still missing: %s\n' "${remaining}" >&2; exit 1; }
