"""Live capture session: binds the Dota window via Windows Graphics Capture
and runs the idle/active state machine.

- IDLE: the gate checks a downscaled frame at ~1 Hz.
- ACTIVE: full slot recognition at ~2 Hz while the gate keeps tripping (with
  hysteresis so one noisy frame doesn't flap the state).
- Manual override forces ACTIVE regardless of the gate.

The session is UI-framework agnostic: the owner (the PyQt app, or a test)
calls tick() on its own timer; the capture callback thread only stores the
newest frame. Recognition confirming a draft screen auto-saves a gate
reference, which is how the gate bootstraps from zero references.

Windows-only imports happen inside start(), so every other module imports
this file fine on Linux.
"""

import threading
import time
from dataclasses import dataclass

import numpy as np

from ..timing import LOOP
from ..vision.layout import DraftLayout
from ..vision.library import Library, RecognitionParams
from ..vision.recognize import DraftRead, SlotRead, read_draft
from . import gate

IDLE_PERIOD = 1.0      # gate cadence
ACTIVE_PERIOD = 0.5    # recognition cadence
# Hysteresis: consecutive gate results needed to switch state.
TRIPS_TO_ACTIVATE = 2
MISSES_TO_DEACTIVATE = 4
# Recognition confirming this many resolved slots marks the frame as a real
# draft screen (feeds gate references).
CONFIRM_SLOTS = 6
STALL_AFTER = 5.0      # no frames for this long -> "stalled" flag
# THE PICTURE HAS NOT CHANGED FOR THIS LONG -> "frozen". A DIFFERENT
# QUESTION FROM `stalled`, which watches whether a frame ARRIVED: a
# capture that goes on delivering the SAME pixels for ever looks
# perfectly healthy to that check and is completely dead.
#
# Measured, from a real report: 579 ticks, every one with a frame,
# recognition run on 386 of them, `read_heroes` 0 on all 579 - and the
# four frames the report carried, spread over three minutes of a draft,
# were BYTE-IDENTICAL to each other and were a picture of the Dota MAIN
# MENU. The app then blamed its own crop boxes, which were on exactly
# the right part of a picture that was never going to contain a pick
# bar.
#
# Ten seconds because a live Dota screen is never pixel-identical for
# that long - the clock alone redraws every second - while an alt-tabbed
# or minimised client legitimately is, and that is a true answer rather
# than a false alarm: the capture is not showing the draft either way.
FROZEN_AFTER = 10.0
# While the game feed is SILENT, recognition runs this often even when the
# gate says no, because a gate that never runs recognition never learns the
# screen it is wrong about. See `_should_probe`.
PROBE_PERIOD = 3.0
# Confident distinct heroes in a probe's read that promote it to active.
# Two portraits matching the library inside the calibrated boxes, each
# under the distance ceiling and over the margin floor, is not something a
# menu produces.
PROBE_SLOTS = 2
# A reading survives this long of continuous idle before it is forgotten.
# Four missed gate checks is four seconds, and a pick does not un-pick in
# four seconds; thirty seconds of not-the-draft-screen is another game.
FORGET_AFTER = 30.0


@dataclass
class SessionState:
    mode: str = "idle"              # idle | active
    forced: bool = False            # manual override
    # The GAME says a draft is happening. The gate is a guess about pixels;
    # this is Dota's own answer, so it outranks the guess. THREE states,
    # not two: True is "a draft is on", False is "the game says it is not",
    # and None is "the game is not saying" — which is not the same as no,
    # and treating it as no is what left the app blind for a whole draft
    # with GSI switched off.
    required: bool | None = None
    gate_score: float = float("inf")
    frames_arrived: int = 0
    stalled: bool = False
    # How long the captured PICTURE has been identical. `stalled` above
    # answers "did a frame arrive"; this answers "was it a new one".
    frozen_for: float = 0.0
    last_read: DraftRead | None = None       # STABILISED — what consumers use
    last_read_raw: DraftRead | None = None   # per-frame — what debug shows
    last_frame: np.ndarray | None = None


# Consecutive identical reads required before a slot's published value
# changes. At the 2 Hz active cadence this is ~1.5 s of latency against a
# ~30 s pick timer — cheap insurance against flicker.
STABLE_CONFIRMS = 3


