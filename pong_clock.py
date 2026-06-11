"""Pong Clock — a TUI clock played as an endless game of Pong.

The left paddle plays for the HOURS, the right paddle plays for the MINUTES,
and the scoreboard is the current time. When a minute passes, the hours
player deliberately misses and the minutes side scores. When the hour rolls
over, the minutes player misses, the hours side scores, and the minutes
score resets to 00.

Run:  python pong_clock.py     (q quits, space pauses)
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta

from rich.segment import Segment
from rich.style import Style
from textual.app import App, ComposeResult
from textual.strip import Strip
from textual.widget import Widget

FPS = 60

# Pixel values in the half-block framebuffer.
(
    EMPTY, NET,
    SCORE_DIM, SCORE_MID, SCORE_HI,
    TRAIL1, TRAIL2, TRAIL3, TRAIL4,
    BALL, PAD_L, PAD_R,
    SPARK_HOT, SPARK_L1, SPARK_L2, SPARK_R1, SPARK_R2,
) = range(17)

PALETTE = {
    EMPTY: "#0b0e14",
    NET: "#252b3d",
    SCORE_DIM: "#39415a",
    SCORE_MID: "#67719a",
    SCORE_HI: "#9aa5cf",
    TRAIL1: "#262b3a",
    TRAIL2: "#3d4458",
    TRAIL3: "#6a7288",
    TRAIL4: "#c4cade",
    BALL: "#f5f7ff",
    PAD_L: "#7aa2f7",
    PAD_R: "#f7768e",
    SPARK_HOT: "#ffffff",
    SPARK_L1: "#9db9ff",
    SPARK_L2: "#46669c",
    SPARK_R1: "#ffa3b5",
    SPARK_R2: "#a3475d",
}

# Particle color ramps, young -> old.
RAMPS = {
    "trail": (TRAIL4, TRAIL3, TRAIL2, TRAIL1),
    "spark_l": (SPARK_HOT, SPARK_L1, SPARK_L1, SPARK_L2),
    "spark_r": (SPARK_HOT, SPARK_R1, SPARK_R1, SPARK_R2),
}

MAX_PARTICLES = 500

# 4x4 Bayer matrix for ordered dithering of the scoreboard digits.
BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 5, 13, 7))

DIGITS = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "011", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
}


class PongClock(Widget):
    """Full-screen pong field rendered at 2x vertical resolution with ▀ cells."""

    DEFAULT_CSS = """
    PongClock {
        width: 1fr;
        height: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._ready = False
        self.paused = False
        self._style_cache: dict[tuple[int, int], Style] = {}

    # ------------------------------------------------------------- lifecycle

    def on_mount(self) -> None:
        self.set_interval(1 / FPS, self._tick)

    def on_resize(self) -> None:
        self._setup_field()

    def _setup_field(self) -> None:
        w = self.size.width
        h = self.size.height * 2
        if w < 24 or h < 20:
            self._ready = False
            return
        first = not self._ready
        self.W, self.H = w, h
        self._buf = [bytearray(w) for _ in range(h)]
        self._zero = bytes(w)
        self.base_speed = w / 1.7  # ~1.7s to cross the court
        self.pad_h = max(6, h // 5)
        self.pad_speed = h * 1.5
        self.l_plane = 4.0
        self.r_plane = w - 5.0
        self.ply = self.pry = h / 2
        self._scale = max(1, min(w // 24, h // 12))
        if first:
            now = datetime.now()
            self.hh, self.mm = now.hour, now.minute
            self.boundary = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        self.miss_side: str | None = None
        # Particles: [x, y, vx, vy, age, life, kind]
        self.particles: list[list] = []
        self._stamp_scores()
        self._serve(random.choice((-1, 1)))
        self._last = time.monotonic()
        self._ready = True

    # ----------------------------------------------------------------- game

    def _serve(self, direction: int) -> None:
        self.bx, self.by = self.W / 2, self.H / 2
        self.rally_speed = self.base_speed
        vy = random.uniform(0.25, 0.6) * self.base_speed * random.choice((-1, 1))
        self._pending_v: tuple[float, float] | None = (direction * self.base_speed, vy)
        self.bvx = self.bvy = 0.0
        self._serve_at = time.monotonic() + 0.9

    def _tick(self) -> None:
        if not self._ready:
            self._setup_field()
            if not self._ready:
                return
        now_m = time.monotonic()
        dt = min(now_m - self._last, 0.05)
        self._last = now_m
        if self.paused:
            return
        if self._pending_v is not None:
            if now_m >= self._serve_at:
                self.bvx, self.bvy = self._pending_v
                self._pending_v = None
        else:
            self._update_play(dt)
        self._update_particles(dt)
        self._move_paddles(dt)
        self._draw()
        self.refresh()

    def _update_play(self, dt: float) -> None:
        self._check_boundary()
        prev_x, prev_y = self.bx, self.by
        self.bx += self.bvx * dt
        self.by += self.bvy * dt
        bot = self.H - 1.0
        if self.by < 0.0:
            self.by = -self.by
            self.bvy = abs(self.bvy)
        elif self.by > bot:
            self.by = 2 * bot - self.by
            self.bvy = -abs(self.bvy)
        reach = self.pad_h / 2 + 1.5
        if self.bvx < 0 and self.bx <= self.l_plane and self.miss_side != "L":
            if abs(self.by - self.ply) <= reach:
                self.bx = 2 * self.l_plane - self.bx
                self._rebound(self.ply, 1, "spark_l")
        elif self.bvx > 0 and self.bx >= self.r_plane and self.miss_side != "R":
            if abs(self.by - self.pry) <= reach:
                self.bx = 2 * self.r_plane - self.bx
                self._rebound(self.pry, -1, "spark_r")
        self._emit_trail(prev_x, prev_y)
        if self.bx < -2 or self.bx > self.W + 1:
            self._score()

    def _rebound(self, pad_y: float, direction: int, spark_kind: str) -> None:
        rel = (self.by - pad_y) / (self.pad_h / 2)
        rel = max(-1.2, min(1.2, rel))
        self.bvx = direction * self.rally_speed
        vy = (0.85 * rel + random.uniform(-0.08, 0.08)) * self.rally_speed
        cap = 1.05 * self.rally_speed
        self.bvy = max(-cap, min(cap, vy))
        impact_x = self.l_plane if direction > 0 else self.r_plane
        self._emit_sparks(impact_x, self.by, direction, spark_kind)

    # ------------------------------------------------------------- particles

    def _emit_trail(self, prev_x: float, prev_y: float) -> None:
        # Spread the particles along this frame's movement segment so the
        # tail stays continuous at high ball speeds.
        for _ in range(3):
            t = random.random()
            self.particles.append([
                prev_x + (self.bx - prev_x) * t + random.uniform(-0.5, 1.5),
                prev_y + (self.by - prev_y) * t + random.uniform(-0.5, 1.5),
                -self.bvx * 0.08 + random.uniform(-4.0, 4.0),
                -self.bvy * 0.08 + random.uniform(-4.0, 4.0),
                0.0,
                random.uniform(0.25, 0.55),
                "trail",
            ])

    def _emit_sparks(self, x: float, y: float, direction: int, kind: str) -> None:
        speed = self.base_speed
        for _ in range(random.randint(12, 18)):
            vx = direction * random.uniform(0.1, 0.9) * speed
            if random.random() < 0.15:  # a few fly backwards off the face
                vx = -direction * random.uniform(0.05, 0.3) * speed
            self.particles.append([
                x,
                y + random.uniform(-1.5, 1.5),
                vx,
                random.uniform(-0.6, 0.6) * speed,
                0.0,
                random.uniform(0.25, 0.7),
                kind,
            ])

    def _update_particles(self, dt: float) -> None:
        damp = max(0.0, 1.0 - 2.0 * dt)
        alive = []
        for p in self.particles:
            p[4] += dt
            if p[4] >= p[5]:
                continue
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            p[2] *= damp
            p[3] *= damp
            if -2 <= p[0] < self.W + 2 and -2 <= p[1] < self.H + 2:
                alive.append(p)
        if len(alive) > MAX_PARTICLES:
            alive = alive[-MAX_PARTICLES:]
        self.particles = alive

    def _check_boundary(self) -> None:
        """Pick the paddle that must concede so the ball exits on the tick."""
        if self.miss_side is not None:
            return
        t_b = (self.boundary - datetime.now()).total_seconds()
        conceder = "R" if self.boundary.minute == 0 else "L"
        if t_b <= 0:
            self.miss_side = conceder
            return
        vx = self.bvx
        if vx == 0:
            return
        # Distances measured to the actual exit thresholds (-2 and W + 1).
        heading = "L" if vx < 0 else "R"
        if heading == conceder:
            dist = self.bx + 2 if conceder == "L" else self.W + 1 - self.bx
        elif heading == "R":  # conceder is L; out to the right paddle and back
            dist = (self.r_plane - self.bx) + (self.r_plane + 2)
        else:  # heading L, conceder R
            dist = (self.bx - self.l_plane) + (self.W + 1 - self.l_plane)
        t_path = dist / abs(vx)
        if t_b <= t_path:
            self.miss_side = conceder
            factor = min(t_path / max(t_b, 0.05), 1.8)
            self.bvx *= factor
            self.bvy *= factor
            self.rally_speed *= factor

    def _score(self) -> None:
        now = datetime.now()
        early = (self.boundary - now).total_seconds()
        if 0 < early <= 2.0:
            # The ball beat the clock by a hair: score the minute that is
            # about to tick, otherwise the conceder would have to miss twice.
            self.hh, self.mm = self.boundary.hour, self.boundary.minute
            self.boundary = self.boundary + timedelta(minutes=1)
        else:
            self.hh, self.mm = now.hour, now.minute
            self.boundary = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        loser = "L" if self.bx < 0 else "R"
        self.miss_side = None
        self._stamp_scores()
        self._serve(-1 if loser == "L" else 1)

    # ------------------------------------------------------------ paddle AI

    def _predict_y(self, plane_x: float) -> float:
        if self.bvx == 0:
            return self.by
        t = (plane_x - self.bx) / self.bvx
        if t <= 0:
            return self.by
        span = self.H - 1.0
        y = (self.by + self.bvy * t) % (2 * span)
        if y < 0:
            y += 2 * span
        return 2 * span - y if y > span else y

    def _paddle_target(self, side: str) -> float:
        if self._pending_v is not None:
            return self.H / 2
        plane = self.l_plane if side == "L" else self.r_plane
        approaching = self.bvx < 0 if side == "L" else self.bvx > 0
        pred = self._predict_y(plane) if approaching else None
        if self.miss_side == side:
            base = pred if pred is not None else self.by
            off = self.pad_h + 4
            return base + off if base < self.H / 2 else base - off
        if pred is not None:
            return pred
        return self.H / 2 + (self.by - self.H / 2) * 0.25

    def _move_paddles(self, dt: float) -> None:
        step = self.pad_speed * dt
        half = self.pad_h / 2
        for side in ("L", "R"):
            pos = self.ply if side == "L" else self.pry
            delta = self._paddle_target(side) - pos
            pos += max(-step, min(step, delta))
            pos = max(half, min(self.H - 1 - half, pos))
            if side == "L":
                self.ply = pos
            else:
                self.pry = pos

    # ------------------------------------------------------------ rendering

    def _stamp_scores(self) -> None:
        s = self._scale
        dw = 3 * s
        cx = self.W // 2
        top = 2
        glyph_h = max(1, 5 * s - 1)
        pixels: list[tuple[int, int, int]] = []

        def shade(xx: int, yy: int) -> int:
            # Vertical fade dithered across three shades with a Bayer matrix.
            t = 0.95 - 0.55 * ((yy - top) / glyph_h)
            level = max(0.0, min(2.0, t * 2.0))
            base = min(int(level), 1)
            threshold = (BAYER[yy % 4][xx % 4] + 0.5) / 16
            return SCORE_DIM + base + (1 if level - base > threshold else 0)

        def stamp(x0: int, text: str) -> None:
            for i, ch in enumerate(text):
                glyph = DIGITS[ch]
                gx = x0 + i * (dw + s)
                for r, row in enumerate(glyph):
                    for c, bit in enumerate(row):
                        if bit == "1":
                            for yy in range(top + r * s, top + (r + 1) * s):
                                for xx in range(gx + c * s, gx + (c + 1) * s):
                                    if 0 <= xx < self.W and 0 <= yy < self.H:
                                        pixels.append((xx, yy, shade(xx, yy)))

        stamp(cx - 3 * s - (2 * dw + s), f"{self.hh:02d}")
        stamp(cx + 3 * s + 1, f"{self.mm:02d}")
        self._score_px = pixels

    def _plot(self, x: int, y: int, value: int) -> None:
        if 0 <= x < self.W and 0 <= y < self.H:
            self._buf[y][x] = value

    def _draw(self) -> None:
        zero = self._zero
        for row in self._buf:
            row[:] = zero
        cx = self.W // 2
        for y in range(self.H):
            if (y // 3) % 2 == 0:
                self._buf[y][cx] = NET
        for x, y, val in self._score_px:
            self._buf[y][x] = val
        for px, py, _, _, age, life, kind in self.particles:
            ramp = RAMPS[kind]
            idx = min(int(age / life * len(ramp)), len(ramp) - 1)
            self._plot(int(px), int(py), ramp[idx])
        y0 = int(self.ply - self.pad_h / 2)
        for yy in range(y0, y0 + self.pad_h):
            self._plot(2, yy, PAD_L)
            self._plot(3, yy, PAD_L)
        y0 = int(self.pry - self.pad_h / 2)
        for yy in range(y0, y0 + self.pad_h):
            self._plot(self.W - 4, yy, PAD_R)
            self._plot(self.W - 3, yy, PAD_R)
        ix, iy = int(self.bx), int(self.by)
        for dx in (0, 1):
            for dy in (0, 1):
                self._plot(ix + dx, iy + dy, BALL)

    def _style(self, pair: tuple[int, int]) -> Style:
        style = self._style_cache.get(pair)
        if style is None:
            style = Style(color=PALETTE[pair[0]], bgcolor=PALETTE[pair[1]])
            self._style_cache[pair] = style
        return style

    def render_line(self, y: int) -> Strip:
        if not self._ready or 2 * y + 1 >= len(self._buf):
            return Strip.blank(self.size.width)
        top = self._buf[2 * y]
        bot = self._buf[2 * y + 1]
        segments: list[Segment] = []
        x0 = 0
        current = (top[0], bot[0])
        for x in range(1, self.W):
            pair = (top[x], bot[x])
            if pair != current:
                segments.append(Segment("▀" * (x - x0), self._style(current)))
                x0 = x
                current = pair
        segments.append(Segment("▀" * (self.W - x0), self._style(current)))
        return Strip(segments, self.W)


class PongClockApp(App):
    """HOURS (left, blue) vs MINUTES (right, pink). The score is the time."""

    CSS = """
    Screen {
        background: #0b0e14;
    }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("space", "toggle_pause", "Pause"),
    ]
    ENABLE_COMMAND_PALETTE = False

    def compose(self) -> ComposeResult:
        yield PongClock()

    def action_toggle_pause(self) -> None:
        clock = self.query_one(PongClock)
        clock.paused = not clock.paused


def main() -> None:
    PongClockApp().run()


if __name__ == "__main__":
    main()
