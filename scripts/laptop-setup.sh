#!/usr/bin/env bash
# One-time setup on the laptop, for the mic half. Run it with internet: Moonshine's model
# downloads on first use and there is no internet on the robot's AP.
set -euo pipefail
cd "$(dirname "$0")/.."

sudo apt-get update
sudo apt-get install -y --no-install-recommends python3-venv python3-pip libportaudio2

[ -d venv-laptop ] || python3 -m venv venv-laptop
./venv-laptop/bin/pip install --upgrade pip
./venv-laptop/bin/pip install -r requirements-laptop.txt

echo "== mics the laptop can see:"
./venv-laptop/bin/python -m sounddevice

cat <<'MSG'

== set MIC_DEVICE in .env to a substring of your mic's name, then:

    ./venv-laptop/bin/python laptop/listen.py

The first run downloads the Moonshine model. Do that now, while you still have internet.
MSG