class SlotStabilizer:
    """Recognition can flicker frame-to-frame (hover overlays, animations,
    a wrong window being captured). Published slot values are therefore
    debounced: a slot changes only after the same NEW value is seen
    STABLE_CONFIRMS times in a row, and unknown never overwrites a resolved
    value — picks don't un-pick during a draft. Reset when the draft screen
    goes away."""

    def __init__(self, n_slots: int = 10, confirms: int = STABLE_CONFIRMS):
        self.confirms = confirms
        self.stable: list[int | None] = [None] * n_slots
        self._candidate: list[int | None] = [None] * n_slots
        self._count = [0] * n_slots

    def reset(self) -> None:
        n = len(self.stable)
        self.stable = [None] * n
        self._candidate = [None] * n
        self._count = [0] * n

    def update(self, read: DraftRead) -> DraftRead:
        out = []
        for i, s in enumerate(read.slots):
            v = s.hero_id
            if v is not None and v != self.stable[i]:
                if v == self._candidate[i]:
                    self._count[i] += 1
                else:
                    self._candidate[i], self._count[i] = v, 1
                if self._count[i] >= self.confirms:
                    self.stable[i] = v
                    self._candidate[i], self._count[i] = None, 0
            elif v == self.stable[i]:
                self._candidate[i], self._count[i] = None, 0
            out.append(SlotRead(rect=s.rect, hero_id=self.stable[i],
                                best_label=s.best_label, distance=s.distance,
                                margin=s.margin, crop=s.crop))
        return DraftRead(slots=out)


