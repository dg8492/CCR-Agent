#!/bin/bash
# Meridian — macOS Launcher
# Double-click this file in Finder to start Meridian

# Always run from the folder containing this script
cd "$(dirname "$0")"

echo "============================================"
echo "  Meridian — Catalyst Capital Research"
echo "============================================"
echo ""

# ── Check Python 3 ────────────────────────────────────────────────────────
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 not found."
    echo ""
    echo "To install it:"
    echo "  1. Install Homebrew:  https://brew.sh"
    echo "  2. Run: brew install python3"
    echo ""
    read -rp "Press Enter to close..."
    exit 1
fi

echo "Python $(python3 --version) detected."
echo ""

# ── Virtual environment (created once, reused every run) ──────────────────
VENV=".venv"
if [ ! -d "$VENV" ]; then
    echo "First run — setting up environment (takes ~30 seconds)..."
    python3 -m venv "$VENV"
    echo ""
fi

source "$VENV/bin/activate"

# ── Install / update dependencies ─────────────────────────────────────────
echo "Checking dependencies..."
pip install -q -r requirements.txt
echo "Ready."
echo ""

# ── Launch ────────────────────────────────────────────────────────────────
echo "Starting Meridian at http://localhost:5051"
echo "Your browser will open automatically."
echo ""
echo "To stop Meridian: press Ctrl+C or close this window."
echo ""

# Open browser after a short delay so Flask has time to start
(sleep 2 && open http://localhost:5051) &

python3 app.py
