"""Tiny Atari-2600-style engine for the terminal.

The screen is an 80x44 grid of chunky "pixels" drawn with half-block
characters (two pixels per terminal cell, so pixels are square) using the
xterm 256-colour palette. Needs a terminal of at least 80 columns x 23 rows.
Standard library only.
"""
import os
import select
import shutil
import sys
import termios
import time
import tty

W, H = 80, 44
MIN_COLS, MIN_ROWS = W, H // 2 + 1  # +1 for the status line

# 3x5 pixel font, one 15-char string per glyph (row-major, '1' = lit).
_FONT_SRC = {
    "A": "010101111101101", "B": "110101110101110", "C": "011100100100011",
    "D": "110101101101110", "E": "111100110100111", "F": "111100110100100",
    "G": "011100101101011", "H": "101101111101101", "I": "111010010010111",
    "J": "001001001101010", "K": "101101110101101", "L": "100100100100111",
    "M": "101111111101101", "N": "110101101101101", "O": "010101101101010",
    "P": "110101110100100", "Q": "010101101110011", "R": "110101110101101",
    "S": "011100010001110", "T": "111010010010010", "U": "101101101101111",
    "V": "101101101101010", "W": "101101111111101", "X": "101101010101101",
    "Y": "101101010010010", "Z": "111001010100111",
    "0": "111101101101111", "1": "010110010010111", "2": "110001010100111",
    "3": "110001010001110", "4": "101101111001001", "5": "111100110001110",
    "6": "011100111101111", "7": "111001010010010", "8": "111101111101111",
    "9": "111101111001110", "-": "000000111000000", ":": "000010000010000",
    "!": "010010010000010", " ": "000000000000000",
}


def text_width(s, scale=1):
    return len(s) * 4 * scale - scale


class Screen:
    def __init__(self):
        self.fb = [[0] * W for _ in range(H)]
        self.labels = {}  # (row, col) -> (char, fg, bg or None, bold); real terminal text over the pixels

    def label(self, col, row, s, fg=255, bg=None, bold=True):
        for i, ch in enumerate(s):
            self.labels[(row, col + i)] = (ch, fg, bg, bold)

    def label_center(self, row, s, fg=255, bg=None, bold=True):
        self.label((W - len(s)) // 2, row, s, fg, bg, bold)

    def load(self, other_fb):
        self.fb = [row[:] for row in other_fb]

    def fill(self, c):
        self.fb = [[c] * W for _ in range(H)]

    def px(self, x, y, c):
        x = int(x)
        y = int(y)
        if 0 <= x < W and 0 <= y < H:
            self.fb[y][x] = c

    def rect(self, x, y, w, h, c):
        for yy in range(int(y), int(y + h)):
            for xx in range(int(x), int(x + w)):
                self.px(xx, yy, c)

    def line(self, x0, y0, x1, y1, c):
        x0, y0, x1, y1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.px(x0, y0, c)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def text(self, s, x, y, c, scale=1):
        for ch in s.upper():
            g = _FONT_SRC.get(ch, _FONT_SRC[" "])
            for i, bit in enumerate(g):
                if bit == "1":
                    self.rect(x + (i % 3) * scale, y + (i // 3) * scale, scale, scale, c)
            x += 4 * scale

    def text_center(self, s, y, c, scale=1, shadow=None):
        x = (W - text_width(s, scale)) // 2
        if shadow is not None:
            self.text(s, x + scale, y + scale, shadow, scale)
        self.text(s, x, y, c, scale)

    def render(self, status=""):
        out = ["\x1b[H"]
        fb = self.fb
        for r in range(H // 2):
            top, bot = fb[2 * r], fb[2 * r + 1]
            fg = bg = None
            for c in range(W):
                t, b = top[c], bot[c]
                ov = self.labels.get((r, c))
                if ov:
                    ch, lfg, lbg, bold = ov
                    out.append("\x1b[0;%s38;5;%d;48;5;%dm%s" % ("1;" if bold else "", lfg, t if lbg is None else lbg, ch))
                    fg = bg = None
                elif t == b:
                    if bg != t:
                        out.append("\x1b[48;5;%dm" % t)
                        bg = t
                    out.append(" ")
                else:
                    if fg != t:
                        out.append("\x1b[38;5;%dm" % t)
                        fg = t
                    if bg != b:
                        out.append("\x1b[48;5;%dm" % b)
                        bg = b
                    out.append("▀")
            out.append("\x1b[0m\r\n")
        out.append("\x1b[0m\x1b[K" + status)
        self.labels = {}
        return "".join(out)


_ESC_KEYS = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT"}


class Terminal:
    """Raw-mode terminal on the alternate screen; non-blocking key reads."""

    def __enter__(self):
        cols, rows = shutil.get_terminal_size()
        if cols < MIN_COLS or rows < MIN_ROWS:
            sys.exit("Terminal too small: need %dx%d, have %dx%d. Enlarge the window and retry."
                     % (MIN_COLS, MIN_ROWS, cols, rows))
        self.fd = sys.stdin.fileno()
        self.old = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        os.write(1, b"\x1b[?1049h\x1b[?25l\x1b[2J")
        return self

    def __exit__(self, *exc):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)
        os.write(1, b"\x1b[0m\x1b[?25h\x1b[?1049l")

    def keys(self):
        data = b""
        while select.select([self.fd], [], [], 0)[0]:
            chunk = os.read(self.fd, 256)
            if not chunk:
                break
            data += chunk
        out = []
        i = 0
        while i < len(data):
            b = data[i]
            if b == 0x1B:
                if i + 2 < len(data) and data[i + 1] in (0x5B, 0x4F) and chr(data[i + 2]) in _ESC_KEYS:
                    out.append(_ESC_KEYS[chr(data[i + 2])])
                    i += 3
                    continue
                out.append("ESC")
            elif b == 0x20:
                out.append("SPACE")
            elif b in (0x0D, 0x0A):
                out.append("ENTER")
            elif 32 < b < 127:
                out.append(chr(b).lower())
            i += 1
        return out

    def draw(self, frame):
        os.write(1, frame.encode("utf-8"))


def run(game, fps=30):
    """Fixed-timestep loop. `game` needs update(keys, now)->bool(keep running), draw(screen)->status."""
    screen = Screen()
    step = 1.0 / fps
    with Terminal() as term:
        nxt = time.monotonic()
        while True:
            now = time.monotonic()
            if not game.update(term.keys(), now):
                break
            status = game.draw(screen)
            term.draw(screen.render(status))
            nxt += step
            delay = nxt - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                nxt = time.monotonic()
