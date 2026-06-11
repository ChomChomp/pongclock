# Pong Clock

A terminal clock played as an endless game of Pong, built with
[Textual](https://textual.textualize.io/).

The left paddle plays for the **hours**, the right paddle plays for the
**minutes**, and the scoreboard *is* the current time. Both paddles play
perfect defense — until the clock is about to tick. When a minute passes,
the hours player deliberately misses so the minutes side scores. When the
hour rolls over, the minutes player misses, the hours side scores, and the
minutes score resets to 00. The ball's speed is quietly adjusted mid-rally
so it crosses the goal line right on the minute boundary.

Rendering uses half-block characters (`▀`) for double vertical resolution,
redrawn at 60 fps with a tiny framebuffer, so the animation stays fluid.

## Run it

```sh
python3 -m venv .venv
.venv/bin/pip install textual
.venv/bin/python pong_clock.py
```

| Key     | Action |
| ------- | ------ |
| `q`     | Quit   |
| `space` | Pause  |

## Test

```sh
.venv/bin/python smoke_test.py
```

Runs the app headless, verifies the ball animates, forces a scoring event,
and checks the pause toggle.
