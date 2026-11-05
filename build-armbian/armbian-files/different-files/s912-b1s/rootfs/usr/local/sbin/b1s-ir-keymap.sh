#!/bin/sh
# Find meson-ir by driver, not by rc number; allow asynchronous module probing.
set -eu
command -v ir-keytable >/dev/null || { echo 'b1s-ir-keymap: install ir-keytable' >&2; exit 1; }
[ -s /etc/rc_keymaps/b1s.toml ] || { echo 'b1s-ir-keymap: missing keymap' >&2; exit 1; }
attempt=0
while [ "$attempt" -lt 50 ]; do
    for d in /sys/class/rc/rc*; do
        [ -e "$d/device/driver" ] || continue
        if [ "$(basename "$(readlink -f "$d/device/driver")")" = meson-ir ]; then
            exec ir-keytable -s "$(basename "$d")" -p nec -w /etc/rc_keymaps/b1s.toml
        fi
    done
    attempt=$((attempt + 1))
    sleep 0.2
done
echo 'b1s-ir-keymap: meson-ir not found' >&2
exit 1
