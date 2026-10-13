"""Where the refresh loop's time actually goes.

The loop runs four times a second, and when it stops being smooth there is
no way to tell from the outside which stage is costing the time: capture,
recognition, scoring and redraw all happen inside one tick. Guessing has
already been wrong once — the stutter that looked like scoring turned out
to be a hidden widget being smooth-scaled — so this measures instead.

Deliberately cheap: two `perf_counter` calls and a deque append per stage,
which is microseconds against stages measured in milliseconds. It is always
on, because a profiler you have to switch on is one that is off when the
thing you wanted to catch happens.

The numbers are kept as a rolling window rather than a running mean: what
matters is what the loop is doing NOW, and an average over an hour of
sitting in the menu hides the ten seconds of draft that were bad.
"""

import time
from collections import OrderedDict, deque
from contextlib import contextmanager

# Enough ticks to cover several seconds at 4Hz, few enough that a slow
# patch shows up rather than being averaged away.
WINDOW = 40
# A tick longer than this is one the user can see. 250ms is the loop's own
# period: past it the app is behind.
SLOW_MS = 120.0


class Stopwatch:
    """Rolling per-stage timings, in call order."""

    def __init__(self, window: int = WINDOW):
        self.window = window
        self._stages: OrderedDict[str, deque] = OrderedDict()
        self.ticks = 0
        self.worst_tick_ms = 0.0

    @contextmanager
    def stage(self, name: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.record(name, (time.perf_counter() - started) * 1000.0)

    def record(self, name: str, ms: float) -> None:
        samples = self._stages.get(name)
        if samples is None:
            samples = self._stages[name] = deque(maxlen=self.window)
        samples.append(ms)

    def tick_done(self, ms: float) -> None:
        self.ticks += 1
        self.worst_tick_ms = max(self.worst_tick_ms, ms)
        self.record("TOTAL", ms)

    def reset(self) -> None:
        self._stages.clear()
        self.ticks = 0
        self.worst_tick_ms = 0.0

    def rows(self) -> list[tuple[str, float, float, float, int]]:
        """(stage, last, mean, worst, samples) — the order they ran in."""
        out = []
        for name, samples in self._stages.items():
            if not samples:
                continue
            out.append((name, samples[-1], sum(samples) / len(samples),
                        max(samples), len(samples)))
        return out

    def slowest(self) -> str:
        """The stage with the worst peak, which is the one to look at."""
        worst, name = 0.0, ""
        for stage, _last, _mean, peak, _n in self.rows():
            if stage != "TOTAL" and peak > worst:
                worst, name = peak, stage
        return f"{name} {worst:.0f}ms" if name else "—"

    def report(self) -> str:
        rows = self.rows()
        if not rows:
            return "No ticks measured yet."
        width = max(len(name) for name, *_ in rows)
        lines = [f"refresh loop · rolling window {self.window} ticks · "
                 f"{self.ticks} ticks total · worst tick "
                 f"{self.worst_tick_ms:.0f}ms",
                 f"{'stage'.ljust(width)}   last     mean    worst    n"]
        for name, last, mean, peak, count in rows:
            flag = "  <-- slow" if peak >= SLOW_MS and name != "TOTAL" else ""
            lines.append(f"{name.ljust(width)} {last:7.1f} {mean:7.1f} "
                         f"{peak:7.1f} {count:4d}{flag}")
        return "\n".join(lines)


# One watch for the whole loop. The provider stages its own work into the
# same object, so capture and recognition are attributed rather than hiding
# inside "poll".
LOOP = Stopwatch()
