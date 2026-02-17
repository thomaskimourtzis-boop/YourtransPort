#!/bin/zsh

# zsh fix: μην σπας αν δεν βρεθούν αρχεία (*.spec κλπ)
setopt NO_NOMATCH
set -e

APP_NAME="YOURTRANS OIL"
SRC="TOKOI_GUI.py"
ICON="icon.icns"

echo "🔨 Building $APP_NAME ..."

# Καθαρισμός παλιών builds
rm -rf build dist
rm -f *.spec || true

# Build
python3 -m PyInstaller --windowed --onedir --clean \
  --name "$APP_NAME" \
  --icon "$ICON" \
  --collect-submodules PySide6 \
  "$SRC"

# Αφαίρεση quarantine
xattr -dr com.apple.quarantine "dist/$APP_NAME.app"

# Εγκατάσταση στα Applications
rm -rf "/Applications/$APP_NAME.app"
cp -R "dist/$APP_NAME.app" "/Applications/"

# Καθαρίζουμε τον project φάκελο για να μείνει μικρός
rm -rf build dist
rm -f *.spec || true

echo "✅ Done! $APP_NAME εγκαταστάθηκε στα Applications."
