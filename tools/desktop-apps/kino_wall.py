#!/usr/bin/env python3
"""Kino wall: the real Kino desktop app (Electron, running on a hidden Xvfb display) streamed to an Omo web wall.

  GET  /      -> a page that shows the display and sends mouse/keyboard back
  WS   /ws    -> server pushes JPEG frames (ffmpeg x11grab, only while someone watches);
                 client sends JSON input that is injected with XTEST into the same display.

Env: KINO_DISPLAY (:95), KINO_PORT (8864), KINO_FPS (12), KINO_SIZE (1600x960). Binds 127.0.0.1 only.
"""
import asyncio, json, os, sys

import websockets
from websockets.asyncio.server import serve
from websockets.http11 import Response
from websockets.datastructures import Headers
from Xlib import X, XK, display as xdisplay
from Xlib.ext import xtest

DISP = os.environ.get('KINO_DISPLAY', ':95')
PORT = int(os.environ.get('KINO_PORT', '8864'))
FPS = os.environ.get('KINO_FPS', '12')
SIZE = os.environ.get('KINO_SIZE', '1600x960')
W, H = map(int, SIZE.split('x'))

d = xdisplay.Display(DISP)
clients = set()
latest = {'frame': None, 'seq': 0}
new_frame = asyncio.Event()

NAMED = {'Enter': 'Return', 'Backspace': 'BackSpace', 'Tab': 'Tab', 'Escape': 'Escape', 'Delete': 'Delete',
         'ArrowLeft': 'Left', 'ArrowRight': 'Right', 'ArrowUp': 'Up', 'ArrowDown': 'Down', 'Home': 'Home',
         'End': 'End', 'PageUp': 'Prior', 'PageDown': 'Next', 'Insert': 'Insert', ' ': 'space',
         'Control': 'Control_L', 'Shift': 'Shift_L', 'Alt': 'Alt_L', 'Meta': 'Super_L', 'CapsLock': 'Caps_Lock',
         'ContextMenu': 'Menu', **{f'F{i}': f'F{i}' for i in range(1, 13)}}
SPARE = None  # keycode we remap for characters that have no key on the layout


def keycode_for(key):
    global SPARE
    if key in NAMED:
        ks = XK.string_to_keysym(NAMED[key])
    elif len(key) == 1:
        o = ord(key)
        ks = o if o < 0x100 else 0x01000000 + o
    else:
        return None
    kc = d.keysym_to_keycode(ks)
    if kc:
        return kc
    if SPARE is None:  # last keycode with no symbols
        first = d.display.info.min_keycode
        maps = d.get_keyboard_mapping(first, d.display.info.max_keycode - first + 1)
        SPARE = next(first + i for i in range(len(maps) - 1, -1, -1) if not any(maps[i]))
    d.change_keyboard_mapping(SPARE, [(ks,) * 4])
    d.sync()
    return SPARE


def inject(ev):
    t = ev.get('t')
    if t in ('move', 'down', 'up', 'wheel'):
        x = max(0, min(W - 1, int(ev.get('x', 0) * W)))
        y = max(0, min(H - 1, int(ev.get('y', 0) * H)))
        xtest.fake_input(d, X.MotionNotify, x=x, y=y)
        if t in ('down', 'up'):
            b = {0: 1, 1: 2, 2: 3}.get(ev.get('b', 0), 1)
            xtest.fake_input(d, X.ButtonPress if t == 'down' else X.ButtonRelease, b)
        elif t == 'wheel':
            dy, dx = ev.get('dy', 0), ev.get('dx', 0)
            for amt, neg, pos in ((dy, 4, 5), (dx, 6, 7)):
                for _ in range(min(10, max(1, round(abs(amt) / 60))) if amt else 0):
                    btn = pos if amt > 0 else neg
                    xtest.fake_input(d, X.ButtonPress, btn)
                    xtest.fake_input(d, X.ButtonRelease, btn)
    elif t in ('kd', 'ku'):
        kc = keycode_for(ev.get('key', ''))
        if kc:
            xtest.fake_input(d, X.KeyPress if t == 'kd' else X.KeyRelease, kc)
    elif t == 'release_all':  # page lost focus: let go of modifiers so nothing sticks
        for k in ('Control', 'Shift', 'Alt', 'Meta'):
            xtest.fake_input(d, X.KeyRelease, keycode_for(k))
        for b in (1, 2, 3):
            xtest.fake_input(d, X.ButtonRelease, b)
    d.sync()


async def grab_loop():
    """Run ffmpeg only while someone is watching; split its MJPEG stream into frames."""
    while True:
        while not clients:
            await asyncio.sleep(0.3)
        proc = await asyncio.create_subprocess_exec(
            'nice', '-n', '19', 'ffmpeg', '-loglevel', 'error', '-f', 'x11grab', '-draw_mouse', '1',
            '-framerate', FPS, '-video_size', SIZE, '-i', DISP, '-f', 'image2pipe', '-c:v', 'mjpeg',
            '-q:v', '5', 'pipe:1', stdout=asyncio.subprocess.PIPE)
        buf = b''
        try:
            while clients:
                chunk = await proc.stdout.read(1 << 16)
                if not chunk:
                    break
                buf += chunk
                while True:
                    s = buf.find(b'\xff\xd8')
                    e = buf.find(b'\xff\xd9', s + 2) if s >= 0 else -1
                    if e < 0:
                        break
                    latest['frame'], latest['seq'] = buf[s:e + 2], latest['seq'] + 1
                    buf = buf[e + 2:]
                    new_frame.set()
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()


async def sender(ws):
    seen = 0
    while True:
        await new_frame.wait()
        new_frame.clear()
        if latest['seq'] != seen:
            seen = latest['seq']
            await ws.send(latest['frame'])
        await asyncio.sleep(0)


async def handler(ws):
    clients.add(ws)
    task = asyncio.create_task(sender(ws))
    try:
        async for msg in ws:
            if isinstance(msg, str):
                try:
                    for ev in json.loads(msg):
                        inject(ev)
                except Exception as ex:
                    print('input error', ex, file=sys.stderr)
    except websockets.ConnectionClosed:
        pass
    finally:
        clients.discard(ws)
        task.cancel()
        inject({'t': 'release_all'})


PAGE = (os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html'))


def http(conn, req):
    if req.path.startswith('/ws'):
        return None
    if req.path in ('/', '/index.html'):
        body = open(PAGE, 'rb').read()
        return Response(200, 'OK', Headers({'Content-Type': 'text/html; charset=utf-8',
                                            'Cache-Control': 'no-store', 'Content-Length': str(len(body))}), body)
    if req.path == '/health':
        body = json.dumps({'ok': True, 'display': DISP, 'clients': len(clients), 'frames': latest['seq']}).encode()
        return Response(200, 'OK', Headers({'Content-Type': 'application/json', 'Content-Length': str(len(body))}), body)
    return Response(404, 'Not Found', Headers({'Content-Length': '0'}), b'')


async def main():
    asyncio.create_task(grab_loop())
    async with serve(handler, '127.0.0.1', PORT, process_request=http, max_size=1 << 20):
        print(f'kino wall on http://127.0.0.1:{PORT}/ (display {DISP} {SIZE})', flush=True)
        await asyncio.Future()


asyncio.run(main())
