#!/usr/bin/env bash
set -euo pipefail
python -V
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python runner.py
