#!/usr/bin/env bash
# Omo Space installer (Linux x86_64 and macOS). Windows: scripts/install.ps1.
#
# From an unpacked release:      ./install.sh
# From a downloaded tarball:     ./install.sh omo-research-<ver>-linux-x64.tar.gz
# From the private repo (gh):    gh release download -R omoresearch/omo-space -p '*.tar.gz' -p install.sh
#                                bash install.sh omo-research-*-linux-x64.tar.gz
#
# Installs the app into ~/.local/share/omo/app (macOS: ~/Library/Application
# Support/OmoResearch/app), adds "Omo Space" to your application menu (or
# an `omo-research` command on macOS), then runs the launcher's setup
# (downloads Minecraft 26.2, Fabric, CC: Tweaked, MCEF, Java and, on Linux,
# tmux from their official sources, hash-checked). Needs no root. Never
# touches your own Minecraft/Prism installs, worlds or Hermes configuration.
# macOS needs tmux and Python 3.11+ first:  brew install tmux python
#
#   --no-setup     install the files only; run setup later from the app
#   --uninstall    remove the app, menu entry and services (keeps worlds + workspaces)
set -euo pipefail

OS="$(uname -s)"
if [ "$OS" = Darwin ]; then
  APP="${OMO_APP_DIR:-$HOME/Library/Application Support/OmoResearch/app}"
else
  APP="${OMO_APP_DIR:-$HOME/.local/share/omo/app}"
fi
DESKTOP="$HOME/.local/share/applications/omo-research.desktop"
BIN="$HOME/.local/bin"
say() { printf '\033[1;33m▸\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

SETUP=1; SRC=""
for arg in "$@"; do
  case "$arg" in
    --no-setup) SETUP=0 ;;
    --uninstall)
      if [ "$OS" = Darwin ]; then
        for label in space.omo.research.bridge space.omo.research.tmux; do
          plist="$HOME/Library/LaunchAgents/$label.plist"
          if [ -f "$plist" ]; then
            say "stopping $label"
            launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
            rm -f "$plist"
          fi
        done
        rm -rf "$APP" "$BIN/omo-research"
        say "removed. Kept: ~/Library/Application Support/OmoResearch (game, worlds, workspaces)."
        exit 0
      fi
      # systemctl --user talks to the login session's manager whatever $HOME
      # says, so only stop a unit that manager loaded from this $HOME. An
      # uninstall in a throwaway HOME must never end the real session server.
      UNITS_DIR="$HOME/.config/systemd/user"; OURS=()
      if [ -z "${OMO_NO_SERVICES:-}" ]; then
        for unit in omo-bridge.service omo-tmux.service; do
          loaded="$(systemctl --user show -p FragmentPath --value "$unit" 2>/dev/null || true)"
          [ -n "$loaded" ] && [ "$loaded" -ef "$UNITS_DIR/$unit" ] && OURS+=("$unit")
        done
      fi
      if [ "${#OURS[@]}" -gt 0 ]; then
        say "stopping Omo services"
        systemctl --user disable --now "${OURS[@]}" 2>/dev/null || true
      else
        say "services: none running from this install; left alone"
      fi
      rm -f "$UNITS_DIR/omo-bridge.service" "$UNITS_DIR/omo-tmux.service"
      [ "${#OURS[@]}" -gt 0 ] && systemctl --user daemon-reload 2>/dev/null || true
      rm -rf "$APP" "$DESKTOP" "$BIN/omo-research"
      say "removed. Kept: ~/.local/share/omo/runtime (game + worlds) and your workspaces."
      say "delete ~/.local/share/omo entirely to remove everything."
      exit 0 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) SRC="$arg" ;;
  esac
done

case "$OS-$(uname -m)" in
  Linux-x86_64)
    command -v python3 >/dev/null || die "python3 is required (sudo apt install python3)."
    command -v systemctl >/dev/null || die "systemd (user services) is required." ;;
  Darwin-arm64|Darwin-x86_64)
    # The bridge needs Python 3.11+; macOS's own python3 is 3.9.
    for p in /opt/homebrew/bin /usr/local/bin; do [ -x "$p/python3" ] && PATH="$p:$PATH"; done
    command -v tmux >/dev/null || [ -x /opt/homebrew/bin/tmux ] || [ -x /usr/local/bin/tmux ] \
      || die "tmux is required: brew install tmux (https://brew.sh)" ;;
  Linux-*) die "x86_64 only on Linux for now (got $(uname -m))." ;;
  *) die "this installer is for Linux and macOS; on Windows run scripts/install.ps1 in PowerShell." ;;
esac
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
  || die "python3 3.11+ is required ($( [ "$OS" = Darwin ] && echo 'brew install python' || echo 'sudo apt install python3'))."

HERE="$(cd "$(dirname "$0")" && pwd)"
TMP=""
if [ -n "$SRC" ]; then
  [ -f "$SRC" ] || die "no such file: $SRC"
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  tar -xzf "$SRC" -C "$TMP"
  HERE="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)"
fi
[ -f "$HERE/launcher/omo_setup.py" ] || die "run this from an unpacked release, or pass the release .tar.gz"

say "installing Omo Space $(cat "$HERE/VERSION" 2>/dev/null || echo dev) into $APP"
mkdir -p "$APP.new"
cp -a "$HERE/." "$APP.new/"
rm -rf "$APP.old"; [ -d "$APP" ] && mv "$APP" "$APP.old"
mv "$APP.new" "$APP"; rm -rf "$APP.old"

mkdir -p "$BIN"
if [ "$OS" = Darwin ]; then
  # The launcher's shebang is /usr/bin/env python3, which may be Apple's 3.9: pin the one checked above.
  printf '#!/bin/sh\nexec "%s" "%s" "$@"\n' "$(command -v python3)" "$APP/launcher/omo-launcher.py" > "$BIN/omo-research"
  chmod +x "$BIN/omo-research"
else
mkdir -p "$(dirname "$DESKTOP")"
ln -sf "$APP/launcher/omo-launcher.py" "$BIN/omo-research"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Omo Space
Comment=Hermes Computers in Minecraft
Exec=$APP/launcher/omo-launcher.py
Icon=$APP/launcher/assets/omo-icon.svg
Terminal=false
Categories=Game;Development;
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$(dirname "$DESKTOP")" 2>/dev/null || true
fi

if ! command -v hermes >/dev/null; then
  say "Hermes Agent not found: Terminals will work; Hermes Computers open a shell until you install it."
  say "  https://hermes-agent.nousresearch.com/docs"
fi

if [ "$SETUP" = 1 ]; then
  say "setup: downloading the pinned game runtime (~650 MB on first run)"
  python3 "$APP/launcher/omo_setup.py" setup
fi

if [ "$OS" = Darwin ]; then
  say "done. Run: omo-research   (add ~/.local/bin to your PATH if it is not there)"
else
  say "done. Open \"Omo Space\" from your app menu, or run: omo-research"
fi
