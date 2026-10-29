#!/usr/bin/env bash
# Omo Space installer (Linux x86_64 and macOS). Windows: scripts/install.ps1.
#
# From an unpacked release:      ./install.sh
# From a downloaded tarball:     ./install.sh omo-space-<ver>-linux-x64.tar.gz
# With the GitHub CLI:           gh release download -R omoresearch/omo-space -p '*-linux-x64.tar.gz' -p install.sh
#                                bash install.sh omo-*-linux-x64.tar.gz
#
# Installs the app into ~/.local/share/omo/app (macOS: ~/Library/Application
# Support/OmoResearch/app), adds "Omo Space" to your application menu and an
# `omo-space` command (`omo-research` still works), then runs the launcher's setup
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
# The same file name omo_setup.py's write_desktop_entry uses (APP_ID), so the
# menu shows one "Omo Space" entry, not two. omo-research.desktop is the old one.
DESKTOP="$HOME/.local/share/applications/space.omo.research.launcher.desktop"
OLD_DESKTOP="$HOME/.local/share/applications/omo-research.desktop"
BIN="$HOME/.local/bin"
# `omo-space` is the command; `omo-research` stays as an alias for old installs.
# The OmoResearch folders, instance and service labels keep their names so
# existing worlds, tokens and services carry over.
CMDS=("$BIN/omo-space" "$BIN/omo-research")

# Output in the launcher's palette (gold, cream, muted). Plain text when the
# stream is not a terminal or NO_COLOR is set, so logs and pipes stay clean.
paint() { [ -t "$1" ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != dumb ]; }
if paint 1; then
  GOLD=$'\033[38;5;179m'; CREAM=$'\033[1;38;5;230m'; DIM=$'\033[38;5;245m'
  GREEN=$'\033[38;5;114m'; WARN=$'\033[38;5;215m'; OFF=$'\033[0m'
else
  GOLD=""; CREAM=""; DIM=""; GREEN=""; WARN=""; OFF=""
fi
if paint 2; then RED=$'\033[1;38;5;203m'; EOFF=$'\033[0m'; else RED=""; EOFF=""; fi
tilde() { local s="$*"; printf '%s' "${s//$HOME/\~}"; }
say()  { printf '  %s▸%s %s\n' "$GOLD" "$OFF" "$*"; }             # a step starts
ok()   { printf '  %s✓%s %s\n' "$GREEN" "$OFF" "$*"; }            # a step is done
warn() { printf '  %s!%s %s\n' "$WARN" "$OFF" "$*"; }             # worth knowing, not fatal
note() { printf '    %s%s%s\n' "$DIM" "$*" "$OFF"; }               # a detail under a line
die()  { printf '\n  %s✗%s %s\n\n' "$RED" "$EOFF" "$*" >&2; exit 1; }
banner() {  # banner <subtitle>
  printf '\n  %s◆%s %sOmo Space%s\n' "$GOLD" "$OFF" "$CREAM" "$OFF"
  printf '    %s%s%s\n\n' "$DIM" "$1" "$OFF"
}

SETUP=1; SRC=""
for arg in "$@"; do
  case "$arg" in
    --no-setup) SETUP=0 ;;
    --uninstall)
      banner "Uninstalling. Your worlds and workspaces stay."
      if [ "$OS" = Darwin ]; then
        for label in space.omo.research.bridge space.omo.research.tmux; do
          plist="$HOME/Library/LaunchAgents/$label.plist"
          if [ -f "$plist" ]; then
            say "Stopping $label"
            launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
            rm -f "$plist"
            ok "Stopped $label"
          fi
        done
        rm -rf "$APP" "${CMDS[@]}"
        ok "Removed the app and the omo-space command"
        note "Kept ~/Library/Application Support/OmoResearch (game, worlds, workspaces)."
        echo
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
        say "Stopping the Omo services"
        systemctl --user disable --now "${OURS[@]}" 2>/dev/null || true
        ok "Stopped ${OURS[*]}"
      else
        ok "No services running from this install; nothing to stop"
      fi
      rm -f "$UNITS_DIR/omo-bridge.service" "$UNITS_DIR/omo-tmux.service"
      [ "${#OURS[@]}" -gt 0 ] && systemctl --user daemon-reload 2>/dev/null || true
      rm -rf "$APP" "$DESKTOP" "$OLD_DESKTOP" "${CMDS[@]}"
      ok "Removed the app, menu entry and omo-space command"
      note "Kept ~/.local/share/omo/runtime (game + worlds) and your workspaces."
      note "To remove everything, delete ~/.local/share/omo."
      echo
      exit 0 ;;
    # The whole header comment, so a new option there is never cut off.
    -h|--help) awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"; exit 0 ;;
    *) SRC="$arg" ;;
  esac
done

case "$OS-$(uname -m)" in
  Linux-x86_64)
    command -v systemctl >/dev/null || die "systemd (user services) is required." ;;
  Darwin-arm64|Darwin-x86_64)
    # The bridge needs Python 3.11+; macOS's own python3 is 3.9.
    for p in /opt/homebrew/bin /usr/local/bin; do [ -x "$p/python3" ] && PATH="$p:$PATH"; done
    command -v tmux >/dev/null || [ -x /opt/homebrew/bin/tmux ] || [ -x /usr/local/bin/tmux ] \
      || die "tmux is required: brew install tmux (https://brew.sh)" ;;
  Linux-*) die "x86_64 only on Linux for now (got $(uname -m))." ;;
  *) die "this installer is for Linux and macOS; on Windows run scripts/install.ps1 in PowerShell." ;;