class CaptureSession:
    def __init__(self, layout: DraftLayout, lib: Library,
                 params: RecognitionParams):
        self.layout, self.lib, self.params = layout, lib, params
        # The size the layout in hand was chosen for, so a display that
        # changes shape mid-session (a windowed Dota being dragged onto a
        # second monitor, or the resolution changed between matches) is
        # re-answered rather than read through the last screen's boxes.
        # See `fit_to_frame`.
        self._sized_for: tuple[int, int] = (0, 0)
        # THE CAPTURED BUFFER IS NOT ALWAYS THE CLIENT AREA - see
        # `_crop_to_client`. Measured once when the window is bound and
        # again whenever the raw frame size changes; None means "never
        # measured", which is not the same as "no padding" and is why
        # this is three-valued rather than a (0, 0).
        self._client: tuple[int, int] | None = None
        self._raw_size: tuple[int, int] = (0, 0)
        # A layout somebody MEASURED — this machine's own calibration file,
        # or an `autocal` reading adopted mid-match — outranks the shipped
        # table and must never be silently replaced by it.
        self.layout_is_measured = False
        self.state = SessionState()
        self._lock = threading.Lock()
        self._latest: np.ndarray | None = None
        self._arrived_at = 0.0
        self._count = 0
        # The last picture's fingerprint, and when it last CHANGED. See
        # FROZEN_AFTER: a capture can go on delivering the same pixels
        # for ever and look perfectly healthy to `stalled`.
        self._fingerprint: int | None = None
        self._changed_at = 0.0
        self._trips = 0
        self._misses = 0
        self._next_tick = 0.0
        self._next_probe = 0.0
        # One-shot "read the board now" (see `detect_now`).
        self._detect_now = False
        self._idle_since = 0.0
        # gate.GATE_DIR is read at call time (not bound as a default) so
        # tests can repoint it.
        self._refs = gate.load_references(gate.GATE_DIR)
        self._control = None
        self._stabilizer = SlotStabilizer()
        self.capture_title: str | None = None

    # -- capture binding (Windows only) --------------------------------
    def start(self, title: str | None = None) -> str:
        """Bind capture to `title`, or to the Dota client when omitted.

        Raises RuntimeError naming the visible windows if nothing matches —
        the caller (the UI) offers a picker rather than dying, because
        "which window am I capturing" is the question behind most apparent
        recognition failures.
        """
        from windows_capture import WindowsCapture

        from .window import find_dota_window_title, list_window_titles
        if title is None:
            title = find_dota_window_title()
        if title is None:
            visible = list_window_titles()
            raise RuntimeError(
                "No window titled exactly 'Dota 2' is open — start Dota in "
                "borderless windowed mode, or pick a capture source in the "
                "app's Debug tab.\nVisible windows:\n  "
                + "\n  ".join(visible[:40] or ["(none)"]))
        self.stop()
        self._stabilizer.reset()
        with self._lock:
            self._latest = None
            self._count = 0
        capture = WindowsCapture(cursor_capture=False, draw_border=False,
                                 window_name=title)

        @capture.event
        def on_frame_arrived(frame, capture_control):  # noqa: ANN001
            with self._lock:
                self._latest = frame.frame_buffer.copy()
                self._arrived_at = time.monotonic()
                self._count += 1

        @capture.event
        def on_closed():
            pass

        self._control = capture.start_free_threaded()
        self.capture_title = title
        return title

    def stop(self) -> None:
        if self._control is not None:
            self._control.stop()
            self._control = None

    # -- for replay/tests: inject frames instead of start() -------------
    def inject_frame(self, frame_bgr: np.ndarray) -> None:
        with self._lock:
            self._latest = frame_bgr
            self._arrived_at = time.monotonic()
            self._count += 1

    def set_required(self, required: bool | None) -> None:
        """Recognise regardless of the gate, because the GAME said so.

        The gate is an economiser: a cheap pixel comparison that decides
        whether this looks like a draft screen, so the expensive path is
        skipped in the menus. It is a guess, and a guess with references
        harvested from whichever screen happened to confirm first — one
        real session sat at 0.707 against a 0.50 threshold for the whole of
        hero selection and recognised nothing until the picks were over.

        GSI reports `HERO_SELECTION` outright. That is Dota's own answer to
        the question the gate is guessing at, so when it is available the
        guess does not get a vote. The gate still earns its keep whenever
        the game feed is off or silent.

        NONE means the feed said nothing at all, which is not the same as
        it saying no: with GSI dead the gate is the only vote, and a gate
        that is wrong stays wrong because it is the thing deciding whether
        the recognition that would correct it ever runs. So silence buys a
        probe (see `_should_probe`) and a flat no does not.
        """
        self.state.required = None if required is None else bool(required)

    def set_forced(self, forced: bool) -> None:
        self.state.forced = forced

    # How far the captured buffer may exceed the client area before the
    # difference stops looking like window chrome and starts looking like
    # the wrong window. Windows' own frame is a few pixels of invisible
    # resize border plus a title bar, so the gap measured on three real
    # displays was (9, 32) every time; a hundred is room for a scaled
    # display without being room for a mistake.
    CHROME_MAX = 100

    def _crop_to_client(self, frame):
        """The client area of the Dota window, out of the captured buffer.

        **THE BUFFER IS THE WINDOW, NOT THE CLIENT, AND EVERY FRACTION IN
        THIS APP IS A SHARE OF THE CLIENT.** Measured on three displays
        from three real recordings, the captured frame came back exactly
        NINE pixels wider and THIRTY-TWO taller than the resolution Dota
        was running at:

            1366x768   captured 1375x800
            1920x1080  captured 1929x1112
            3440x1440  captured 3449x1472

        The content sits at the TOP-LEFT of that buffer - the pick bar
        measured 4 to 8 pixels down in every one, so there is no title
        bar above it - and the extra rows and columns are padding on the
        right and bottom.

        Two things go wrong when they are counted. Every fraction is
        divided by a number 2% too big, so `slot_h` read 0.0587 on one
        display and 0.0598 on another where both are really 0.0611. And
        worse, the PADDING CHANGES THE ASPECT: 1366x768 is 1.779 and
        lands in the 16:9 group, while 1375x800 is 1.719 and lands in
        the narrower one, so the crop boxes came from the wrong row of
        the table entirely. That is a wrong geometry on a fresh install,
        which is the fault the table exists to prevent.

        `capture.window.client_size` has existed, been tested and been
        called by nothing this whole time; this is its caller.

        Cropping is REFUSED rather than guessed at when the client size
        is unknown (not Windows, window gone), not smaller than the
        buffer, or smaller by more than `CHROME_MAX` - the last because a
        client far smaller than the frame means we are not looking at
        what we think we are, and cropping to it would throw away most of
        the picture.
        """
        shape = getattr(frame, "shape", None)
        if not shape or len(shape) < 2:
            return frame
        height, width = int(shape[0]), int(shape[1])
        if (width, height) != self._raw_size:
            # The window was resized, or this is the first frame. Asking
            # Windows is two calls and happens about once a session.
            self._raw_size = (width, height)
            self._client = self._measure_client()
        if not self._client:
            return frame
        want_w, want_h = self._client
        if not (0 < want_w <= width and 0 < want_h <= height):
            return frame
        if width - want_w > self.CHROME_MAX or height - want_h > self.CHROME_MAX:
            return frame
        if (want_w, want_h) == (width, height):
            return frame
        return frame[:want_h, :want_w]

    def _measure_client(self) -> tuple[int, int] | None:
        """The bound window's client size, or None. Never fatal: a frame
        we cannot measure is better than no frame at all."""
        title = self.capture_title
        if not title:
            return None
        try:
            from .window import client_size
            return client_size(title)
        except Exception:
            return None

    def fit_to_frame(self, frame) -> bool:
        """Point the crop boxes at a display of THIS shape. True if moved.

        The shipped table is keyed by the captured frame's size (see
        `vision/measured.py`), and nothing knows that size until a frame
        arrives — the session is built at startup, long before Dota is
        bound. So the answer is resolved on the first frame and again
        whenever the size changes, which costs one dict lookup per tick
        and is what makes a fresh install right during its FIRST hero
        selection rather than after `autocal` has seen a strategy screen.

        **A MEASURED LAYOUT IS NEVER OVERWRITTEN BY A GUESS.** This
        machine's own `calibration_local.json`, and an `autocal` reading
        adopted from the match in progress, are answers; everything in
        the table is a good starting guess. `layout_is_measured` is what
        separates them, and without it the first tick after a successful
        measurement would throw it away.
        """
        if self.layout_is_measured or frame is None:
            return False
        shape = getattr(frame, "shape", None)
        if not shape or len(shape) < 2:
            return False
        height, width = int(shape[0]), int(shape[1])
        if not width or not height or (width, height) == self._sized_for:
            return False
        from ..vision.measured import layout_for

        self._sized_for = (width, height)
        fitted = layout_for(width, height)
        if fitted == self.layout:
            return False
        self.layout = fitted
        # The boxes have moved, so every reading taken through the old
        # ones is about a different set of pixels. Keeping them would let
        # the stabiliser outvote the first frames read through the right
        # geometry, which is the fault `detect_now` exists to clear.
        self._stabilizer.reset()
        self.state.last_read = None
        self.state.last_read_raw = None
        return True

    def adopt_measured(self, layout: DraftLayout) -> None:
        """Take a layout something MEASURED, and stop fitting from the table."""
        self.layout = layout
        self.layout_is_measured = True
        self._stabilizer.reset()
        self.state.last_read = None
        self.state.last_read_raw = None

    def detect_now(self) -> None:
        """Read the screen on the NEXT tick, whatever the gate thinks.

        A ONE-SHOT, not a mode: `set_forced` is a switch the user leaves
        on, and "re-read the board now" is a thing you press once. It also
        throws away what is remembered first, because the point of asking
        is that the board on screen and the board in the app disagree —
        keeping the stabiliser's memory would let the old answer win the
        vote against the new frame for another few ticks.
        """
        self.forget_reading()
        self._detect_now = True

    def forget_reading(self) -> None:
        """Throw away the picks read off the screen, keeping the session.

        Two callers, and they mean the same thing by it: Detect all, where
        the user is saying the board and the screen disagree, and a NEW
        MATCH, where the board belongs to a game that is over.

        The thirty-second timer below says in its own comment what it is
        for - "thirty seconds of not-the-draft-screen is a different
        game" - and a match id is that answer outright, with no wait. It
        had to wait, and a real session opened on the previous match's ten
        heroes for the first nine seconds of a new draft.
        """
        self.state.last_read = None
        self.state.last_read_raw = None
        self._stabilizer.reset()

    def _consume_detect_now(self) -> bool:
        asked, self._detect_now = self._detect_now, False
        return asked

    def _should_probe(self, now: float) -> bool:
        """Run recognition against the gate's advice, occasionally.

        The gate is a self-fulfilling prophecy: its references are
        harvested from frames recognition confirmed, so a gate that does
        not recognise hero selection never gets a hero-selection reference
        and goes on not recognising it. One real ranked game read four
        heroes in the first two seconds, missed four gate checks, went idle
        and never looked again for the remaining twenty — with the draft
        still on screen.

        A probe is one recognition every `PROBE_PERIOD`, and it is the way
        out of that loop: it costs ten small correlations, it publishes
        what it finds, and a confirmed frame saves the gate reference that
        stops it being needed. It runs ONLY while the game feed is silent
        (`required is None`) — when the game says a draft is on there is
        nothing to probe for, and when it says one is not, that is an
        answer and the app should believe it.
        """
        return (self.state.required is None
                and self.state.mode == "idle"
                and not self.state.forced
                and now >= self._next_probe)

    # -- state machine ---------------------------------------------------
    def tick(self) -> SessionState:
        """Call frequently (e.g. every 250 ms); internally rate-limits to the
        current mode's cadence. Mutates and returns self.state."""
        now = time.monotonic()
        with self._lock:
            frame = self._latest
            self.state.frames_arrived = self._count
            arrived_at = self._arrived_at
        self.state.stalled = bool(frame is not None
                                  and now - arrived_at > STALL_AFTER)
        if frame is None or now < self._next_tick:
            return self.state

        if frame.ndim == 3 and frame.shape[2] == 4:
            frame = frame[:, :, :3]
        # BEFORE anything reads its size. `fit_to_frame` keys the shipped
        # table on it, `autocal` measures fractions against it and the
        # recorder saves it - so a padded buffer reaching any of them is
        # the wrong denominator everywhere at once.
        frame = self._crop_to_client(frame)

        # Always publish the frame, even while idle. It used to be set only
        # when recognition ran, so a session that never tripped the gate
        # reported `frame=none` and the debug view had nothing to show —
        # exactly when a picture is what you need.
        self.state.last_frame = frame

        # IS IT A NEW PICTURE? Decimating by slicing is a VIEW and costs
        # nothing, and a 31-step stride over a 3440x1440 frame is about
        # 15 KB to hash - far too coarse to miss a screen change and far
        # too cheap to matter four times a second. The same trick, and
        # the same reason, as `gate.signature`.
        mark = hash(frame[::31, ::31].tobytes())
        if mark != self._fingerprint or not self._changed_at:
            self._fingerprint, self._changed_at = mark, now
        self.state.frozen_for = now - self._changed_at

        active = (self.state.forced or self.state.required
                  or self.state.mode == "active")
        self._next_tick = now + (ACTIVE_PERIOD if active else IDLE_PERIOD)

        with LOOP.stage("  gate (is this the draft screen)"):
            # ONE signature per tick. `is_draft_screen` computes the whole
            # thing again, so asking both questions cost twice what the
            # answer is worth — and the answer is a comparison, not a
            # second measurement.
            self.state.gate_score = gate.score(frame, self._refs)
            tripped = self.state.gate_score <= gate.DEFAULT_THRESHOLD
        if tripped:
            self._trips, self._misses = self._trips + 1, 0
        else:
            self._trips, self._misses = 0, self._misses + 1
        if self.state.mode == "idle" and self._trips >= TRIPS_TO_ACTIVATE:
            self.state.mode = "active"
            self._idle_since = 0.0
        elif (self.state.mode == "active"
              and self._misses >= MISSES_TO_DEACTIVATE
              and not self.state.forced):
            # Drop to the cheap cadence, but KEEP the reading. Wiping it
            # here meant four missed gate checks — four seconds — deleted
            # every pick the app had read, which is what "it found four
            # heroes and then showed none" was. A pick does not un-pick.
            self.state.mode = "idle"
            self._idle_since = now
        if (self.state.mode == "idle" and self._idle_since
                and now - self._idle_since > FORGET_AFTER
                and self.state.last_read is not None):
            # Thirty seconds of not-the-draft-screen is a different game,
            # so at that point it IS stale and goes.
            self.state.last_read = None
            self.state.last_read_raw = None
            self._stabilizer.reset()

        # Re-asked AFTER the state machine, never reusing `active` from
        # before it: the mode may have just flipped to idle, and the tick
        # that deactivates must not also read. `active` above is only the
        # cadence for the NEXT tick.
        probing = self._should_probe(now)
        if probing:
            self._next_probe = now + PROBE_PERIOD
        asked = self._consume_detect_now()
        if (self.state.mode == "active" or self.state.forced
                or self.state.required or probing or asked):
            self.fit_to_frame(frame)
            with LOOP.stage("  recognise (10 crops vs the library)"):
                raw = read_draft(frame, self.layout, self.lib, self.params)
            self.state.last_read_raw = raw
            self.state.last_read = self._stabilizer.update(raw)
            resolved = 10 - raw.unknown_count()
            if probing and resolved >= PROBE_SLOTS:
                # The gate said no and the library disagreed with it, in
                # the calibrated boxes, with confident margins. Believe the
                # library: it is looking at the actual portraits.
                self.state.mode = "active"
                self._trips, self._misses = TRIPS_TO_ACTIVATE, 0
                self._idle_since = 0.0
            # A frame the recogniser itself resolves is the draft screen;
            # judge that on the raw read so gate bootstrap isn't delayed.
            if resolved >= CONFIRM_SLOTS:
                with LOOP.stage("  save gate reference"):
                    if gate.save_reference(frame, gate.GATE_DIR) is not None:
                        self._refs = gate.load_references(gate.GATE_DIR)
        return self.state
