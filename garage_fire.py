#!/usr/bin/env python3
"""GARAGE FIRE - an Atari 2600 style garage-fire game for the terminal.

Aim the hose with the joystick (arrow keys / WASD), hold the button (SPACE)
to extend the water stream. The stream's reach is how long you hold; its
arc is where you aim. Put every fire out before the garage burns down.
"""
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from engine import Screen, W, H, run  # noqa: E402

GROUND = 36            # first ground row; feet rest on GROUND-1
SPR_H = 17
MAX_REACH = 88.0
DROOP = 0.007          # gravity bend of the stream
HOLD_FIRST = 0.60      # seconds a first SPACE press counts as "held" (covers key-repeat delay)
HOLD_REPEAT = 0.14     # seconds each auto-repeat extends it

# --- palette (xterm-256 indices) -------------------------------------------
SKY = [17, 18, 54, 90, 126, 162, 168, 174, 180, 216, 222, 229]   # dawn bands, top -> horizon
SUN, SUN_RIM = 227, 221
HILL, HILL2 = 53, 54
GRASS, GRASS_DK, DRIVE = 29, 22, 244
WALLS = [  # (siding, shade, roof, roof_dark) by how burnt the garage is
    (181, 138, 96, 95),
    (138, 95, 240, 238),
    (240, 237, 235, 234),
]
TRIM, DOOR_DARK, DOOR_BACK, DOOR_FLOOR, GLASS = 252, 233, 235, 237, 24
FLAME = [230, 227, 220, 208, 196, 160]
WATER = [255, 51, 39]
SMOKE = [238, 240, 242, 244, 246, 248]
HUD = 227

SPRITE = [
    "..HHHH...",
    ".HHHHHH..",
    "HHHHHHHH.",
    "..HSSSS..",
    "...SSSSS.",
    "...SSS...",
    "..CCCCC..",
    "TCCCCCCC.",
    "TCYYYYYC.",
    "TCCCCCCC.",
    "TCCCCCCC.",
    ".CYYYYYC.",
    ".PPPPPPP.",
    "..PPPPP..",
    "..PP.PP..",
    "..PP.PP..",
    ".BBB.BBB.",
]
SPR_COL = {"H": 214, "S": 180, "C": 136, "Y": 227, "P": 94, "B": 235, "T": 245}

# fire sites: (centre x, base y, width, max height)
SITE_DEFS = [
    (56, 35, 9, 12), (66, 35, 9, 14), (53, 16, 8, 9), (48, 29, 5, 8),
    (69, 15, 8, 9), (74, 29, 5, 8), (61, 35, 8, 10), (61, 13, 6, 8),
]
SITE_ORDER = [0, 1, 2, 3, 4, 5, 6, 7]


class Site:
    def __init__(self, x, base, w, h, hp):
        self.x, self.base, self.w, self.h, self.hp = x, base, w, h, hp
        self.out = False
        self.phase = random.random() * 6.28

    def height(self):
        return self.h * (0.25 + 0.75 * self.hp / 100.0)