esac
# The newest Python 3.11+ on PATH. `python3` can be older (Ubuntu 22.04: 3.10,
# macOS: 3.9) while a versioned one next to it is new enough.
PY=""
for cand in python3 python3.14 python3.13 python3.12 python3.11; do
  exe="$(command -v "$cand" 2>/dev/null || true)"
  [ -n "$exe" ] && "$exe" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null && { PY="$exe"; break; }
done
if [ -z "$PY" ]; then
  have="$(python3 -c 'import platform; print(platform.python_version())' 2>/dev/null || echo none)"
  if [ "$OS" = Darwin ]; then
    die "Python 3.11+ is required (found: $have). Install it with:  brew install python"
  elif grep -qs 'VERSION_ID="22.04"' /etc/os-release; then
    die "Python 3.11+ is required (Ubuntu 22.04 has $have). Install it next to the system one, then run this again:
      sudo apt install python3.11"
  else
    die "Python 3.11+ is required (found: $have). Install a newer python3 from your distribution, then run this again."
  fi
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
TMP=""
if [ -n "$SRC" ]; then
  [ -f "$SRC" ] || die "no such file: $SRC"
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  tar -xzf "$SRC" -C "$TMP"
  HERE="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)"
fi
[ -f "$HERE/launcher/omo_setup.py" ] || die "run this from an unpacked release, or pass the release .tar.gz"

VERSION="$(cat "$HERE/VERSION" 2>/dev/null || echo dev)"
banner "Your AI workspace, built in Minecraft · $VERSION"
ok "$([ "$OS" = Darwin ] && echo macOS || echo Linux) $(uname -m) · $("$PY" -c 'import platform; print("Python", platform.python_version())')"

say "Installing the app into $(tilde "$APP")"
mkdir -p "$APP.new"
cp -a "$HERE/." "$APP.new/"
rm -rf "$APP.old"; [ -d "$APP" ] && mv "$APP" "$APP.old"
mv "$APP.new" "$APP"; rm -rf "$APP.old"
ok "App installed"

mkdir -p "$BIN"
# The launcher's shebang is /usr/bin/env python3, which can be older than 3.11
# (macOS 3.9, Ubuntu 22.04 3.10): the command and the menu entry pin $PY.
rm -f "$BIN/omo-space"                          # an older install left a symlink here
printf '#!/bin/sh\nexec "%s" "%s" "$@"\n' "$PY" "$APP/launcher/omo-launcher.py" > "$BIN/omo-space"
chmod +x "$BIN/omo-space"
ln -sf omo-space "$BIN/omo-research"
if [ "$OS" != Darwin ]; then
mkdir -p "$(dirname "$DESKTOP")"
rm -f "$OLD_DESKTOP"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Omo Space
Comment=Hermes and your terminals, inside Minecraft
Exec="$PY" "$APP/launcher/omo-launcher.py"
Icon=$APP/launcher/assets/omo-icon.svg
Terminal=false
Categories=Game;Development;
StartupWMClass=space.omo.research.launcher
EOF
command -v update-desktop-database >/dev/null && update-desktop-database "$(dirname "$DESKTOP")" 2>/dev/null || true
fi
if [ "$OS" = Darwin ]; then ok "Added the omo-space command"; else ok "Added the menu entry and the omo-space command"; fi
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) warn "$(tilde "$BIN") is not on your PATH, so the omo-space command won't be found yet"
     note "add it in your shell profile:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

# The window is GTK 4 + libadwaita through PyGObject. Without them Omo Space
# still works, from a menu in a terminal; say so now, with the packages.
if [ "$OS" != Darwin ] && ! "$PY" -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")' 2>/dev/null; then
  warn "The Omo Space window needs GTK 4 and libadwaita for Python. Until then it opens as a menu in a terminal."
  if [ "$PY" != /usr/bin/python3 ]; then
    note "Your distribution's packages serve /usr/bin/python3, not $PY; the terminal menu does everything the window does."
  elif command -v apt-get >/dev/null; then note "sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
  elif command -v dnf >/dev/null; then note "sudo dnf install python3-gobject gtk4 libadwaita"
  elif command -v pacman >/dev/null; then note "sudo pacman -S python-gobject gtk4 libadwaita"
  fi
fi

if ! command -v hermes >/dev/null; then
  warn "Hermes Agent not found. Terminals work; Hermes Computers open a shell until you install it."
  note "https://hermes-agent.nousresearch.com/docs"
fi

if [ "$SETUP" = 1 ]; then
  say "Setup: downloading the pinned game runtime (~650 MB on first run)"
  note "Minecraft, Fabric, CC: Tweaked, MCEF and Java, each from its official source, hash-checked."
  note "The in-game browser fetches its engine (about 360 MB) the first time a web wall opens."
  echo
  "$PY" "$APP/launcher/omo_setup.py" setup \
    || die "setup stopped; the last lines above say why. Fix that, then run omo-space and choose Install / Set up."
fi

printf '\n  %s✓ Omo Space is installed.%s\n' "$CREAM" "$OFF"
if [ "$OS" = Darwin ]; then
  note "Start it with:  omo-space"
else
  note "Open \"Omo Space\" from your app menu, or run:  omo-space"
fi
[ "$SETUP" = 1 ] || note "Setup was skipped (--no-setup). The launcher offers it the first time you open it."
echo
