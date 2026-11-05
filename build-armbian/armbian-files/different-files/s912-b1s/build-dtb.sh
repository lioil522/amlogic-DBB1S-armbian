#!/bin/bash
# Build an isolated B1S DTB. No kernel checkout or network access required.
set -euo pipefail
board_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
profile="${1:-stage1}"
output="${2:?Usage: bash build-dtb.sh PROFILE OUTPUT.dtb [KERNEL_CONFIG]}"
config="${3:-}"
case "${profile}" in
    baseline) fragments=() ;;
    power-key) fragments=(fixes/power-key.dtsi) ;;
    gpu) fragments=(fixes/gpu-cooling.dtsi) ;;
    hdmi) fragments=(fixes/hdmi-active-low.dtsi) ;;
    no-adc-keys) fragments=(fixes/no-adc-keys.dtsi) ;;
    core) fragments=(fixes/power-key.dtsi fixes/gpu-cooling.dtsi) ;;
    stage1) fragments=(fixes/power-key.dtsi fixes/gpu-cooling.dtsi fixes/hdmi-active-low.dtsi fixes/no-adc-keys.dtsi fixes/usb-vbus.dtsi) ;;
    usb-vbus) fragments=(fixes/usb-vbus.dtsi) ;;
    *) echo "Unknown B1S profile: ${profile}" >&2; exit 1 ;;
esac
if [[ -n "${config}" ]]; then
    bash "${board_dir}/check-kernel-config.sh" "${config}" "${profile}"
fi
mkdir -p -- "$(dirname -- "${output}")"
work="$(mktemp -d "$(dirname -- "${output}")/.b1s-dtb.XXXXXX")"
trap 'rm -rf -- "${work}"' EXIT
if [[ "${profile}" == stage1 ]]; then
    cp "${board_dir}/dts/meson-gxm-dangbei-b1s.dts" "${work}/input.dts"
else
    {
        echo '/dts-v1/;'
        echo '/include/ "meson-gxm-dangbei-b1s-baseline.dtsi"'
        for fragment in "${fragments[@]}"; do
            printf '/include/ "%s"\n' "${fragment}"
        done
    } >"${work}/input.dts"
fi
dtc -@ -I dts -O dtb -i "${board_dir}/dts" \
    -o "${work}/output.dtb" "${work}/input.dts"
[[ -s "${work}/output.dtb" ]]
mv -f -- "${work}/output.dtb" "${output}"
printf 'B1S profile=%s dtc=%s\n' "${profile}" "$(dtc --version)"
sha256sum "${output}"
