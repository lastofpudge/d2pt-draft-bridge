"""Pick-slot geometry. ALL COORDINATES ARE FRACTIONS OF WINDOW WIDTH/HEIGHT,
never absolute pixels (see CLAUDE.md) — the Dota window is measured from its
handle at capture time and the layout survives resolution changes.

The draft screen's ten portraits sit in two mirrored banks of five along the
top, so the layout is parameterised by a handful of numbers instead of forty:
per-team first-slot position, slot size, and horizontal pitch. Calibration
mode nudges these parameters and saves to calibration_local.json
(gitignored), which overrides the shipped defaults.

The shipped defaults are a starting guess for a 16:9 Ranked All Pick screen
and are expected to be nudged against the user's first real frames — the
debug overlay draws the boxes so being off is visible instantly.
"""

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from ..config import CALIBRATION_FILE


HUD_ASPECT = 16 / 9


def hud_box(width: int, height: int) -> tuple[float, float]:
    """(left edge, width) of the 16:9 area Dota draws its HUD into.

    On 16:9 and narrower this is the whole window. On anything wider the
    HUD is pillarboxed to a centred 16:9 box, which is what makes a plain
    fraction-of-width wrong on an ultrawide monitor. That half is
    CONFIRMED: on a real 3440x1440 client the draft timer sits exactly at
    the centre of the middle 2560 pixels, and the pick bar is symmetric
    about it.

    **THE NARROW HALF IS SETTLED NOW, and it went the other way.**
    Horizontally there was never anything to decide — the largest 16:9
    rectangle inside a display taller than 16:9 is the full width, which
    is what the `min` returns. The VERTICAL was the open question:
    `SlotRect.to_pixels` read `y` and `h` as fractions of the WINDOW
    height, which on a 1920x1200 panel put the bar 11% lower and drew it
    11% taller than on 1920x1080.

    It is a fraction of the HUD BOX — see `SlotRect.to_pixels`, which
    carries the three pieces of evidence. The one that settles it is not
    a measurement at all but arithmetic: the pick tile is SQUARE on a
    real 3440x1440 client, and read against the window the crop box came
    out 42x56 on 800x600. A portrait does not change shape because the
    monitor did.

    The old objection — that changing the convention silently invalidates
    every saved `calibration_local.json` — has expired. There are no
    hand-set calibrations any more (the drag is gone), a measurement now
    replaces the last one rather than being refused, so a file written
    under the old reading is re-measured at the next strategy time. And
    it can only have been wrong on a display taller than 16:9, where it
    was wrong anyway.

    Settle it with a real 16:10 frame, not with reasoning — and it has
    been, by `tools/find_portraits.py` run over a folder of the user's own
    screenshots (`find_portraits._vertical`; there is no menu item for it
    any more, since it was an instrument rather than a feature). It weighs
    THREE candidates, not two:
    this function's reading, a 16:9 HUD box hung at the TOP of a taller
    display, and one CENTRED. The first two differ by 4px on 1920x1200
    and the third by 56px, so only the third would actually miss the
    portraits. Only a display TALLER than 16:9 can tell them apart — at
    16:9 and wider the vertical slack is nought and all three are the
    same number — which is why the confirmed 3440x1440 measurement above
    says nothing whatever about this.

    THE CENTRED MODEL IS REFUTED, measured rather than argued. Over the
    user's own screenshots the bar's top read 0.0019 spread as a fraction
    of the WINDOW, 0.0031 against a top-hung HUD box, and **-0.2028 to
    -0.0491 against a centred one** — negative, on every frame: it puts
    the pick bar above the top of the box it claims Dota draws it in. That
    was the only one of the three that would have missed the portraits.
    The two survivors differ by 4px on 1920x1200 and are still not
    separated, which costs nothing.
    """
    if not width or not height:
        return 0.0, float(width)
    span = min(float(width), height * HUD_ASPECT)
    return (width - span) / 2.0, span


