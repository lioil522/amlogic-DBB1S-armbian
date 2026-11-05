#!/bin/bash
# Prepare a private, pinned kernel worktree. Build outputs stay under WORK.
# Usage: CROSS_COMPILE=aarch64-linux-gnu- bash prepare-kernel.sh SOURCE CONFIG WORK
set -euo pipefail
board_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source_dir="$(realpath -e -- "${1:?Source Git checkout required}")"
config="$(realpath -e -- "${2:?Full kernel config required}")"
work="$(realpath -m -- "${3:?New work directory required}")"
commit=e0497a975383cedc8b8d8a7c9e84eac2e2238a6e
fail() { echo "B1S kernel: $*" >&2; exit 1; }
check_usb_config() {
    local option
    for option in USB_DWC2 USB_GADGET USB_DWC3 USB_DWC3_MESON_G12A USB_ROLE_SWITCH PHY_MESON_GXL_USB2; do
        grep -Eq "^CONFIG_${option}=[ym]$" "$1" || fail "CONFIG_${option} is required"
    done
    grep -Eq '^CONFIG_USB_DWC2_(DUAL_ROLE|PERIPHERAL)=y$' "$1" || fail 'DWC2 device support is required'
    grep -Eq '^CONFIG_USB_DWC3_(DUAL_ROLE|HOST)=y$' "$1" || fail 'DWC3 host support is required'
}
[[ ! -e "${work}" ]] || fail "Work directory already exists: ${work}"
[[ "$(git -C "${source_dir}" rev-parse HEAD)" == "${commit}" ]] || fail "Expected source commit ${commit}"
[[ -z "$(git -C "${source_dir}" status --porcelain --untracked-files=all)" ]] || fail 'Source checkout must be clean'
[[ "$(git -C "${source_dir}" config --bool core.sparseCheckout || true)" != true ]] || fail 'Full source checkout required'
[[ -s "${source_dir}/Makefile" && -s "${source_dir}/scripts/kconfig/Makefile" ]] || fail 'Incomplete source checkout'
bash "${board_dir}/check-kernel-config.sh" "${config}" stage1
check_usb_config "${config}"
cross="${CROSS_COMPILE:-}"
cc="$(command -v "${CC:-${cross}gcc}")" || fail 'C compiler missing'
hostcc="$(command -v "${HOSTCC:-gcc}")" || fail 'Host compiler missing'
ld="$(command -v "${cross}ld")" || fail 'Linker missing'
make_bin="$(command -v make)" || fail 'make missing'
[[ "$("${cc}" -dumpmachine)" == aarch64* ]] || fail 'Compiler must target aarch64; set CROSS_COMPILE on x86'
for tool in git patch dtc flex bison bc openssl sha256sum python3; do
    command -v "${tool}" >/dev/null || fail "Missing dependency: ${tool}"
done
mkdir -p -- "${work}/inputs" "${work}/build"
cp -- "${config}" "${work}/inputs/base.config"
cp -a -- "${board_dir}/kernel/patches" "${board_dir}/dts" "${work}/inputs/"
cp -- "${BASH_SOURCE[0]}" "${work}/inputs/prepare-kernel.sh"
git -C "${source_dir}" worktree add --detach "${work}/source" "${commit}"
kernel="${work}/source"
for file in "${work}/inputs/patches/"*.patch; do
    patch --batch --forward --fuzz=0 --dry-run -d "${kernel}" -p1 < "${file}"
    patch --batch --forward --fuzz=0 -d "${kernel}" -p1 < "${file}"
done
# The candidate release is explicit; upstream localversion files are recorded
# by source-changes.patch rather than silently adding another suffix.
find "${kernel}" -maxdepth 1 -type f -name 'localversion*' -delete
# Keep the board includes under a dedicated namespace in the kernel tree.
amlogic="${kernel}/arch/arm64/boot/dts/amlogic"
[[ ! -e "${amlogic}/b1s" ]] || fail 'Unexpected b1s source directory'
cp -a -- "${work}/inputs/dts" "${amlogic}/b1s"
sed 's|/include/ "|/include/ "b1s/|' "${work}/inputs/dts/meson-gxm-dangbei-b1s.dts" > "${amlogic}/meson-gxm-dangbei-b1s.dts"
if ! grep -Eq '(^|[[:space:]])meson-gxm-dangbei-b1s\.dtb([[:space:]]|$)' "${amlogic}/Makefile"; then
    printf '\ndtb-$(CONFIG_ARCH_MESON) += meson-gxm-dangbei-b1s.dtb\n' >> "${amlogic}/Makefile"