class Game:
    def __init__(self):
        self.state = "title"
        self.high = 0
        self.frame = 0
        self.parts = []
        self.stars = [(random.randrange(W), random.randrange(2, 16)) for _ in range(14)]
        self.reset()

    # ---- setup ---------------------------------------------------------
    def reset(self):
        self.score = 0
        self.level = 1
        self.integrity = 100.0
        self.water = 100.0
        self.sputter = False
        self.start_level()

    def start_level(self):
        n = min(2 + self.level, len(SITE_DEFS))
        self.sites = [Site(*SITE_DEFS[i], hp=random.uniform(35, 60)) for i in SITE_ORDER[:n]]
        self.growth = 2.5 + 0.7 * self.level
        self.angle = 32.0
        self.fx = 10
        self.reach = 0.0
        self.hold_until = 0.0
        self.parts = []
        self.timer = 0.0

    # ---- geometry --------------------------------------------------------
    def hand(self):
        a = math.radians(self.angle)
        return self.fx + 7 + math.cos(a) * 2, GROUND - SPR_H + 8 - math.sin(a) * 2.5

    def nozzle(self):
        a = math.radians(self.angle)
        hx, hy = self.hand()
        return hx + math.cos(a) * 6, hy - math.sin(a) * 6

    def stream(self, length):
        """Points (x, y, s) along the ballistic stream; stops at the ground."""
        a = math.radians(self.angle)
        ca, sa = math.cos(a), math.sin(a)
        x0, y0 = self.nozzle()
        pts, s = [], 0.0
        while s <= length:
            x, y = x0 + s * ca, y0 - s * sa + DROOP * s * s
            if y >= GROUND - 1:
                pts.append((x, GROUND - 1, s))
                break
            if x >= W:
                break
            pts.append((x, y, s))
            s += 0.7
        return pts

    # ---- update ----------------------------------------------------------
    def update(self, keys, now):
        self.frame += 1
        dt = 1.0 / 30
        for k in keys:
            if k in ("q", "ESC"):
                return False
        if self.state == "title":
            if "SPACE" in keys or "ENTER" in keys:
                self.reset()
                self.state = "play"
            return True
        if self.state == "over":
            if "SPACE" in keys or "r" in keys:
                self.reset()
                self.state = "play"
            return True
        if "p" in keys and self.state in ("play", "pause"):
            self.state = "pause" if self.state == "play" else "play"
        if self.state == "pause":
            return True

        # joystick
        for k in keys:
            if k in ("UP", "w"):
                self.angle = min(80.0, self.angle + 3)
            elif k in ("DOWN", "s"):
                self.angle = max(4.0, self.angle - 3)
            elif k in ("LEFT", "a"):
                self.fx = max(8, self.fx - 1)
            elif k in ("RIGHT", "d"):
                self.fx = min(30, self.fx + 1)
            elif k == "SPACE":
                self.hold_until = now + (HOLD_FIRST if self.hold_until < now else HOLD_REPEAT)

        if self.state == "clear":
            self.timer -= dt
            self.step_particles(dt)
            if self.timer <= 0:
                self.level += 1
                self.integrity = min(100.0, self.integrity + 25)
                self.start_level()
                self.state = "play"
            return True

        # button -> stream length
        held = now < self.hold_until
        if self.sputter and self.water > 25:
            self.sputter = False
        if self.water <= 0:
            self.sputter = True
        if held and not self.sputter:
            self.reach = min(MAX_REACH, self.reach + 55 * dt)
            self.water = max(0.0, self.water - 14 * dt)
        else:
            self.reach = max(0.0, self.reach - 120 * dt)
            self.water = min(100.0, self.water + 8 * dt)

        # water vs fire
        pts = self.stream(self.reach) if self.reach > 1 else []
        if pts:
            head = [p for p in pts if p[2] >= self.reach - 10]
            for s in self.sites:
                if s.out:
                    continue
                top = s.base - s.height() - 1
                hit = any(abs(x - s.x) <= s.w / 2 + 1 and top <= y <= s.base + 1 for x, y, _ in head)
                if hit:
                    s.hp -= 55 * dt
                    self.spawn("steam", s.x + random.uniform(-2, 2), s.base - s.height() / 2)
                    if s.hp <= 0:
                        s.out = True
                        self.score += 100 * self.level
                        for _ in range(10):
                            self.spawn("steam", s.x + random.uniform(-3, 3), s.base - 2)
            ex, ey, _ = pts[-1]
            if self.frame % 2 == 0:
                self.spawn("splash", ex, ey)

        # fire growth, smoke, embers, damage to the garage
        burn = 0.0
        for s in self.sites:
            if s.out:
                if random.random() < 0.05:
                    self.spawn("smoke", s.x, s.base - 2)
                continue
            s.hp = min(100.0, s.hp + self.growth * dt)
            burn += s.hp / 100.0
            top = s.base - s.height()
            if random.random() < 0.12 * s.hp / 100:
                self.spawn("smoke", s.x + random.uniform(-2, 2), top)
            if random.random() < 0.15 * s.hp / 100:
                self.spawn("ember", s.x + random.uniform(-2, 2), top)
        self.integrity -= burn * 0.4 * dt
        self.step_particles(dt)

        if self.integrity <= 0:
            self.integrity = 0
            self.state = "over"
            self.high = max(self.high, self.score)
        elif all(s.out for s in self.sites):
            bonus = int(self.integrity) * 5
            self.score += bonus
            self.bonus = bonus
            self.high = max(self.high, self.score)
            self.state = "clear"
            self.timer = 3.5
            self.reach = 0.0
        return True

    # ---- particles -------------------------------------------------------
    def spawn(self, kind, x, y):
        r = random.uniform
        if kind == "smoke":
            p = [x, y, r(3, 7), -r(5, 9), 0.0, r(2.5, 4.0), kind]
        elif kind == "ember":
            p = [x, y, r(-4, 4), -r(14, 24), 0.0, r(0.7, 1.2), kind]
        elif kind == "steam":
            p = [x, y, r(-3, 5), -r(10, 16), 0.0, r(0.6, 1.1), kind]
        else:  # splash
            p = [x, y, r(-14, 14), -r(10, 22), 0.0, r(0.3, 0.5), kind]
        if len(self.parts) < 260:
            self.parts.append(p)

    def step_particles(self, dt):
        keep = []
        for p in self.parts:
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            if p[6] == "splash":
                p[3] += 60 * dt
            p[4] += dt
            if p[4] < p[5] and -2 < p[0] < W + 2 and p[1] > -2:
                keep.append(p)
        self.parts = keep

    # ---- drawing ---------------------------------------------------------
    def draw_scene(self, s):
        # sky bands
        band = GROUND // len(SKY)
        for i, c in enumerate(SKY):
            s.rect(0, i * band, W, band, c)
        for x, y in self.stars:
            if y < 12 and (x + self.frame // 20) % 5:
                s.px(x, y, 252 if y < 7 else 250)
        # sun + hills
        cx, cy, r = 24, 33, 8
        for y in range(cy - r, GROUND):
            for x in range(cx - r - 2, cx + r + 3):
                d = math.hypot(x - cx, (y - cy) * 1.0)
                if d <= r - 1:
                    s.px(x, y, SUN)
                elif d <= r + 1:
                    s.px(x, y, SUN_RIM)
        for x in range(W):
            top = int(31 - 3 * math.sin(x * 0.11 + 1) - 2 * math.sin(x * 0.27))
            s.rect(x, top, 1, GROUND - top, HILL if x % 7 else HILL2)
        # ground
        s.rect(0, GROUND, W, 2, GRASS)
        s.rect(0, GROUND + 2, W, H - GROUND - 2, GRASS_DK)
        s.rect(40, GROUND, 40, 2, DRIVE)
        self.draw_garage(s)
        # hydrant
        s.rect(1, 31, 3, 5, 196)
        s.rect(0, 30, 5, 1, 231)
        s.px(4, 33, 245)

    def draw_garage(self, s):
        k = 0 if self.integrity > 66 else 1 if self.integrity > 33 else 2
        wall, shade, roof, roof_dk = WALLS[k]
        for y in range(12, 20):
            hw = 2 + int((y - 12) * 2.4)
            s.rect(61 - hw, y, 2 * hw + 1, 1, roof if y % 2 == 0 else roof_dk)
        s.rect(43, 19, 37, 1, shade)
        s.rect(46, 20, 31, 16, wall)
        for x in range(46, 77):
            if x % 3 == 0:
                s.rect(x, 20, 1, 16, shade)
        s.rect(51, 23, 21, 13, TRIM)
        s.rect(52, 24, 19, 12, DOOR_BACK)
        s.rect(52, 34, 19, 2, DOOR_FLOOR)
        s.rect(54, 28, 6, 1, 94)           # shelf
        s.rect(54, 31, 3, 3, 130)          # boxes
        s.rect(52, 24, 19, 2, 247)         # rolled-up door
        s.rect(52, 25, 19, 1, 244)
        for x0 in (47, 72):
            s.rect(x0 - 1, 24, 6, 7, TRIM)
            s.rect(x0, 25, 4, 5, GLASS)

    def draw_flames(self, s):
        f = self.frame // 2
        for i, st in enumerate(self.sites):
            if st.out:
                continue
            h = st.height()
            half = st.w / 2.0
            for dx in range(-int(half), int(half) + 1):
                shape = max(0.0, 1 - abs(dx) / (half + 1)) ** 0.8
                flick = 0.78 + 0.22 * math.sin(f * 0.9 + dx * 1.7 + st.phase) + 0.08 * math.sin(f * 2.3 + dx)
                ch = int(round(h * shape * flick))
                for j in range(max(ch, 1)):
                    frac = j / max(ch, 1)
                    c = FLAME[0] if frac < .2 else FLAME[1] if frac < .4 else FLAME[2] if frac < .6 \
                        else FLAME[3] if frac < .8 else FLAME[4] if j < ch - 1 else FLAME[5]
                    s.px(st.x + dx, st.base - j, c)

    def draw_particles(self, s, smoke):
        for x, y, _, _, age, life, kind in self.parts:
            f = age / life
            if (kind == "smoke") != smoke:
                continue
            if kind == "smoke":
                c = SMOKE[min(len(SMOKE) - 1, int(f * len(SMOKE)))]
            elif kind == "ember":
                c = 220 if f < .4 else 208 if f < .7 else 124
            elif kind == "steam":
                c = 255 if f < .5 else 250
            else:
                c = 51 if f < .5 else 255
            s.px(x, y, c)

    def draw_firefighter(self, s):
        fx, y0 = self.fx, GROUND - SPR_H
        hx, hy = self.hand()
        nx, ny = self.nozzle()
        a = math.radians(self.angle)
        bx, by = hx - math.cos(a) * 2, hy + math.sin(a) * 2
        # hose: hydrant -> along the ground -> up to the nozzle
        s.line(4, 33, 7, GROUND - 1, 242)
        s.line(7, GROUND - 1, fx + 1, GROUND - 1, 242)
        s.line(fx + 1, GROUND - 1, fx + 3, GROUND - 6, 242)
        s.line(fx + 3, GROUND - 6, bx, by, 242)
        s.line(fx + 3, GROUND - 7, bx, by + 1, 242)
        for r, row in enumerate(SPRITE):
            for c, ch in enumerate(row):
                if ch != ".":
                    s.px(fx + c, y0 + r, SPR_COL[ch])
        s.line(fx + 5, y0 + 7, hx, hy, 136)       # arm
        s.line(hx, hy, nx, ny, 178)               # nozzle
        s.px(hx, hy, 94)
        s.px(nx, ny, 220)

    def draw_stream(self, s):
        if self.reach <= 1:
            return
        L = self.reach
        for x, y, d in self.stream(L):
            c = WATER[(int(d / 1.5) - self.frame) % 3]
            s.px(x, y, c)
            if d < L * 0.6:
                s.px(x, y + 1, 39)

    def draw_hud(self, s):
        s.label(2, 0, "SCORE %05d" % self.score, 255, 17)
        s.label_center(0, "LEVEL %d" % self.level, HUD, 17)
        s.label(W - 11, 0, "HI %05d" % self.high, 250, 17)
        # tank and garage meters on the ground strip
        s.label(2, 20, "WATER", HUD, GRASS_DK)
        s.rect(9, 39, 29, 4, 17)
        n = int(self.water / 100 * 27)
        s.rect(10, 40, n, 2, 196 if self.sputter or self.water < 25 else 51)
        s.label(44, 20, "GARAGE", HUD, GRASS_DK)
        s.rect(51, 39, 28, 4, 17)
        n = int(self.integrity / 100 * 26)
        s.rect(52, 40, n, 2, 46 if self.integrity > 60 else 226 if self.integrity > 30 else 196)

    def banner(self, s, row, text, fg=255, bg=17, spaced=False):
        if spaced:
            text = " ".join(text)
        s.label_center(row, "  %s  " % text, fg, bg)

    def draw(self, s):
        self.draw_scene(s)
        self.draw_particles(s, smoke=True)
        self.draw_flames(s)
        self.draw_firefighter(s)
        self.draw_stream(s)
        self.draw_particles(s, smoke=False)
        self.draw_hud(s)
        if self.state == "title":
            self.banner(s, 4, "GARAGE FIRE", 227, 124, spaced=True)
            self.banner(s, 6, "EARLY MORNING SHIFT", 250, 17)
            if self.frame % 40 < 28:
                self.banner(s, 10, "PRESS SPACE TO START", 255, 17)
        elif self.state == "pause":
            self.banner(s, 8, "PAUSED", 255, 17, spaced=True)
        elif self.state == "clear":
            self.banner(s, 7, "ALL OUT!", 255, 22, spaced=True)
            self.banner(s, 9, "BONUS %d" % self.bonus, HUD, 17)
        elif self.state == "over":
            self.banner(s, 7, "GAME OVER", 255, 124, spaced=True)
            self.banner(s, 9, "THE GARAGE IS LOST", HUD, 17)
            if self.frame % 40 < 28:
                self.banner(s, 11, "PRESS SPACE TO RETRY", 255, 17)
        bar = "#" * int(self.reach / MAX_REACH * 10)
        return " ↑↓ AIM  ←→ MOVE  HOLD SPACE = SPRAY  P PAUSE  Q QUIT   AIM %2d  REACH [%-10s]" % (
            self.angle, bar)


def main():
    run(Game())


if __name__ == "__main__":
    main()
