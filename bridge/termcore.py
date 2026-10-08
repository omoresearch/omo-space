#!/usr/bin/env python3
"""Terminal rendering core shared by the Omo bridge.

Everything that turns a host terminal into something a ComputerCraft screen
can show: the Omo palette for CC's 16 colour slots, ANSI/256/truecolour
mapping onto them, glyph folding into CC's font, the text helpers behind
selection, find and links, and a PTY-backed pyte screen. The PTY only ever
runs `tmux attach`, never the user's shell directly.
"""
import base64
import binascii
import codecs
import fcntl
import os
import pty
import re
import select
import signal
import struct
import subprocess
import termios
import threading
import unicodedata

import pyte

# --------------------------------------------------------------------------- palette
# The station sets CC's 16 palette slots to this scheme (sent in `welcome`), so
# the slots are simply the 16 ANSI colours in Omo's dark theme. Index = ANSI
# colour number; value = (CC blit digit, RGB). Slot choice keeps the stock CC
# meaning close (red on red, green on green) for anything drawn before the
# palette arrives.
ANSI_SLOTS = [
    ("f", 0x1A1511),  # 0  black: espresso, the terminal background
    ("e", 0xE5645A),  # 1  red: Omo interrupted
    ("d", 0x6FD08C),  # 2  green: Omo live
    ("1", 0xDDA744),  # 3  yellow: Omo gold
    ("b", 0x6A9FD8),  # 4  blue
    ("a", 0xC77DBB),  # 5  magenta
    ("9", 0x5DB8B0),  # 6  cyan
    ("8", 0xE8E1D5),  # 7  white: the default text, cream
    ("7", 0x6E6259),  # 8  bright black: dim text, the status bar
    ("6", 0xF28B82),  # 9  bright red
    ("5", 0x9EE6B2),  # 10 bright green
    ("4", 0xF7D774),  # 11 bright yellow: Hermes gold
    ("3", 0x9CC3F0),  # 12 bright blue
    ("2", 0xE2A6D6),  # 13 bright magenta
    ("c", 0x8FDCD4),  # 14 bright cyan
    ("0", 0xFBF7EE),  # 15 bright white: cream
]
PALETTE = {digit: f"{rgb:06x}" for digit, rgb in ANSI_SLOTS}
SLOT = [digit for digit, _ in ANSI_SLOTS]
DEFAULT_FG, DEFAULT_BG = SLOT[7], SLOT[0]
BRIGHT = {SLOT[i]: SLOT[i + 8] for i in range(8)}

# Highlights drawn over the text: (fg, bg).
MARKS = {"sel": (SLOT[0], SLOT[12]), "hit": (SLOT[0], SLOT[3]), "cur": (SLOT[0], SLOT[10])}

_NAMES = ["black", "red", "green", "brown", "blue", "magenta", "cyan", "white"]
NAMED = {**{n: SLOT[i] for i, n in enumerate(_NAMES)}, **{"bright" + n: SLOT[i + 8] for i, n in enumerate(_NAMES)}}
# xterm's own values for the 16 colours when an app picks them from the
# 256-colour table (pyte hands those over as hex).
XTERM16 = {h: SLOT[i] for i, h in enumerate((
    "000000", "cd0000", "00cd00", "cdcd00", "0000ee", "cd00cd", "00cdcd", "e5e5e5",
    "7f7f7f", "ff0000", "00ff00", "ffff00", "5c5cff", "ff00ff", "00ffff", "ffffff"))}
