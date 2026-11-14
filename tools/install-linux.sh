#!/bin/sh
# Installs Tuckvault for the current Linux user, without root.
#
#   sh tools/install-linux.sh              install or update
#   sh tools/install-linux.sh --uninstall  remove the program (settings and backups stay)
#
# What it creates:
#   ~/.local/share/tuckvault/venv             the program and its one library
#   ~/.local/bin/tuckvault                    the command
#   ~/.local/share/applications/tuckvault.desktop   the menu entry
# Settings, the key and history live in ~/.config/tuckvault and are never touched here.
#
# Needs Python 3.10 or newer with venv and tkinter. On Debian and Ubuntu:
#   sudo apt install python3-venv python3-tk
set -eu

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
PREFIX="$DATA/tuckvault"
VENV="$PREFIX/venv"
BIN="$HOME/.local/bin"
APPS="$DATA/applications"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

if [ "${1:-}" = "--uninstall" ]; then
    if [ -x "$VENV/bin/tuckvault" ]; then
        "$VENV/bin/tuckvault" schedule remove || true
    fi
    rm -f "$BIN/tuckvault" "$APPS/tuckvault.desktop" "$PREFIX/tuckvault.png"
    rm -rf "$VENV"
    rmdir "$PREFIX" 2>/dev/null || true
    echo "Tuckvault removed. Your settings (~/.config/tuckvault) and your backups were left alone."
    exit 0
fi

if [ ! -f "$HERE/tuckvault/__init__.py" ]; then
    echo "Run this from an unpacked Tuckvault download: sh tools/install-linux.sh" >&2
    exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    echo "Tuckvault needs Python 3.10 or newer (python3)." >&2
    exit 1
fi
if ! python3 -c 'import tkinter' 2>/dev/null; then
    echo "Note: tkinter is missing, so only the command line will work." >&2
    echo "      For the window: sudo apt install python3-tk (or your distribution's equivalent)." >&2
fi

mkdir -p "$PREFIX" "$BIN" "$APPS"
if ! python3 -m venv "$VENV"; then
    echo "Could not create a Python environment. On Debian and Ubuntu: sudo apt install python3-venv" >&2
    exit 1
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$HERE/requirements.txt"
"$VENV/bin/python" -m pip install --quiet --no-deps "$HERE"
ln -sf "$VENV/bin/tuckvault" "$BIN/tuckvault"
cp "$HERE/tuckvault/assets/tuckvault.png" "$PREFIX/tuckvault.png"

cat > "$APPS/tuckvault.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Tuckvault
Comment=Encrypted, verified backups
Exec="$VENV/bin/tuckvault-gui"
Icon=$PREFIX/tuckvault.png
Terminal=false
Categories=Utility;Archiving;
EOF

# Timers made by an earlier install point at the same program path; refresh them anyway.
"$VENV/bin/python" -c 'from tuckvault import config, scheduler; scheduler.refresh(config.load())' || true

echo "Tuckvault $("$VENV/bin/python" -c 'import tuckvault; print(tuckvault.__version__)') installed."
echo "Open it from your applications menu, or run: tuckvault"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "Note: $BIN is not on your PATH; use $VENV/bin/tuckvault or add it." ;;
esac
