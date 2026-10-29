#!/usr/bin/env bash
# Build the release bundles a player downloads:
#
#   dist/omo-space-<version>-linux-x64.tar.gz   launcher, bridge, config, units,
#                                                  the mod jar, the Omo Workshop world
#   dist/omo-space-<version>-macos.tar.gz       the same files (one bundle for arm64 + x86_64)
#   dist/omo-space-<version>-windows.zip        the same files plus install.ps1
#   dist/install.sh, dist/install.ps1              the one-command installers
#   dist/SHA256SUMS
#
#   scripts/package.sh            build the mod (release, no test driver), then package
#   OMO_SKIP_BUILD=1 scripts/package.sh   package the jar already in mod/build
#
# Third-party mods, Minecraft, Java, Prism and tmux are NOT in the bundle: the
# launcher downloads them from their official sources and checks every hash.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION="$(python3 -c 'import json;print(json.load(open("launcher/manifest.json"))["version"])')"
JAR="$(python3 -c 'import json;print(json.load(open("launcher/manifest.json"))["omolife"]["file"])')"
TAG="${OMO_VERSION:-$(git describe --tags --exact-match 2>/dev/null || echo "v$VERSION")}"
BASE="omo-space-${TAG#v}"
DIST="$ROOT/dist"

if [ "${OMO_SKIP_BUILD:-0}" != 1 ]; then
  OMO_TEST=0 mod/build.sh
fi
[ -f "mod/build/$JAR" ] || { echo "package: mod/build/$JAR missing (run mod/build.sh)" >&2; exit 1; }
if unzip -l "mod/build/$JAR" | grep -q 'TestDriver'; then
  echo "package: mod/build/$JAR contains the test driver; rebuild without OMO_TEST" >&2; exit 1
fi

bundle() { # bundle <name> <tar.gz|zip>
  local NAME="$1" KIND="$2" STAGE="$DIST/$1"
  rm -rf "$STAGE" && mkdir -p "$STAGE"
  # Only what the running product needs, from git (never untracked tokens or logs).
  git archive HEAD launcher bridge config scripts/omo-bridge.service scripts/omo-tmux.service \
      "worlds/Omo Workshop" LICENSE THIRD_PARTY_NOTICES.md README.md | tar -x -C "$STAGE"
  cp "mod/build/$JAR" "$STAGE/launcher/$JAR"        # omo_setup.py looks beside itself first
  cp scripts/install.sh "$STAGE/install.sh"
  [ "$KIND" = zip ] && cp scripts/install.ps1 "$STAGE/install.ps1"
  echo "$TAG" > "$STAGE/VERSION"

  # Guard: nothing secret or personal may ship.
  if grep -rIl --exclude-dir=vendor -E '/home/[a-z]+/|/Users/[a-z]+/|API_SERVER_KEY=|BEGIN [A-Z ]*PRIVATE KEY' "$STAGE" \
       | grep -v '/fonts/'; then
    echo "package: the files above contain a host path or a secret; refusing" >&2; exit 1
  fi
  find "$STAGE" \( -name token -o -name '*.token' -o -name '*.log' -o -name __pycache__ \) -prune -exec rm -rf {} +

  if [ "$KIND" = zip ]; then
    rm -f "$DIST/$NAME.zip"; ( cd "$DIST" && zip -qr "$NAME.zip" "$NAME" )
  else
    tar -C "$DIST" -czf "$DIST/$NAME.tar.gz" "$NAME"
  fi
  rm -rf "$STAGE"
  echo "package: $DIST/$NAME.$KIND ($(du -h "$DIST/$NAME.$KIND" | cut -f1))"
}

bundle "$BASE-linux-x64" tar.gz
bundle "$BASE-macos" tar.gz
bundle "$BASE-windows" zip
cp scripts/install.sh scripts/install.ps1 "$DIST/"
( cd "$DIST" && sha256sum "$BASE-linux-x64.tar.gz" "$BASE-macos.tar.gz" "$BASE-windows.zip" \
    install.sh install.ps1 > SHA256SUMS )
cat "$DIST/SHA256SUMS"