fi
cp -- "${config}" "${work}/build/.config"
"${kernel}/scripts/config" --file "${work}/build/.config" \
    --set-str LOCALVERSION '-b1s-pm1' --disable LOCALVERSION_AUTO
export KBUILD_BUILD_TIMESTAMP='2026-11-02 00:00:00 UTC' KBUILD_BUILD_USER=b1s KBUILD_BUILD_HOST=builder KBUILD_BUILD_VERSION=1
make_args=(-C "${kernel}" "O=${work}/build" ARCH=arm64 "CROSS_COMPILE=${cross}" "CC=${cc}" "HOSTCC=${hostcc}" "LD=${ld}" LOCALVERSION=)
"${make_bin}" "${make_args[@]}" olddefconfig
bash "${board_dir}/check-kernel-config.sh" "${work}/build/.config" stage1
check_usb_config "${work}/build/.config"
cp -- "${work}/build/.config" "${work}/inputs/resolved.config"
diff -u "${work}/inputs/base.config" "${work}/inputs/resolved.config" > "${work}/inputs/config.diff" || [[ "$?" == 1 ]]
"${make_bin}" -s "${make_args[@]}" kernelrelease > "${work}/inputs/kernelrelease"
[[ "$(cat "${work}/inputs/kernelrelease")" == 6.18.48-b1s-pm1 ]] || fail 'Unexpected kernel release'
git -C "${kernel}" add -N -- arch/arm64/boot/dts/amlogic/b1s arch/arm64/boot/dts/amlogic/meson-gxm-dangbei-b1s.dts
git -C "${kernel}" diff --binary --no-ext-diff > "${work}/inputs/source-changes.patch"
{
    printf 'source_url=https://github.com/ophub/linux-6.18.y.git\nsource_commit=%s\n' "${commit}"
    printf 'patch_upstream=0287c960b15f27498d85cadf584bb59301ea2382\nprofile=stage1\n'
    printf 'compiler=%s\n' "${cc}"
    "${cc}" --version
    "${ld}" --version
    "${hostcc}" --version
    "${make_bin}" --version
    dtc --version
    if command -v dpkg-query >/dev/null; then
        dpkg-query -W -f='${Package}\t${Version}\n'
    fi
    printf 'build_timestamp=%s\n' "${KBUILD_BUILD_TIMESTAMP}"
} > "${work}/inputs/provenance.txt"
# Pin available tool binaries and input files; preserve full source identity separately.
(
    cd -- "${work}"
    find inputs -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum
    sha256sum "${cc}" "${hostcc}" "${ld}" "${make_bin}"
    for tool in cc1 as; do
        compiler_tool="$("${cc}" -print-prog-name="${tool}")"
        [[ "${compiler_tool}" == */* ]] || compiler_tool="$(command -v "${compiler_tool}")"
        sha256sum "${compiler_tool}"
    done
) > "${work}/SHA256SUMS"
{
    echo '#!/bin/bash'
    echo 'set -euo pipefail'
    printf 'cd -- %q\n' "${work}"
    echo 'sha256sum --check --quiet SHA256SUMS'
    echo 'cmp inputs/resolved.config build/.config'
    printf '[[ "$(git -C source rev-parse HEAD)" == %q ]]\n' "${commit}"
    echo 'git -C source diff --binary --no-ext-diff | cmp - inputs/source-changes.patch'
    echo '[[ -z "$(git -C source ls-files --others --exclude-standard)" ]]'
    printf 'export KBUILD_BUILD_TIMESTAMP=%q KBUILD_BUILD_USER=b1s KBUILD_BUILD_HOST=builder KBUILD_BUILD_VERSION=1\n' "${KBUILD_BUILD_TIMESTAMP}"
    printf 'exec %q ' "${make_bin}"
    printf '%q ' "${make_args[@]}"
    echo '"-j${JOBS:-2}" "${@:-Image}"'
} > "${work}/compile.sh"
chmod +x "${work}/compile.sh"
printf 'Prepared %s\nBuild: bash %q Image modules dtbs\nDebs: bash %q bindeb-pkg\n' "$(cat "${work}/inputs/kernelrelease")" "${work}/compile.sh" "${work}/compile.sh"
echo 'Review inputs/config.diff before building. This is a new candidate build, not a byte-for-byte reproduction of the running kernel.'
