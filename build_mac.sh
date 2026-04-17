#!/bin/bash
# Packages Meridian for macOS distribution.
# Run this on Windows (Git Bash) or Mac to produce Meridian-macOS.zip.

set -e

echo "============================================"
echo " Meridian -- macOS Package"
echo "============================================"
echo ""

# Make the launcher executable inside the zip (macOS will respect this)
chmod +x launch_mac.command 2>/dev/null || true

echo "Creating Meridian-macOS.zip..."

# Build the zip with only what Mac users need
# Copy .env as .mrd so it's less obvious in the package
cp .env _config

zip -r Meridian-macOS.zip \
    INSTRUCTIONS.txt \
    app.py \
    document_loader.py \
    requirements.txt \
    launch_mac.command \
    ui/ \
    docs/ \
    clients.json \
    _config \
    --exclude "*.pyc" \
    --exclude "__pycache__/*" \
    --exclude ".venv/*" \
    --exclude "*.log"

rm _config

echo ""
echo "============================================"
echo " DONE: Meridian-macOS.zip"
echo "============================================"
echo ""
echo " Send the ZIP to the Mac user."
echo " They unzip it and double-click launch_mac.command."
echo ""
echo " First run: downloads dependencies (~30 sec, one-time)."
echo " Every run after that: launches in seconds."
echo ""
echo " Requires: Python 3 (brew install python3)"
echo " macOS will ask to allow Terminal access on first run — click OK."
echo ""
