"""Headless smoke test: run the app, let it animate, force a score."""

import asyncio
from datetime import datetime, timedelta

from pong_clock import PongClock, PongClockApp


async def main() -> None:
    app = PongClockApp()
    async with app.run_test(size=(96, 28)) as pilot:
        await pilot.pause(1.5)
        clock = app.query_one(PongClock)
        assert clock._ready, "field never initialized"
        assert clock._pending_v is None, "ball was never served"
        x1, y1 = clock.bx, clock.by
        await pilot.pause(0.5)
        assert (clock.bx, clock.by) != (x1, y1), "ball is not moving"
        assert any(any(row) for row in clock._buf), "framebuffer is empty"
        assert clock._score_px, "scores not stamped"
        assert clock.particles, "no trail particles emitted during play"
        shades = {val for _, _, val in clock._score_px}
        assert len(shades) > 1, f"score digits are not dithered: {shades}"

        # Force the ball out the left side and confirm a score/reserve cycle.
        clock.miss_side = "L"
        clock.bx, clock.bvx = 5.0, -abs(clock.base_speed) * 1.5
        await pilot.pause(0.6)
        assert clock._pending_v is not None or clock.bx > 0, "ball never re-served"

        # Regression: a ball that exits just BEFORE the minute boundary must
        # score the upcoming minute, not leave the score unchanged (which
        # forced the conceder to miss twice).
        near = datetime.now() + timedelta(seconds=1.2)
        clock.boundary = near
        clock.bx = -5.0
        clock._score()
        assert (clock.hh, clock.mm) == (near.hour, near.minute), "early exit did not score the upcoming minute"
        assert clock.boundary == near + timedelta(minutes=1), "boundary was not advanced past the scored minute"

        # A genuinely flukey miss (boundary far away) must NOT advance the clock.
        far = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=30)
        clock.boundary = far
        clock.bx = -5.0
        clock._score()
        now = datetime.now()
        assert (clock.hh, clock.mm) == (now.hour, now.minute), "fluke miss desynced the clock"

        # Pause toggle freezes the ball.
        await pilot.press("space")
        bx = clock.bx
        await pilot.pause(0.4)
        assert clock.bx == bx, "pause did not freeze the game"
        await pilot.press("space")

        print(f"scoreboard reads {clock.hh:02d}:{clock.mm:02d}")
    print("smoke test passed")


if __name__ == "__main__":
    asyncio.run(main())
