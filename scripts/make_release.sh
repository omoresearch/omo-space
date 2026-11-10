#!/bin/sh
# Build the testable release tarball for World Sense.
#
#   sh scripts/make_release.sh [version]
#
# Output: dist/omo-world-sense-<version>.tar.gz + dist/SHA256SUMS.
# The tarball holds everything a tester needs for the read path without a
# game, plus the mod sources and docs for the in-game path:
#   bridge/world_sense.py, bridge/brain.py, bridge/cost_ledger.py,
#   bridge/omobridge.py (patched), tools/, tests/test_world_sense.py,
#   examples/demo_snapshot.json, scripts/demo.sh, docs/WORLD-SENSE*.md,
#   QUICKSTART.md, MANIFEST.txt.
# It deliberately does NOT include a built mod jar: the Fabric jar needs
# Fabric Loom + game jars, which CI builds (see .github/workflows).
# Testers run the demo with: tar xzf <tarball> && cd <dir> && sh scripts/demo.sh
set -e
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
VERSION=${1:-0.1.0}
NAME="omo-world-sense-$VERSION"
DIST="$ROOT/dist"
STAGE="$DIST/$NAME"
rm -rf "$STAGE" "$DIST/$NAME.tar.gz"
mkdir -p "$STAGE"
cd "$ROOT"
# Only files tracked on this branch, so a dirty tree cannot leak in.
# The bridge runtime files (hermesfeed, termcore, vendor/, board.html,
# mcshell.bashrc) are upstream files the patched bridge needs at runtime —
# without them the tarball's demo cannot start (caught by testing the
# tarball itself, 2026-11-11).
for f in bridge/world_sense.py bridge/brain.py bridge/cost_ledger.py bridge/omobridge.py \
  bridge/hermesfeed.py bridge/termcore.py bridge/board.html bridge/mcshell.bashrc \
  tools/world_agent.py tools/push_snapshot.py tests/test_world_sense.py \
  examples/demo_snapshot.json scripts/demo.sh scripts/make_release.sh \
  docs/WORLD-SENSE.md docs/WORLD-SENSE-TRY-IT.md \
  mod/src/main/java/space/omo/life/world/WorldSense.java \
  mod/src/main/java/space/omo/life/world/WorldSenseCommand.java \
  mod/src/main/java/space/omo/life/world/WorldTools.java; do
  if [ ! -f "$f" ]; then echo "missing: $f" >&2; exit 1; fi
  mkdir -p "$STAGE/$(dirname "$f")"
  cp "$f" "$STAGE/$f"
done
cp -r bridge/vendor "$STAGE/bridge/vendor"
cat > "$STAGE/QUICKSTART.md" <<'EOF'
# World Sense — 5-minute test

No game, no key, no model needed for the read path.

```sh
sh scripts/demo.sh
```

That starts a scratch bridge, pushes a canned world snapshot, runs the
reference agent with the deterministic echo brain, and prints the cost
ledger. Expected: a ~450-char world summary, one validated tool call
(`world.describe`), one ledger entry.

With a real brain:

```sh
export OPENROUTER_API_KEY=...   # or install the Muse / Claude CLI
python3 tools/world_agent.py --bridge http://127.0.0.1:8780 \
  --token-file ~/.local/share/omo/token \
  --backend openrouter --model anthropic/claude-haiku-4.5 \
  --task "describe the area and call world.describe"
```

In game (Fabric 26.2, this branch): stand somewhere and run `/omo sense`,
then read it with `curl "http://127.0.0.1:8780/world/describe?token=$(cat
~/.local/share/omo/token)"`. Writes need `-Domo.world.tools=true`.

Full guide: docs/WORLD-SENSE-TRY-IT.md. Design + trust model:
docs/WORLD-SENSE.md.
EOF
(cd "$STAGE" && find . -type f | sort > MANIFEST.txt)
(cd "$DIST" && tar czf "$NAME.tar.gz" "$NAME" && sha256sum "$NAME.tar.gz" > SHA256SUMS)
echo "built: $DIST/$NAME.tar.gz ($(du -h "$DIST/$NAME.tar.gz" | cut -f1))"
cat "$DIST/SHA256SUMS"
wc -l < "$STAGE/MANIFEST.txt" | xargs echo "files in tarball:"
