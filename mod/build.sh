#!/usr/bin/env bash
# Build the Omo Space Fabric mod without a Gradle bootstrap.
#
# The Minecraft jar Prism keeps is already in readable Mojang mappings and the
# mod only touches CC:T, MCEF and vanilla client classes, so javac against the
# launcher's own libraries is enough. No remapping step is required.
#
#   mod/build.sh              release jar (no test driver)
#   OMO_TEST=1 mod/build.sh   jar with the in-game test driver (OmoTest instance only)
#
# Compiles against the pinned mods of the OmoTest instance, falling back to
# OmoResearch (override with OMO_MODS_DIR=/path/to/mods).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

JAVA_HOME="${JAVA_HOME:-$HOME/.local/share/Java/temurin-25}"
JAVAC="$JAVA_HOME/bin/javac"
JAR="$JAVA_HOME/bin/jar"
PRISM="$HOME/.local/share/PrismLauncher"
LIBS="$PRISM/libraries"
TEST="${OMO_TEST:-0}"

die() { echo "ERROR: $*" >&2; exit 1; }

[ -x "$JAVAC" ] || die "no javac at $JAVAC (set JAVA_HOME to a JDK 21+)"

# -- one libs location, pinned mods only ---------------------------------------
MODS="${OMO_MODS_DIR:-}"
if [ -z "$MODS" ]; then
  for inst in OmoTest OmoResearch; do
    cand="$PRISM/instances/$inst/.minecraft/mods"
    if ls "$cand"/cc-tweaked-26.2-fabric-*.jar >/dev/null 2>&1; then MODS="$cand"; break; fi
  done
fi
[ -n "$MODS" ] || die "no instance with the pinned mods (OmoTest or OmoResearch). Run launcher/omo_setup.py setup first."
one() { # one <glob-description> <files...>: exactly the first existing match, or fail
  local what="$1"; shift
  for f in "$@"; do [ -f "$f" ] && { echo "$f"; return; }; done
  die "missing $what in $MODS"
}
CCT="$(one 'CC: Tweaked 1.120.x' "$MODS"/cc-tweaked-26.2-fabric-1.120.*.jar)"
MCEF="$(one 'MCEF Modern' "$MODS"/mcef-modern-*.jar)"
FAPI="$(one 'Fabric API' "$MODS"/fabric-api-*.jar)"
MC="$LIBS/com/mojang/minecraft/26.2/minecraft-26.2-client.jar"
[ -f "$MC" ] || die "missing $MC (launch the instance once so Prism downloads it)"
echo "compiling against $MODS"

# Nested jars (CC:T core, MCEF's jcef-api, Fabric API modules) are unpacked
# once into build/ so javac can see them.
CP="$CCT:$MCEF:$MC"
unpack() { # unpack <jar> <dir>
  rm -rf "$2"; mkdir -p "$2"
  "$JAR" --extract --file "$1" --dir "$2" 'META-INF/jars/' 2>/dev/null || true
  for nested in "$2"/META-INF/jars/*.jar; do [ -f "$nested" ] && CP="$CP:$nested"; done
}
unpack "$MCEF" build/nested
unpack "$FAPI" build/fapi
unpack "$CCT" build/ccnested

CP="$CP:$(ls "$LIBS"/net/fabricmc/sponge-mixin/*/sponge-mixin-*.jar | head -1)"
CP="$CP:$(ls "$LIBS"/net/fabricmc/fabric-loader/*/fabric-loader-*.jar | head -1)"
# vanilla client classes reference these at compile time
for extra in \
    "$LIBS"/com/mojang/blaze3d/*/*.jar \
    "$LIBS"/com/mojang/datafixerupper/*/*.jar \
    "$LIBS"/com/mojang/brigadier/*/*.jar \
    "$LIBS"/com/mojang/authlib/*/*.jar \
    "$LIBS"/com/mojang/logging/*/*.jar \
    "$LIBS"/com/google/guava/guava/*/*.jar \
    "$LIBS"/org/joml/joml/*/*.jar \
    "$LIBS"/it/unimi/dsi/fastutil/*/*.jar \
    "$LIBS"/org/slf4j/slf4j-api/*/*.jar \
    "$LIBS"/org/jspecify/jspecify/*/*.jar \
    "$LIBS"/com/google/code/gson/gson/*/*.jar \
    "$LIBS"/io/netty/netty-buffer/*/*.jar \
    "$LIBS"/io/netty/netty-common/*/*.jar \
    "$LIBS"/org/lwjgl/lwjgl/*/lwjgl-3*.jar; do
    [ -f "$extra" ] && CP="$CP:$extra"
done

# GUARD: Mixin owns the mixin package and refuses to load ordinary classes
# from it ("is in a defined mixin package ... and cannot be referenced
# directly"). That error surfaces in game as a disconnect with "Network
# Protocol Error" when the screen opens, so catch it at build time.
for f in src/main/java/space/omo/life/mixin/*.java; do
    if ! grep -qE '@Mixin|interface ' "$f"; then
        die "$(basename "$f") is a plain class inside the mixin package. Move it to space/omo/life/client/."
    fi
done

# Test-only sources: the in-game driver. Release builds leave it out.
TEST_ONLY='/client/TestDriver\.java$'

rm -rf build/classes
mkdir -p build/classes
SOURCES="$(mktemp)"
trap 'rm -f "$SOURCES"' EXIT
if [ "$TEST" = "1" ]; then
  find src/main/java -name '*.java' > "$SOURCES"
else
  find src/main/java -name '*.java' | grep -Ev "$TEST_ONLY" > "$SOURCES"
fi

echo "checking Lua with CC:T's compiler..."
find src/main/resources -name '*.lua' -print0 | xargs -0 "$JAVA_HOME/bin/java" \
  -cp "$(ls build/ccnested/META-INF/jars/cobalt-*.jar | head -1)" tools/LuaCheck.java \
  || die "Lua does not compile"
echo "compiling$([ "$TEST" = "1" ] && echo ' (with test driver)')..."
"$JAVAC" -Xlint:all,-processing,-serial,-classfile,-options -proc:none \
    --release 21 \
    -cp "$CP" \
    -d build/classes \
    @"$SOURCES"

cp -r src/main/resources/. build/classes/

OUT="build/omolife-1.0.0+mc26.2.jar"
rm -f "$OUT"
"$JAR" --create --file "$OUT" -C build/classes .

echo "built $OUT$([ "$TEST" = "1" ] && echo ' (TEST BUILD: includes the driver)')"
