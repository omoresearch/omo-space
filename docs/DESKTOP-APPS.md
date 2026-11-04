# Desktop apps on walls

Walls can now run desktop apps, not just web pages. For Electron apps (a website plus a native preload API),
the wall loads the app's website directly and a local bridge gives the page the desktop app's native
features by relaying them to the real app, running hidden on the same machine. Apps with no web front end
can be streamed instead (video of a hidden X display, mouse and keys sent back).

- Opt-in switch: `<game dir>/config/omolife-cef-debug-port.txt` containing a port (e.g. `9338`), then restart
  the game. It adds `--remote-debugging-port=<port>` (127.0.0.1) and
  `--disable-features=LocalNetworkAccessChecks` to the wall Chromium. No file, no switches (the default).
- Bridge, file API, stream fallback, setup and the Kino worked example: [tools/desktop-apps](../tools/desktop-apps/README.md).
- Never hand a wall page a real directory handle (folder drop or folder picker): on the game's Chromium that
  kills the game. The bridge emulates file handles for that reason.
