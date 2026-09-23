#!/usr/bin/env bash
# Removes the Attack Shark X11 app. Settings in ~/.config are kept unless you ask.
set -euo pipefail

PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$PREFIX/attack-shark-x11"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/attack-shark-x11"
RULE="/etc/udev/rules.d/70-attack-shark-x11.rules"
OLD_RULE="/etc/udev/rules.d/99-attack-shark-x11.rules"

rm -rf "$APP_DIR"
rm -f "$HOME/.local/bin/attack-shark"
rm -f "$PREFIX/applications/attack-shark-x11.desktop"
rm -f "$PREFIX/icons/hicolor/64x64/apps/attack-shark-x11.png"
command -v update-desktop-database >/dev/null && update-desktop-database "$PREFIX/applications" || true
echo "App removed."

if [ -f "$RULE" ] || [ -f "$OLD_RULE" ]; then
  read -rp "Also remove the udev rule (needs sudo)? [y/N] " reply
  [[ ${reply:-N} =~ ^[Yy]$ ]] && sudo rm -f "$RULE" "$OLD_RULE" && sudo udevadm control --reload && echo "Rule removed."
fi

if [ -d "$CONFIG_DIR" ]; then
  read -rp "Also delete your profiles and macros in $CONFIG_DIR? [y/N] " reply
  [[ ${reply:-N} =~ ^[Yy]$ ]] && rm -rf "$CONFIG_DIR" && echo "Settings deleted."
fi
