#!/usr/bin/env bash
# One-time setup on the Pi. Run it while the Pi still has internet (on your router), before you
# move it to the robot's AP -- it downloads Piper's models, and nothing here reaches the network
# again afterwards.
#
#   bash scripts/setup-pi.sh            # stages 1-2: speech + the robot link
#   VISION=1 bash scripts/setup-pi.sh   # also stages 3-4: OpenCV + MediaPipe (big)
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== apt: sox (voice effects), alsa-utils (aplay), portaudio (mic, if ever on the Pi),
        a compiler + headers (pyworld and pyaudio, which unitree_webrtc_connect pulls in,
        have no arm64 wheel, so pip builds them)"
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    python3-venv python3-pip python3-dev build-essential sox alsa-utils libportaudio2 \
    portaudio19-dev

echo "== venv"
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements-pi.txt
[ "${VISION:-}" = "1" ] && ./venv/bin/pip install -r requirements-pi-vision.txt

echo "== piper voices -> ./piper-voices (~550 MB for all of them)"
mkdir -p piper-voices
# The models behind the 103 named voices in pi/voices.py. Trim this list if space is tight:
# en_US-libritts_r-medium is the kid voice; en_US-ryan-medium alone covers scooby, robot and the
# ryan-* styles.
./venv/bin/python -m piper.download_voices --data-dir piper-voices \
    en_US-ryan-medium en_US-amy-medium en_US-lessac-medium en_US-kristin-medium \
    en_US-hfc_female-medium en_GB-jenny_dioco-medium en_GB-semaine-medium \
    en_US-arctic-medium en_GB-vctk-medium en_US-libritts_r-medium

[ -f .env ] || { cp .env.example .env; echo "== created .env: put GO2_AES_KEY in it"; }

cat <<'MSG'

== done. Check the speaker, then start the server:

    aplay -L                                  # find the card, set ALSA_DEVICE in .env
    ./venv/bin/python pi/server.py

Then from the laptop, on the same network as the Pi:

    http://<pi>:8080
MSG
