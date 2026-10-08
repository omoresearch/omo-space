# Contributing

## Layout

| Path | What |
| --- | --- |
| `mod/` | The Fabric mod (`omolife`): blocks, screens, mixins, Lua station client. `mod/build.sh` builds it. |
| `bridge/` | `omobridge.py`: workspaces, tabs and views over tmux, the `/board` page. Pure stdlib + `bridge/vendor`. |
| `launcher/` | `omo_setup.py` (setup / play / repair engine) and `omo-launcher.py` (the GTK app). `manifest.json` pins every download. |
| `config/`, `scripts/` | tmux config, CC:T server rule, systemd user units, packaging and the installer. |
| `worlds/` | The Omo Workshop starter world and the scripts that build demo worlds. |
| `tests/` | Bridge and launcher tests (no game) and the in-game acceptance suite. |
| `docs/` | Guide, builder docs, release checklist, README media. |

## Build and test

```bash
mod/build.sh                     # release jar -> mod/build/  (needs a set-up OmoTest or OmoResearch instance)
OMO_TEST=1 mod/build.sh          # jar with the in-game test driver
python3 tests/test_launcher.py   # offline, fake HOME
python3 tests/test_bridge.py     # disposable tmux server, stub hermes
tests/launch-test.sh & python3 tests/acceptance.py   # full in-game run on Xvfb
scripts/package.sh               # dist/ release bundle + install.sh + SHA256SUMS
```

No test may touch the live bridge (port 8780), its token or the `omo` tmux
socket; the test stack lives on port 8794 and socket `omo-test`.

## Rules

- A clean compile is not proof: mixins resolve at class load. Check the game
  log for mixin failures, and run the relevant acceptance section.
- Keep helpers out of the `mixin` package (the build fails if you do not).
- Never put a token, host path or credential in a world, an item or a log.
- Commit messages: imperative subject; the body says why.
