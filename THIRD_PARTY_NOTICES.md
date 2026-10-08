# Third-party notices

Omo Space's own code is MIT (see `LICENSE`). That licence covers the code in
this repository (the `omolife` mod, the bridge, the launcher, tools and tests).
It does not cover third-party software that the launcher downloads or that is
vendored under `bridge/vendor`. It stands on the work below.

## Downloaded by the launcher (not in this repo or the release bundle)

The launcher fetches each of these from its official source at the version and
hash pinned in `launcher/manifest.json`, and verifies every file.

| Component | Version | Licence | Source |
| --- | --- | --- | --- |
| Minecraft: Java Edition | 26.2 | Mojang EULA — you sign in with your own account | https://www.minecraft.net |
| Fabric Loader | 0.19.5 | Apache-2.0 | https://github.com/FabricMC/fabric-loader |
| Fabric API | 0.160.0+26.2 | Apache-2.0 | https://github.com/FabricMC/fabric |
| CC: Tweaked | 1.120.2 | ComputerCraft Public License | https://github.com/cc-tweaked/CC-Tweaked |
| MCEF Modern | 0.3.3 | LGPL-2.1-only | https://github.com/DimasKama/mcef-modern |
| Chromium Embedded Framework (via MCEF, on first use) | 146 | BSD-3-Clause | https://bitbucket.org/chromiumembedded/cef |
| Eclipse Temurin JRE | 25 | GPL-2.0 with Classpath Exception | https://adoptium.net |
| Prism Launcher | 11.1.0 | GPL-3.0-only | https://github.com/PrismLauncher/PrismLauncher |
| tmux (static build by mjakob-gh) | 3.7b | ISC | https://github.com/tmux/tmux |

## Vendored in `bridge/vendor`

| Package | Version | Licence |
| --- | --- | --- |
| pyte | 0.8.2 | LGPL-3.0 (see `bridge/vendor/pyte-0.8.2.dist-info/LICENSE`) |
| wcwidth | 0.8.3 | MIT |
| websockets | 17.1 | BSD-3-Clause |

Each package's own licence file is kept beside it.

## Fonts (launcher)

IBM Plex Mono and Crimson Text, both SIL Open Font License 1.1
(`launcher/assets/fonts/OFL-*.txt`).

## Trademarks

Minecraft is a trademark of Mojang Synergies AB / Microsoft. Omo Space is
not affiliated with or endorsed by Mojang or Microsoft. Hermes Agent is by Nous
Research.
