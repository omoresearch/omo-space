# Security

Omo Space puts **real shells on your computer** behind blocks in a
Minecraft world. Treat it with the same care as a terminal.

## The model

- The bridge listens on **127.0.0.1 only** and refuses foreign `Host` headers.
- The mod's controls authenticate with a per-install token at
  `~/.local/share/omo/token` (mode 0600). It is never in a world save, an item
  or a log.
- The Lua screen inside a computer only ever holds a **single-use ticket**,
  bound to its station, its workspace and the running bridge process.
- Worlds store workspace ids, station ids and monitor URLs, never a credential.
  `/omo export-check` shows what a map would carry before you share it.
- Hermes' own approval prompts and permissions are untouched. Nothing here
  enables paid inference by itself.

## Limits

- Singleplayer and trusted local worlds. Stations and monitors answer only to
  the world owner (matched by account UUID), or to operators listed in
  `ops.json`. "Allow cheats" for everyone in a LAN world does not count. When
  the world runs without Microsoft authentication (the LAN mode agent bodies
  use), an operator entry only counts from this computer, because anyone can
  log in under any name. Do **not** expose a world with Omo stations to
  players you do not trust with your shell.
- Agent LAN (local agent bodies joining your world) is off unless the game
  starts with `-Domo.agents.lan=true`; the Omo Space launcher sets it for its
  own installs. It listens on 127.0.0.1 only and accepts offline logins from
  this computer only. `-Domo.agents.allowRemote=true` lets anyone who can
  reach that port join, with the world's cheats. Use it only on a network you
  control (for example a Tailscale share limited to that port), never with a
  router port forward.
- Station computers never boot from a floppy in an adjacent disk drive.
- On a dedicated server the mod does not talk to a bridge at all unless the
  server starts with `-Domo.bridge.dedicated=true`.
- A block, a room or a pocket dimension is a presentation boundary, not a
  sandbox. Processes run as your user.
- Downloaded maps are untrusted. A map cannot carry your token, and since
  1.3.0 it cannot make a station run its own code or connect by itself:
  station computers ignore their own startup files and settings, refuse to
  connect while a data pack changes the CraftOS ROM, and only get a ticket
  after you use them in person (remembered per station and workspace in
  `config/omolife-trusted-stations.txt`, outside the world). Map presets
  never create workspaces until you open the station. Once you do use a
  map's station, it is a real shell like any other.
- CC: Tweaked's `/computercraft view|tp|shutdown|turn-on` and its computer
  screen follow the same owner/operator rule on stations. Monitor walls only
  load `http(s)` pages, keep Chromium's same-origin policy, and on servers
  you don't host only load after you click them.
- Monitor walls are visible to everyone near them: what a shell shows on a
  wall can be read by any player who can see it.

## Reporting

Please report vulnerabilities privately to **harry@omo.space** (subject: "Omo security") rather than
in a public issue. Include the version, what an attacker needs, and a
reproduction. We aim to answer within a few days.
