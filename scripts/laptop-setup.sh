#!/usr/bin/env bash
# One-time setup on the laptop, for the mic half. Run it with internet: Moonshine's model
# downloads on first use and there is no internet on the robot's AP.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=python3
if [ "$(uname)" = Darwin ]; then
    # macOS: the sounddevice wheel bundles PortAudio, so nothing to install system-wide.
    # numpy<2 has no wheels past Python 3.12; prefer an older python3.x if there is one.
    for v in 3.11 3.12 3.10; do command -v "python$v" >/dev/null && { PY="python$v"; break; }; done
    echo "== macOS: using $PY. The first mic open asks for Microphone access for your terminal/VS Code."
else
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends python3-venv python3-pip libportaudio2
fi

[ -d venv-laptop ] || "$PY" -m venv venv-laptop
./venv-laptop/bin/pip install --upgrade pip
./venv-laptop/bin/pip install -r requirements-laptop.txt

echo "== mics the laptop can see:"
./venv-laptop/bin/python -m sounddevice

cat <<'MSG'

== set MIC_DEVICE in .env to a substring of your mic's name, then:

    ./venv-laptop/bin/python laptop/listen.py

The first run downloads the Moonshine model. Do that now, while you still have internet.
MSG