@dataclass
class SlotRect:
    team: str        # "radiant" | "dire"
    slot: int        # 0..4 within the team
    x: float         # all fractional [0..1]
    y: float
    w: float
    h: float

    def to_pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        """Fractions to pixels: HORIZONTAL against Dota's 16:9 HUD box.

        Dota lays its HUD out in a 16:9 area centred horizontally, and on a
        wider display it pillarboxes that area rather than stretching it —
        so on 3440x1440 the ten portraits occupy the middle 2560 pixels and
        a fraction of the FULL width lands hundreds of pixels off.

        **THE VERTICAL IS THE WINDOW'S HEIGHT, AND AN ATTEMPT TO MAKE IT
        THE HUD BOX WAS REVERTED AGAINST REAL ARTWORK.** That change read
        `y` and `h` as fractions of the 16:9 box, on three arguments: a
        measurement that the portrait HEIGHT sits tighter against the box
        (0.0044 against 0.0137), an arithmetic claim that the pick tile is
        square, and a test that passed at every resolution.

        **The test was circular and is the reason this shipped.** It drew
        its own pick bar at `round(layout.y * box_h)` and then asserted the
        app could read it — the convention under test used to place the
        thing being tested, so it could not fail. A synthetic bar is
        evidence about our own arithmetic and nothing else.

        The real sheet, over the user's own 22 screenshots, is the
        evidence: under the WINDOW the boxes land on the portraits at 20
        of 22 resolutions, and under the HUD box they land on the player
        NAME strip at all of them. Twenty working traded for two.

        What the measurement actually said is narrower than what was done
        with it, and it says so itself: the portrait HEIGHT is tighter
        against the HUD box, while the bar's TOP is UNDECIDED and if
        anything favours the window (0.0019 against 0.0031). `y` was never
        covered by it. The two open failures — 800x600 and 1440x900 — are
        still open, and are not a licence to move the eighteen that work.
        """
        left, span = hud_box(width, height)
        # HORIZONTAL against the HUD box, which IS settled — 3440x1440
        # put the portraits in the middle 2560 pixels and a fraction of
        # the full width landed 440px off. VERTICAL against the window,
        # which is what the real screenshots say.
        return (round(left + self.x * span), round(self.y * height),
                round(self.w * span), round(self.h * height))


@dataclass
class DraftLayout:
    # Radiant bank (left): first portrait's top-left, size, and pitch.
    radiant_x: float = 0.0575
    dire_x: float = 0.6250      # dire bank (right) mirrors radiant
    y: float = 0.0330
    slot_w: float = 0.0525
    slot_h: float = 0.0930
    pitch: float = 0.0640       # horizontal step between slots in a bank
    # Role icon strip relative to each slot (fraction of window, offset from
    # the slot's top-left). Ranked role queue shows assigned roles here.
    role_dy: float = 0.0960
    role_h: float = 0.0180

    def bank_span(self) -> float:
        """Left edge of a bank's first portrait to the right edge of its
        fifth, as a fraction of the HUD box.

        FOUR pitches plus ONE portrait, which is the arithmetic that has
        already been got wrong once: `4 * slot_w + 4 * pitch` hung the Dire
        calibration box a hundred pixels off the right of the screen, where
        it could not be dragged at all. Spelled once so nobody has to
        rederive it.
        """
        return 4 * self.pitch + self.slot_w

    def slots(self) -> list[SlotRect]:
        out = []
        for team, x0 in (("radiant", self.radiant_x), ("dire", self.dire_x)):
            for i in range(5):
                out.append(SlotRect(team, i, x0 + i * self.pitch,
                                    self.y, self.slot_w, self.slot_h))
        return out

    def role_rect(self, slot: SlotRect) -> SlotRect:
        return SlotRect(slot.team, slot.slot, slot.x,
                        slot.y + self.role_dy, slot.w, self.role_h)


# How far the two bank origins may be from mirroring each other before
# the calibration is refused, as a fraction of the HUD span. The pick bar
# is CENTRED on that span, so the left origin is the mirror of the right
# to within 1 to 4 PIXELS on every frame that has ever located all ten.
# IT IS BOUNDED FROM BOTH SIDES, and the lower bound was a surprise.
#
#   must CATCH   a bank one portrait out ........ 0.0645 (the smallest
#                                                 pitch any group uses)
#   must PASS    `DraftLayout()`'s own defaults .. 0.0285 out of mirror
#
# Those shipped six predate `measured.py` and are deliberately untouched
# - they are only ever used when there is no frame to measure against,
# since every path that HAS one goes through the table - but they are
# not mirror-consistent, and refusing them would refuse the app's own
# starting point. 0.04 sits between the two with room either side.
#
# A real measurement is nowhere near either bound: the mirror lands
# within 1 to 4 PIXELS on every frame that has ever located all ten,
# which at a 2560px HUD span is 0.0016.
MIRROR_TOLERANCE = 0.04


def why_impossible(layout: "DraftLayout") -> str:
    """Why these boxes cannot be a real pick bar, or "" if they could.

    TWO CHECKS, AND THE FIRST IS UNARGUABLE: the boxes have to be inside
    the HUD box. A calibration whose last Dire portrait starts past the
    right-hand edge describes a bar that is not on the screen.

    The second catches the error that stays on screen. `autocal` reads a
    bank's origin off the first portrait it locates in it, so a missed
    leading portrait shifts that bank by a whole pitch and leaves every
    other fraction correct - and the result still fits inside the box, so
    nothing refuses it.

    MEASURED, from a real report: a 3440x1440 machine was reading with
    `dire_x` 0.6996 against a shipped 0.5710 - out by 329px, which is
    1.99 pitches - while its other five fractions matched the shipped
    table to within four pixels. Its Dire bank ran to 1.018 of the HUD
    box, 46px past the edge of the screen area, and its mirror came out
    NEGATIVE. The app used it for a whole draft and then reported that it
    could not find the portraits.
    """
    right_edge = layout.dire_x + 4 * layout.pitch + layout.slot_w
    if layout.radiant_x < 0 or layout.dire_x < 0:
        return "a bank starts left of the HUD box"
    if right_edge > 1.0:
        return (f"the Dire bank runs to {right_edge:.3f} of the HUD box, "
                "which is off the right-hand edge of the screen")
    if layout.y < 0 or layout.y + layout.slot_h > 1.0:
        return "the bar is outside the frame vertically"
    if layout.slot_w <= 0 or layout.slot_h <= 0 or layout.pitch <= 0:
        return "a portrait has no size"
    mirrored = 1.0 - right_edge
    if abs(mirrored - layout.radiant_x) > MIRROR_TOLERANCE:
        return (f"the two banks do not mirror: the Radiant origin is "
                f"{layout.radiant_x:.4f} where the bar being centred puts "
                f"it at {mirrored:.4f}, which is "
                f"{abs(mirrored - layout.radiant_x) / max(layout.pitch, 1e-6):.1f} "
                "portrait pitches out")
    return ""


