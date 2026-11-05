#!/bin/bash
set -euo pipefail
root="${1:?Root filesystem path is required}"
[[ -d "${root}/etc" ]] || exit 1
if grep -Eq "^(BUILD_DESKTOP=['\"]?(yes|true)|DESKTOP_ENVIRONMENT=['\"]?[a-zA-Z])" \
    "${root}/etc/armbian-release" 2>/dev/null || \
    [[ -n "$(find "${root}/usr/share/xsessions" "${root}/usr/share/wayland-sessions" -maxdepth 1 -name '*.desktop' -print -quit 2>/dev/null || true)" ]]; then
    echo desktop
else
    echo server
fi
