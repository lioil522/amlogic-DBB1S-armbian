#!/bin/sh
# The B1S DT connects HDMI I2S IN, not HDMI PCM IN. Use PCM device 0 for playback.
set -eu
command -v amixer >/dev/null || { echo 'b1s-hdmi-audio: install alsa-utils' >&2; exit 1; }
attempt=0
while [ "$attempt" -lt 50 ]; do
    if amixer -c 0 sget 'AIU HDMI CTRL SRC' >/dev/null 2>&1; then
        amixer -c 0 sset 'AIU HDMI CTRL SRC' I2S >/dev/null
        exit 0
    fi
    attempt=$((attempt + 1))
    sleep 0.2
done
echo 'b1s-hdmi-audio: AIU sound card 0 not found' >&2
exit 1
