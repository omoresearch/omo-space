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
  the world owner, or to operators listed in `ops.json`. When the world runs
  without Microsoft authentication (the LAN mode agent bodies use), an
  operator entry only counts from this computer, because anyone can log in
  under any name. Do **not** expose a world with Omo stations to players you
  do not trust with your shell.
- Agent bodies join through LAN on port 25566 without authentication, so Omo
  only lets players from this computer in. `-Domo.agents.allowRemote=true`
  lets anyone who can reach that port join, with the world's cheats. Use it
  only on a network you control (for example a Tailscale share limited to that
  port), never with a router port forward.
- Station computers never boot from a floppy in an adjacent disk drive.
- A block, a room or a pocket dimension is a presentation boundary, not a
  sandbox. Processes run as your user.
- Downloaded maps are untrusted: a map cannot carry your token, but it can
  contain stations that open shells on your machine once you use them.

## Reporting

Please report vulnerabilities privately to **harry@omo.space** (subject: "Omo security") rather than
in a public issue. Include the version, what an attacker needs, and a
reproduction. We aim to answer within a few days.