_RGB = [((rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255) for _, rgb in ANSI_SLOTS]
_NEAREST = {}


def nearest(hexstr):
    """Closest palette slot to an RGB colour (redmean distance)."""
    hit = _NEAREST.get(hexstr) or XTERM16.get(hexstr)
    if hit:
        return hit
    try:
        r, g, b = (int(hexstr[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None
    best, best_d = DEFAULT_FG, None
    for i, (cr, cg, cb) in enumerate(_RGB):
        rm = (r + cr) / 2
        d = (2 + rm / 256) * (r - cr) ** 2 + 4 * (g - cg) ** 2 + (2 + (255 - rm) / 256) * (b - cb) ** 2
        if best_d is None or d < best_d:
            best, best_d = SLOT[i], d
    _NEAREST[hexstr] = best
    return best


def _slot(name, default):
    if not name or name == "default":
        return default
    key = name.lower()
    if key in NAMED:
        return NAMED[key]
    if len(key) == 6:
        return nearest(key) or default
    return default


def cell_colours(ch):
    """(fg, bg) blit digits for a pyte cell: bold is bright (for the eight
    basic colours and the default), reverse swaps, underline (which CC cannot
    draw) reads as bold, and text never vanishes into its own background."""
    fg, bg = _slot(ch.fg, DEFAULT_FG), _slot(ch.bg, DEFAULT_BG)
    if (ch.bold or ch.underscore) and (ch.fg == "default" or ch.fg in _NAMES):
        fg = BRIGHT.get(fg, fg)
    if fg == bg and ch.fg != ch.bg:
        fg = SLOT[15] if bg in (SLOT[0], SLOT[8], SLOT[4], SLOT[1]) else SLOT[0]
    if ch.reverse:
        fg, bg = bg, fg
    return fg, bg


# --------------------------------------------------------------------------- glyph folding
# CC's font: CP437 symbols in 1-31, a checkerboard at 127, 2x3 teletext
# blocks in 128-159 (128 + bits: 1 top-left, 2 top-right, 4 mid-left,
# 8 mid-right, 16 bottom-left; the bottom-right pixel is drawn by inverting),
# Latin-1 in 160-255. Anything else folds to the nearest ASCII.
GLYPHS = {}
for _a, _b in {
    "─━═╌╍┄┅┈┉": "-", "│┃║╎╏┆┇┊┋": "|",
    "┌┍┎┏╭╒╓╔┐┑┒┓╮╕╖╗└┕┖┗╰╘╙╚┘┙┚┛╯╛╜╝├┝┞┟┠┡┢┣╞╟╠┤┥┦┧┨┩┪┫╡╢╣┬┭┮┯┰┱┲┳╤╥╦┴┵┶┷┸┹┺┻╧╨╩"
    "┼┽┾┿╀╁╂╃╄╅╆╇╈╉╊╋╪╫╬": "+",
    "•●∙◦▪": "\x07", "◘": "\x08", "☺": "\x01", "☻": "\x02", "♥": "\x03", "♦◆": "\x04",
    "♣": "\x05", "♠": "\x06", "♂": "\x0b", "♀": "\x0c", "♪": "\x0e", "♫": "\x0f",
    "►▶▸⏵❯❱": "\x10", "◄◀◂⏴❮❰": "\x11", "↕": "\x12", "‼": "\x13", "▬■": "\x16", "↨": "\x17",
    "↑⏶": "\x18", "↓⏷": "\x19", "→⟶⇒➜": "\x1a", "←⟵⇐": "\x1b", "∟": "\x1c", "↔": "\x1d",
    "▲▴△⌃": "\x1e", "▼▾▽⌄": "\x1f", "░▒▓": "\x7f",
    "▌▎▍▏": "\x95", "▀▔": "\x8f", "▘": "\x81", "▝": "\x82", "▖": "\x90",
    "○◯◎◉◈": "o", "◇□": "#", "★☆✦✧⚹": "*", "✓✔": "v", "✗✘": "x", "⚠": "!", "…": ".",
    "–—―": "-", "‘’‚‛": "'", "“”„‟": '"', "↵⏎": "<", "≈": "~", "≤": "<", "≥": ">", "≠": "!",
    "™": "T", "€": "E", "☤": "+", "⏲": "t", "⌘": "^", "⇧": "^", "⌥": "^", "⎋": "^",
    "⇥": ">", "⌫": "<", "␣": " ", "›》⟩❭": ">", "‹《⟨❬": "<",
}.items():
    for _ch in _a:
        GLYPHS[_ch] = (_b, False)
# Filled from the other side: draw the complement with the colours swapped.
for _ch, _g in {"█": "\x80", "▐": "\x95", "▄": "\x83", "▂▃▁": "\x8f", "▅▆▇": "\x83",
                "▟": "\x81", "▙": "\x82", "▛": "\x90", "▜": "\x90"}.items():
    for _c in _ch:
        GLYPHS[_c] = (_g, True)
_FOLDED = {}


def fold_char(ch):
    """(glyph, invert) for one cell: a character CC's font can draw, and
    whether to swap the cell's colours to draw it."""
    if not ch:
        return " ", False
    # pyte keeps a base letter and its combining marks in one cell ("स्");
    # the base code point is what can be drawn.
    ch = ch[0]
    o = ord(ch)
    if 32 <= o <= 126:
        return ch, False
    hit = _FOLDED.get(ch)
    if hit is None:
        hit = _FOLDED[ch] = _fold_slow(ch, o)
    return hit


def _fold_slow(ch, o):
    if ch in GLYPHS:
        return GLYPHS[ch]
    if 0xA1 <= o <= 0xFF and o != 0xAD:
        return ch, False
    if o == 0xA0 or o == 0x2800:
        return " ", False
    if 0x2800 <= o <= 0x28FF:
        return "\x7f", False
    if 0x2500 <= o <= 0x257F:
        return "+", False
    if 0x2580 <= o <= 0x259F:
        return "\x7f", False
    if o < 32:
        return " ", False
    try:
        ascii_form = "".join(c for c in unicodedata.normalize("NFKD", ch) if ord(c) < 128)
        if ascii_form.strip():
            return ascii_form[0], False
        cat = unicodedata.category(ch)
    except (TypeError, ValueError):
        return ".", False
    if cat.startswith("Z"):
        return " ", False
    if cat.startswith("P"):
        return "-", False
    if cat.startswith("S"):
        return "*", False
    return ".", False


# --------------------------------------------------------------------------- frames
def encode_rows(screen, cols, rows, marks=None):
    """RLE-encode a pyte screen: [[count, text, fg, bg], ...] per row.
    `marks` maps a row to [(x1, x2, style)] highlights (MARKS)."""
    buf = screen.buffer
    lines = []
    for y in range(rows):
        line = buf[y] if y in buf else {}
        row_marks = (marks or {}).get(y, ())
        row = []
        run_n, run_t, run_f, run_b = 0, [], None, None
        for x in range(cols):
            ch = line.get(x) if hasattr(line, "get") else None
            if ch is None:
                data, flip, fg, bg = " ", False, DEFAULT_FG, DEFAULT_BG
            else:
                data, flip = fold_char(ch.data or " ")
                fg, bg = cell_colours(ch)
                if flip:
                    fg, bg = bg, fg
            for x1, x2, style in row_marks:
                if x1 <= x <= x2:
                    fg, bg = MARKS[style]
                    if flip:
                        fg, bg = bg, fg
            if (fg, bg) == (run_f, run_b):
                run_t.append(data)
                run_n += 1
            else:
                if run_n:
                    row.append([run_n, "".join(run_t), run_f, run_b])
                run_n, run_t, run_f, run_b = 1, [data], fg, bg
        if run_n:
            row.append([run_n, "".join(run_t), run_f, run_b])
        lines.append(row)
    return lines


# --------------------------------------------------------------------------- text on screen
# Screen text is one string per row, one character per cell; the right half
# of a wide character is WIDE so positions stay cell-aligned.
WIDE = "\x00"
WORD = re.compile(r"[\w\x00\-./~:@%+#?=&,]")
URL = re.compile(r"https?://[^\s<>\"'`|\\^{}\x00]+")


def normal_sel(sel):
    """(x1, y1, x2, y2) with the start before the end."""
    x1, y1, x2, y2 = sel
    return (x2, y2, x1, y1) if (y1, x1) > (y2, x2) else (x1, y1, x2, y2)


def expand(lines, sel, unit, cols):
    """Grow a selection to whole words or whole lines, as a double or
    triple click does. Returns (x1, y1, x2, y2), normalised."""
    x1, y1, x2, y2 = normal_sel(sel)
    if unit == "line":
        return 0, y1, cols - 1, y2
    if unit == "word":
        a, b = lines[y1] if y1 < len(lines) else "", lines[y2] if y2 < len(lines) else ""
        if x1 < len(a) and WORD.match(a[x1]):
            while x1 > 0 and WORD.match(a[x1 - 1]):
                x1 -= 1
        if x2 < len(b) and WORD.match(b[x2]):
            while x2 + 1 < len(b) and WORD.match(b[x2 + 1]):
                x2 += 1
    return x1, y1, x2, y2


def sel_marks(sel, cols):
    """{row: [(x1, x2, "sel")]} for a normalised selection."""
    if not sel:
        return {}
    x1, y1, x2, y2 = sel
    return {y: [(x1 if y == y1 else 0, x2 if y == y2 else cols - 1, "sel")] for y in range(y1, y2 + 1)}


def selection_text(lines, sel, cols):
    """The selected text. A row that runs to the last column continues on the
    next (a wrapped line copies as one line, as in any terminal)."""
    if not sel:
        return ""
    x1, y1, x2, y2 = sel
    out = []
    for y in range(y1, min(y2, len(lines) - 1) + 1):
        line = lines[y].ljust(cols)
        part = line[x1 if y == y1 else 0:(x2 if y == y2 else cols - 1) + 1]
        wrapped = y < y2 and line[cols - 1] not in (" ", WIDE)
        out.append(part.replace(WIDE, "") + ("" if wrapped else "\n"))
    text = "".join(out)
    return "\n".join(s.rstrip() for s in text.split("\n")).rstrip("\n")


def matcher(q):
    """Smart case: lower-case queries ignore case, mixed-case ones match it."""
    return re.compile(re.escape(q), 0 if q != q.lower() else re.IGNORECASE)


def find_spans(lines, q):
    """[(y, x1, x2)] for every match of q on screen."""
    rx = matcher(q)
    return [(y, m.start(), m.end() - 1) for y, line in enumerate(lines) for m in rx.finditer(line)]


def url_at(lines, x, y, cols):
    """The http(s) URL under a cell, else None. Rows that run to the last
    column join the next one, so a link wrapped over lines opens whole."""
    if not 0 <= y < len(lines):
        return None
    rows = [line.ljust(cols)[:cols] for line in lines]
    top, bottom = y, y
    while top > 0 and y - top < 8 and rows[top - 1][-1] != " ":
        top -= 1
    while bottom + 1 < len(rows) and bottom - y < 8 and rows[bottom][-1] != " ":
        bottom += 1
    pos = (y - top) * cols + x
    for m in URL.finditer("".join(rows[top:bottom + 1]).replace(WIDE, " ")):
        if m.start() <= pos < m.end():
            return m.group(0).rstrip(".,;:!?)]}'\"")
    return None


# --------------------------------------------------------------------------- OSC 52
# Apps (vim, nvim, Hermes, tmux itself) copy with OSC 52; tmux passes it on
# to its clients (set-clipboard on). The bridge honours writes only: a query
# ("?") is never answered, so nothing on the host clipboard leaks to a pane.
OSC52 = "\x1b]52;"
OSC52_MAX = 1 << 20                       # base64 characters (~768 KiB of text)
_OSC_END = re.compile(r"\x07|\x1b\\")


def osc52_text(payload):
    """The text of an OSC 52 write, or None for a query or garbage."""
    data = payload.split(";", 1)[-1]
    if not data or data == "?":
        return None
    try:
        raw = base64.b64decode(data, validate=False)
    except (binascii.Error, ValueError):
        return None
    return raw.decode("utf-8", "replace").replace("\x00", "")


# --------------------------------------------------------------------------- PTY attach
def set_winsize(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class TolerantScreen(pyte.Screen):
    """pyte.Screen that survives sequences it does not implement.

    tmux and modern TUIs probe the terminal (private DSR `ESC[?6n`, DECRQM,
    kitty keyboard queries). Stock pyte raises on some of them, and an
    exception in the reader thread would silently freeze the screen while the
    process underneath kept running - the exact "stale frame" failure the PRD
    forbids. Answer what we can, ignore the rest.
    """

    bells = 0

    def report_device_status(self, mode=0, **kwargs):
        if kwargs.get("private"):
            return
        try:
            super().report_device_status(mode)
        except Exception:
            pass

    def report_device_attributes(self, mode=0, **kwargs):
        try:
            super().report_device_attributes(mode, **kwargs)
        except Exception:
            pass

    def write_process_input(self, data):
        # Deliberately silent. Replies to DA/DSR probes get echoed by the
        # shell as stray text ("6c"), and the applications used here work
        # without them; tmux answers the pane's own queries itself.
        return

    def bell(self, *args):
        self.bells += 1

    def debug(self, *args, **kwargs):
        pass


class PtyScreen:
    """A PTY running `argv`, rendered to a pyte screen.

    Used for one thing only: attaching to a tmux session. The process on the
    other side is a tmux client, so killing this object never kills the
    user's shell — that lives in the tmux server.
    """

    def __init__(self, cols, rows, argv, env=None, notify=None, on_clipboard=None):
        self.cols, self.rows = cols, rows
        # Called from the reader thread whenever the screen changed (or the
        # client died); the bridge hops it onto its event loop.
        self.notify = notify or (lambda: None)
        self.on_clipboard = on_clipboard or (lambda text: None)
        self.screen = TolerantScreen(cols, rows)
        self.stream = pyte.Stream(self.screen)
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.lock = threading.Lock()
        self.alive = True
        self.mouse_reporting = False
        self._osc = ""                          # an OSC 52 split across reads

        self.master, slave = pty.openpty()
        set_winsize(self.master, rows, cols)
        full_env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor",
                    "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        if env:
            full_env.update(env)
        self.proc = subprocess.Popen(
            argv, stdin=slave, stdout=slave, stderr=slave, close_fds=True,
            start_new_session=True, cwd=os.path.expanduser("~"), env=full_env)
        os.close(slave)
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        while self.alive:
            try:
                ready, _, _ = select.select([self.master], [], [], 0.2)
                if self.master not in ready:
                    continue
                data = os.read(self.master, 65536)
                if not data:
                    break
                text = self._take_osc52(self.decoder.decode(data))
                if text:
                    with self.lock:
                        self._track_modes(text)
                        try:
                            self.stream.feed(text)
                        except Exception:
                            # A parser hiccup must never freeze the screen.
                            self.stream = pyte.Stream(self.screen)
                    self.notify()
            except (OSError, ValueError):
                break
        self.alive = False
        self.notify()

    def _take_osc52(self, text):
        """Cut OSC 52 clipboard writes out of the stream (pyte ignores them)
        and hand their text to on_clipboard. A sequence split across reads is
        held back until it completes; one over OSC52_MAX is dropped."""
        text, self._osc = self._osc + text, ""
        out, pos = [], 0
        while True:
            i = text.find(OSC52, pos)
            if i < 0:
                tail = next((n for n in range(len(OSC52) - 1, 0, -1) if text.endswith(OSC52[:n])), 0)
                out.append(text[pos:len(text) - tail])
                self._osc = text[len(text) - tail:] if tail else ""
                break
            out.append(text[pos:i])
            end = _OSC_END.search(text, i)
            if not end:
                if len(text) - i <= OSC52_MAX:
                    self._osc = text[i:]
                break
            if end.start() - i <= OSC52_MAX:
                clip = osc52_text(text[i + len(OSC52):end.start()])
                if clip:
                    self.on_clipboard(clip)
            pos = end.end()
        return "".join(out)

    def _track_modes(self, text):
        # Backstop for mouse modes set in sequences pyte splits oddly
        # (e.g. combined "ESC[?1000;1006h").
        for match in re.finditer(r"\x1b\[\?([\d;]+)([hl])", text):
            if {"1000", "1002", "1003"} & set(match.group(1).split(";")):
                self.mouse_reporting = match.group(2) == "h"

    def write(self, data):
        try:
            while data:
                data = data[os.write(self.master, data):]
            return True
        except OSError:
            return False

    def resize(self, cols, rows):
        cols, rows = max(10, min(cols, 400)), max(5, min(rows, 200))
        if (cols, rows) == (self.cols, self.rows):
            return
        self.cols, self.rows = cols, rows
        with self.lock:
            self.screen.resize(rows, cols)
        set_winsize(self.master, rows, cols)
        # Signal explicitly: the tmux client runs in its own session without a
        # controlling terminal, so the kernel's SIGWINCH never reaches it.
        try:
            self.proc.send_signal(signal.SIGWINCH)
        except OSError:
            pass
        self.notify()

    def _lines(self):
        buf = self.screen.buffer
        out = []
        for y in range(self.rows):
            line = buf[y] if y in buf else {}
            cells = []
            for x in range(self.cols):
                ch = line.get(x)
                cells.append(ch.data[:1] if ch and ch.data else (WIDE if ch is not None and x else " "))
            out.append("".join(cells))
        return out

    def text_lines(self):
        """Screen text, one cell per character (WIDE marks a wide char's right half)."""
        with self.lock:
            return self._lines()

    @property
    def bells(self):
        return self.screen.bells

    def _mouse(self):
        # pyte stores private modes shifted left by 5 (?1000 -> 32000).
        return self.mouse_reporting or any((m << 5) in self.screen.mode for m in (1000, 1002, 1003))

    @property
    def mouse(self):
        """Whether the app asked for mouse reports."""
        with self.lock:
            return self._mouse()

    def frame(self, sel=None, find=None):
        """The screen as RLE rows. `sel` is a normalised selection; `find` is
        (query, row) - every match is marked, the one nearest `row` as current."""
        with self.lock:
            cursor = (self.screen.cursor.x, self.screen.cursor.y)
            marks = sel_marks(sel, self.cols)
            if find and find[0]:
                spans = find_spans(self._lines(), find[0])
                cur =min(spans, key=lambda s: (abs(s[0] - find[1]), s[1])) if spans and find[1] is not None else None
                for s in spans:
                    marks.setdefault(s[0], []).insert(0, (s[1], s[2], "cur" if s == cur else "hit"))
            lines = encode_rows(self.screen, self.cols, self.rows, marks)
            mouse = self._mouse()
            hidden = pyte.modes.DECTCEM not in self.screen.mode
        return {"cols": self.cols, "rows": self.rows, "cursor": list(cursor), "mouse": mouse,
                "lines": lines, "cursor_hidden": hidden}

    def close(self):
        self.alive = False
        try:
            pgid = os.getpgid(self.proc.pid)
            os.killpg(pgid, signal.SIGHUP)
            try:
                self.proc.wait(timeout=2)
            except Exception:
                os.killpg(pgid, signal.SIGKILL)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        try:
            self.proc.wait(timeout=3)
        except Exception:
            pass
        try:
            os.close(self.master)
        except Exception:
            pass
