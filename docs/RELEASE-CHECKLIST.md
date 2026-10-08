# V1 release checklist (status as of 2026-09-26)

The PRD's "smallest release that counts" (section 7), what was verified, how,
and what is still open. Verified means run on this Linux host (Ubuntu, Xvfb +
Mesa software GL, Temurin 25, Minecraft 26.2, Fabric 0.19.5, CC:T 1.120.2,
MCEF Modern 0.3.3, Hermes Agent 0.21.4) against an **isolated** bridge and
session server (`tests/testenv.py`), never the live ones.

| # | Gate | Status | Evidence (`tests/acceptance.py`) |
| --- | --- | --- | --- |
| 1 | Place and use | verified | 1a-1f: Hermes Computer binds its own workspace and a real Hermes reply lands on screen ("42 omo", which is in no prompt); a line typed with its Enter in one burst submits; the Terminal opens a plain shell with no agent |
| 2 | Real screens | verified | 2a-2b: an attached monitor resizes the PTY to its geometry (1x1 → 15x9) and back to 80x29 when broken; multi-block walls render one terminal (5x3 checked by eye) |
| 3 | Continuity | verified | 3a bridge restart, 3b real chunk unload (block entity gone while far), 3c full game quit and relaunch: same shell PID, `OMO_MARK`, cwd and a real `sleep 9001` child job |
| 4 | Tabs and stations | verified | 4a-4d: Ctrl+Shift+T new tab, distinct shells, Ctrl+Tab back with state, Ctrl+W deletes a word in bash and keeps the tab; two stations, two workspaces |
| 5 | Move and recover | verified | 5a-5c: a moved station keeps its workspace, reattaches to the same shell, exactly one view |
| 6 | Honest recovery | verified | 6a-6d: killing the whole session server marks every workspace and tab INTERRUPTED and the job is really gone; Revive shell is explicit and re-runs nothing; Resume Hermes reopens the saved conversation and remembers the earlier turn |
| 7 | Safe installation | verified to the sign-in step (17 Sep) + unit-tested | 7a-7b: the save holds no token, ticket or browser data but does hold the URL binding. `tests/test_launcher.py` (33): resumable downloads incl. full-size `.part` and 416, repair of damaged tmux/Java/mod/CC:T config without touching saves, unit rendering with odd paths, setup lock. `tests/test_bridge.py` (32): wrong token, foreign Host, ticket reuse, ticket for another workspace, pre-restart ticket all rejected |
| 8 | Web monitors | verified | 8a-8f: two walls, two live pages; frame time with 4 pages p95 13-19 ms (software GL); binding survives a real chunk unload and a full game restart; no page accumulation; pages are freed when their wall goes |

Bridge integration (`tests/test_bridge.py`, disposable tmux + stub hermes, no
inference): 32/32, including Enter-as-its-own-keypress for Ink TUIs, screen
resize, exited vs ended vs interrupted, tmux id reuse after a server restart,
per-workspace Hermes profile, no Hermes autorun leaking into shell tabs.

## Fixed in this pass (found by review and testing)

- The two systemd units formed an ordering cycle, so `omo-bridge` was dropped
  at every boot (journal: "Found ordering cycle"). Templates now render from
  `scripts/`, without the cycle.
- Hermes' TUI swallowed Enter typed at game speed; the bridge now delivers
  Enter as its own keypress.
- Any CC computer could mint a ticket and get a host shell; tickets are now
  bound to the calling station, its workspace and the bridge process, and a
  ticket screen can only use its own workspace.
- Panel shortcuts stole Ctrl+W / Ctrl+T from the shell; the rename box closed
  the GUI on "e"; stations and monitors had no owner check.
- Monitor walls drew one terminal per panel; screens never resized (missing
  SIGWINCH to the tmux client); Hermes autorun leaked into sibling tabs;
  combining characters froze screens; reused tmux window ids resurrected the
  wrong tab after a restart; pages leaked on web→terminal switches.

## Not done / needs Harry

- **Clean-machine play-through past the Microsoft sign-in** on a real account.
- **Licences for redistribution**: CC:T is CCPL (downloaded from Modrinth, not
  bundled), MCEF LGPL-2.1, Prism GPL-3, Temurin GPL+CE, a third-party static
  tmux (consider building it ourselves). Mojang brand wording.
- **Performance budget**: measured, not agreed. Chromium is ~300 MB per page;
  page cap is 6.
- **Windows/macOS**: code and offline tests are in (docs/PLATFORMS.md). Each still
  needs one real play-through: launchd agents, WSL port forwarding, Prism and
  MCEF on that OS.
- **Signed update delivery** for the launcher itself.
- **Hermes `--source`**: Omo chats are ordinary `cli` sessions. Tagging them
  `minecraft` needs a Hermes-side change (`_create_titled_session` ignores
  `HERMES_SESSION_SOURCE`, and unknown sources count as messaging surfaces).
- **Legacy `mc-termbridge*` services** still run for old worlds; retire them
  when ready.
- ~~Dead files to delete~~: removed on 30 Sep 2026 (`build/`, `legacy/`, old agent
  launchers, unused GUI sprites).