def calibration_in_use(calibration_file: Path | None = None) -> bool:
    """Is there a calibration this machine will actually USE?

    NOT the same question as "does the file exist", which is what the
    capture session used to latch on. An impossible file is ignored by
    `load_layout`, so latching on its existence would pin the session to
    `DraftLayout()`'s sizeless defaults and stop `fit_to_frame` ever
    keying the shipped table on the real frame - leaving the display
    worse off than if the file had never been written.
    """
    path = calibration_file or CALIBRATION_FILE
    if not path.exists():
        return False
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        candidate = DraftLayout()
        for k, v in saved.items():
            if hasattr(candidate, k):
                setattr(candidate, k, float(v))
    except (OSError, ValueError, TypeError):
        return False
    return not why_impossible(candidate)


def load_layout(width: int = 0, height: int = 0,
                calibration_file: Path | None = None) -> DraftLayout:
    """Where to look for the portraits, most specific answer first.

    Same rule as `save_calibration`: the path is resolved at CALL time,
    never bound as a default argument.

    THREE LEVELS, AND THE MACHINE'S OWN MEASUREMENT WINS. A
    `calibration_local.json` was measured by this app, on this display,
    off a real match — it is an ANSWER, where a shipped number is a good
    starting guess, so it is taken whole and nothing else is consulted.
    Failing that, `measured.layout_for` answers for the SHAPE of the
    display (see that module: a resolution measured on a bot draft first,
    then the aspect group it belongs to). Failing even a frame to measure,
    `DraftLayout()`'s own defaults stand.

    **THE SIZE IS THE FRAME'S, NOT THE MONITOR'S.** Dota windowed at
    1280x720 on a 4K panel draws a 16:9 HUD, and what the app captures is
    the window. Passing nothing keeps the old behaviour exactly, which is
    what every tool and test that has no frame in hand wants.
    """
    from .measured import layout_for

    calibration_file = calibration_file or CALIBRATION_FILE
    layout = layout_for(width, height) if (width and height) else DraftLayout()
    if calibration_file.exists():
        overrides = json.loads(calibration_file.read_text(encoding="utf-8"))
        known = set(asdict(layout))
        bad = set(overrides) - known
        if bad:
            raise ValueError(f"{calibration_file} has unknown keys {sorted(bad)}; "
                             f"valid keys: {sorted(known)}")
        candidate = replace(layout)
        for k, v in overrides.items():
            setattr(candidate, k, float(v))
        # AND A SAVED MEASUREMENT CAN BE WRONG, which this used to have no
        # way of saying. The local file outranks everything and
        # `CaptureSession.layout_is_measured` latches on its mere
        # EXISTENCE, so a bad one is permanent: the boxes never land, so
        # `bugreport.repair` can never locate ten portraits, so it can
        # never write a better one. A real machine sat in that loop for a
        # whole draft and reported that the app could not find the
        # portraits. An impossible file is ignored, which puts the display
        # straight back on the shipped table it should have been using.
        broken = why_impossible(candidate)
        if broken:
            return layout
        layout = candidate
    return layout


def save_calibration(layout: DraftLayout,
                     calibration_file: Path | None = None) -> None:
    """The destination is resolved at CALL time, never bound as a default.

    A default argument is evaluated once at import, so a test that repoints
    `CALIBRATION_FILE` still wrote into the real repository — which is how
    a test run left a stray calibration_local.json behind and broke an
    unrelated test on the next run. Same rule as `ui_settings.load`.
    """
    broken = why_impossible(layout)
    if broken:
        # REFUSED RATHER THAN WRITTEN. What this saves is an ANSWER that
        # outranks the shipped table for ever after, so writing one that
        # cannot describe a pick bar is the expensive mistake - and the
        # app cannot measure its way out of it afterwards, because the
        # boxes have to land before a better measurement can be taken.
        raise ValueError(f"refusing to save these crop boxes: {broken}")
    path = calibration_file or CALIBRATION_FILE
    path.write_text(json.dumps(asdict(layout), indent=2), encoding="utf-8")
