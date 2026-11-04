# Desktop apps on walls

A web wall is a Chromium page (MCEF, windowless) per monitor wall. Many desktop apps are an Electron shell
around a website plus a small preload API (`window.electron`) that gives the site local files, local agents
and other native features. This folder puts such an app on a wall **natively**: the wall loads the app's
website itself (sharp, real input, no video), and a local bridge gives the page the desktop app's preload
API back by relaying it to the real desktop app, which runs hidden on the same machine.

Worked example: [Kino](https://kino.ai), the AI video editor (`/opt/Kino`, Electron 44). Everything here is
written for Kino, but the parts are generic: swap the schema source and the app-specific fixes in
`kino_native.py`.

```
 wall (MCEF) page https://kino.ai                     hidden desktop app (Electron, Xvfb :95)
 ┌───────────────────────────────┐   CDP 9338        ┌────────────────────────────────┐
 │ shim.js (runs before the page)│◄────────────────► │ kino_native.py                 │  CDP 9347
 │  window.electron = relay      │  binding/evaluate │  calls window.electron.* on the│◄──────────►  real
 │  FileSystem*Handle = emulated │                   │  real app window, events back  │   window.electron
 └──────────────┬────────────────┘                   └────────────────────────────────┘
                │ fetch 127.0.0.1:8865 (token)
                ▼
           kino_fs.py: local file API (grants = only the paths the app asks for)
```

## Why the file system is emulated

On the game's Chromium (MCEF / CEF 146), being handed a **directory** handle kills the whole game with a
SIGTRAP and no crash report or hs_err:

- a folder dropped onto a wall page (CDP `Input.dispatchDragEvent`, the way the desktop app gives Kino its
  `KinoProjects` folder): two game deaths, each 1-2 s after the drop;
- `showDirectoryPicker()` answered by a `CefDialogHandler`: reproduced in a standalone MCEF-like OSR browser
  (same JCEF build and switches), exit 133.

File pickers work; permission prompts are ignored (CEF logs *Implement OnShowPermissionPrompt*). So walls
never get real handles: `shim.js` gives the page emulated `FileSystemDirectoryHandle` /
`FileSystemFileHandle` / writable-stream objects (`instanceof` works, they survive IndexedDB via a
pack/revive marker, `FileSystemObserver.observe` is a no-op for them) whose operations are requests to
`kino_fs.py`. `showDirectoryPicker` is blocked on the wall page.

## Files

| File | What it does |
|---|---|
| `kino_native.py` | The relay. Attaches to every wall page over the wall browser's DevTools port, injects `shim.js` before page scripts, relays `window.electron` calls/subscriptions to the hidden app, copies the app's site cookies and IndexedDB media-path table once (so the wall opens logged in), bounces Kino's `/open-desktop` page, resumes the hidden app if something SIGSTOPs it, and appends every prompt sent to the embedded agent to `prompts.jsonl` before relaying it (a crash can't eat a long prompt). |
| `shim.js` | Page side: the `window.electron` relay, emulated file handles, the synthetic drop that answers Kino's `dispatchFileDrop`, `authorizeLocalMedia` -> a file-API URL. |
| `kino_fs.py` | 127.0.0.1:8865 file API: stat / list / child / read (Range) / remove / move / writable streams via `.crswap` temp files. Per-run token, CORS for `https://kino.ai` only, only paths under a grant. |
| `kino_wall.py` + `index.html` | Fallback: video stream of the hidden app's whole X display (ffmpeg x11grab MJPEG over WebSocket, XTEST input) at 127.0.0.1:8864. Works with no mod change, software-rendered. |
| `systemd/*.service` | User units: `kino-wall-xvfb`, `kino-wall-app`, `kino-wall-native`, `kino-wall-stream`. |

## Setup

1. Mod: put a port in `<game dir>/config/omolife-cef-debug-port.txt` (e.g. `9338`) and restart the game.
   Only then does the wall Chromium open DevTools on 127.0.0.1 and allow pages to reach 127.0.0.1
   (`--disable-features=LocalNetworkAccessChecks`), with software WebGL (`--enable-unsafe-swiftshader`, needed
   for Kino's timeline). Delete the file to turn both off. While it's on, local
   programs can drive the wall browser and its logins: keep it to machines you trust.
2. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` here.
3. Copy `systemd/*.service` to `~/.config/systemd/user/`, then
   `systemctl --user enable --now kino-wall-xvfb kino-wall-app kino-wall-native` (and `kino-wall-stream` for
   the fallback). The hidden app reuses `~/.config/Kino`, so sign in to the desktop app once first.
4. Point a wall at `https://kino.ai/` (or `http://127.0.0.1:8864/` for the stream).

## Video codecs

The game's Chromium has no proprietary codecs: it can neither decode nor encode H.264, HEVC or AAC
(`<video>`, MSE and WebCodecs all say no; VP9, AV1 and Opus work). Kino reads MP4s itself (mediabunny) and
builds 720p H.264/AAC editing proxies in OPFS (`proxy-<mediaId>.mp4` / `.m4a`), so on a wall every import
failed with *MP4 video codec not supported on this device: avc1*. `kino_fs.py` therefore:

- serves media files as a cached VP9/Opus copy with the same name, size and duration (ffmpeg, niced, cpus
  10-15, `~/.cache/omo-kino-proxy`; `?raw=1` gets the original bytes);
- makes Kino-style 720p VP9/Opus editing proxies (`?edit=1`), which `shim.js` writes into the wall's OPFS
  before Kino looks for them (Kino's reuse check demuxes the file and compares durations, and accepts them),
  so the wall never has to encode.

## Pitfalls found the hard way

- Child processes of Electron inherit its DevTools listening socket: after the app restarts, an agent it
  spawned can still hold the old port. Use an uncommon port (9347) and check `ss -ltnp` before blaming the app.
- Relay with two CDP sessions: run every event handler as its own task; awaiting one in the socket reader
  deadlocks when it waits on the other session.
- A stand-in browser for tests: headless Google Chrome (not Brave: it lacks the File System Access API), with
  `--disable-features=LocalNetworkAccessChecks` like the wall.
- Kino with the `claude-code` backend keeps chats only on this machine (`~/.config/Kino/agent-sessions.json`
  -> `~/.claude/projects/...`), not on kino.ai.
