#!/bin/bash
set -euo pipefail
config="${1:?Kernel config is required}"
profile="${2:-stage1}"
[[ -s "${config}" ]] || { echo "Missing kernel config: ${config}" >&2; exit 1; }
required=()
case "${profile}" in
    power-key|core|stage1|adc) required+=(KEYBOARD_GPIO_POLLED) ;;
esac
case "${profile}" in
    gpu|core|stage1|adc) required+=(DRM_PANFROST DEVFREQ_THERMAL THERMAL_OF) ;;
esac
[[ "${profile}" != adc ]] || required+=(KEYBOARD_ADC IIO MESON_SARADC)
case "${profile}" in
    stage1|usb-vbus)
        required+=(REGULATOR_FIXED_VOLTAGE PHY_MESON_GXL_USB2 USB_ROLE_SWITCH
                   USB_DWC3 USB_DWC3_MESON_G12A USB_DWC3_DUAL_ROLE
                   USB_XHCI_HCD USB_XHCI_PLATFORM USB_DWC2 USB_GADGET
                   USB_LIBCOMPOSITE USB_F_MASS_STORAGE USB_MASS_STORAGE)
        # The helper binds/unbinds DWC2 and unloads the gadget before host mode.
        grep -qx 'CONFIG_USB_DWC2_HOST=y' "${config}" && {
            echo 'B1S requires DWC2 peripheral support, not HOST-only.' >&2; exit 1;
        }
        grep -Eq '^CONFIG_USB_DWC2_(DUAL_ROLE|PERIPHERAL)=y$' "${config}" || {
            echo 'B1S requires a DWC2 DUAL_ROLE or PERIPHERAL configuration.' >&2; exit 1;
        }
        grep -qx 'CONFIG_USB_MASS_STORAGE=m' "${config}" || {
            echo 'B1S manual gadget requires CONFIG_USB_MASS_STORAGE=m (not built-in).' >&2; exit 1;
        }
        ;;
esac
case "${profile}" in
    stage1) required+=(RC_CORE IR_MESON IR_NEC_DECODER SND_MESON_AIU SND_MESON_GX_SOUND_CARD) ;;
    hdmi) required+=(SND_MESON_AIU SND_MESON_GX_SOUND_CARD) ;;
esac
# The stage1 and no-adc-keys profiles explicitly disable the unpopulated keypad.
# Other isolated profiles retain the uncalibrated recovered node for comparison.
if [[ "${profile}" != adc && "${profile}" != stage1 && "${profile}" != no-adc-keys ]] &&
    grep -Eq '^CONFIG_KEYBOARD_ADC=[ym]$' "${config}"; then
    echo 'B1S ADC needs calibrated thresholds before using an ADC-enabled kernel.' >&2
    exit 1
fi
for option in "${required[@]}"; do
    grep -Eq "^CONFIG_${option}=[ym]$" "${config}" || {
        echo "B1S ${profile}: CONFIG_${option} must be enabled in ${config}" >&2
        exit 1
    }
done
