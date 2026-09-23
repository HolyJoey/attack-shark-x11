#!/usr/bin/env bash
# Installs the Attack Shark X11 app for the current user.
set -euo pipefail

APP_ID="dev.local.AttackSharkX11"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$PREFIX/attack-shark-x11"
BIN_DIR="$HOME/.local/bin"
RULE="/etc/udev/rules.d/70-attack-shark-x11.rules"
OLD_RULE="/etc/udev/rules.d/99-attack-shark-x11.rules"  # earlier versions; too late for uaccess

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }

say "Checking requirements"
missing=()
command -v python3 >/dev/null || missing+=("python3")
python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')" 2>/dev/null \
  || missing+=("python3 GTK4 + libadwaita bindings (Debian/Ubuntu: python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 · Fedora: python3-gobject gtk4 libadwaita · Arch: python-gobject gtk4 libadwaita)")
if ((${#missing[@]})); then
  printf 'Missing:\n'; printf '  - %s\n' "${missing[@]}"; exit 1
fi
echo "OK"

say "Installing to $APP_DIR"
mkdir -p "$APP_DIR" "$BIN_DIR"
cp -r "$SRC"/*.py "$SRC"/x11.png "$APP_DIR/"
[ -d "$SRC/protocol-reference" ] && cp -r "$SRC/protocol-reference" "$APP_DIR/"

cat > "$BIN_DIR/attack-shark" <<EOF
#!/usr/bin/env bash
exec python3 "$APP_DIR/app.py" "\$@"
EOF
chmod +x "$BIN_DIR/attack-shark"

say "Adding the menu entry and icon"
ICONS="$PREFIX/icons/hicolor"
mkdir -p "$ICONS/64x64/apps" "$PREFIX/applications"
cp "$SRC/x11.png" "$ICONS/64x64/apps/attack-shark-x11.png"
cat > "$PREFIX/applications/attack-shark-x11.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Attack Shark X11
Comment=Configure DPI, buttons, lighting, polling rate and battery
Exec=$BIN_DIR/attack-shark
Icon=attack-shark-x11
Terminal=false
Categories=Utility;Settings;HardwareSettings;
StartupWMClass=$APP_ID
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$PREFIX/applications" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -qf "$ICONS" 2>/dev/null || true

say "Device permissions"
if [ ! -f "$OLD_RULE" ] && [ -f "$RULE" ] && cmp -s "$SRC/udev/70-attack-shark-x11.rules" "$RULE"; then
  echo "Rule already installed."
else
  echo "The mouse's HID nodes are root-only by default, so the app needs one udev rule:"
  cat "$SRC/udev/70-attack-shark-x11.rules"
  read -rp "Install it with sudo? [Y/n] " reply
  if [[ ${reply:-Y} =~ ^[Yy]?$ ]]; then
    sudo rm -f "$OLD_RULE"
    sudo cp "$SRC/udev/70-attack-shark-x11.rules" "$RULE"
    sudo udevadm control --reload
    sudo udevadm trigger --subsystem-match=hidraw --action=add
    echo "Installed. If the mouse still isn't found, replug the receiver."
  else
    echo "Skipped. Install it later with:"
    echo "  sudo rm -f $OLD_RULE && sudo cp $SRC/udev/70-attack-shark-x11.rules $RULE && sudo udevadm control --reload"
  fi
fi

say "Done"
echo "Launch it from your menu, or run: attack-shark"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "Note: $BIN_DIR is not on your PATH." ;;
esac
