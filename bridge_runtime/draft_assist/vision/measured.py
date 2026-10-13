"""Where the pick bar sits, measured per display shape rather than guessed.

**THIS IS THE TABLE THE OWNER ASKED FOR**: "i just run every resolution of
dota 2 - draft against bots - record via the dota draft assist app - send
back reports - refine the protrait recognition for each resolution... and
then the user of this app has their current rresdolution sensed by this app
and it knows exactly where to look fort the portraits ... o nthe first try".

What it buys is DRAFT NUMBER ONE. The app has measured itself at strategy
time for a long while (`autocal`, `_remember_measured_layout`) and now
repairs itself from a bad draft's own recording (`bugreport.repair`), so a
fresh install has been self-correcting from the second draft onwards. The
one thing neither could do is be right during the FIRST hero selection,
which is the screen the app exists for.

**A TABLE OF RESOLUTIONS WOULD HAVE A HUNDRED ROWS AND TWO ANSWERS.**
`layout.hud_box` already computes every resolution from `scale = min(w/16,
h/9)`, centred — so 1920x1080, 2560x1440 and 3840x2160 are the SAME
fractions, exactly, and measuring each of them separately measures the same
thing three times. What actually varies is the display's SHAPE, and the
measurement says it varies in one step:

    bar width / HUD span   frames
    0.780 - 0.791          1360x768, 1920x1080, 2560x1440, 3440x1440
    0.889 - 0.898          800x600, 1024x768, 1280x1024, 1440x900, 1600x1200

which is 16:9-and-wider against narrower-than-16:9. Nobody has explained
the mechanism and nothing here claims one; `GROUPS` records the step as
measured and stops there.

**AND THE NARROW SIDE IS TWO SHAPES, NOT ONE — MEASURED, AFTER A 16:10
LAPTOP READ ONE HERO OF TEN.** That step above was read off screenshots
of which four were 4:3 or 5:4 and the one 16:10 frame was the known-bad
one (it fitted the CHOOSE YOUR HERO grid, and its `y` was thrown out as
an outlier). So `TALL` was a 4:3/5:4 average wearing a band that reached
up to 16:9 — and 16:10 is the commonest laptop shape there is. Two bot
drafts, two machines, two resolutions, one shape:

    display           dire_x       y   slot_w   slot_h    pitch
    1920x1200 16:10   0.5938  0.0058   0.0682   0.0617   0.0719
    2560x1600 16:10   0.5938  0.0056   0.0676   0.0612   0.0719
    TALL (4:3, 5:4)   0.5926  0.0052   0.0692   0.0525   0.0709

The two drafts agree with EACH OTHER to 0.0006 on every fraction —
`dire_x` and `pitch` to four decimal places — and with `TALL` to within
4 PIXELS on everything except `slot_h`, where they miss it by **11 and
14 pixels**. On a matcher that reads 0.99 at the true size and 0.12 four
pixels out, that one fraction is the whole difference between reading a
draft and reading nothing: the 2560x1600 machine cropped 84px boxes over
98px portraits, resolved not one hero from the screen, and showed only
the hero GSI names for you.

That is a SHAPE with two independent measurements behind it, which is
what `GROUPS` is for, so `SIXTEEN_TEN` carries it and neither display
needs a row. 3:2 has never been measured and falls in this band rather
than `TALL` on arithmetic alone — see the band's own note.

**THE MECHANISM IS VISIBLE HERE AND IS STILL NOT ACTED ON.** `slot_h` is
the only fraction stored against a denominator that does not track the
bar. Against the HUD SPAN the three narrow readings collapse: 0.0390 at
5:4, 0.0386 at 1920x1200, 0.0382 at 2560x1600 — one number, where
against the window they run 0.0488 to 0.0617. So `slot_h ~ 0.0386 *
aspect` predicts all three to within 1%, and would predict 3:2 outright
instead of leaving it to a band edge. It is NOT shipped: this project
has moved the vertical convention once already and reverted it against
the owner's own screenshots, the change would also move 4:3, and two
distinct narrow aspects is not enough to re-cut a convention on. A
measured band costs nothing and cannot be wrong about the displays it
was measured on.

**SO THERE ARE TWO LEVELS AND THE SPECIFIC ONE WINS.** `EXACT` is keyed by
resolution and is where a bot-game measurement goes — one row per display
actually played on, pasted from `tools/measure_recording.py --row`. `GROUPS`
is the shape-level answer, which is what an untried resolution falls back
to. A table alone could say nothing about a display nobody owned; a group
alone would ignore a real reading somebody took. Having both means adding a
row can only ever make one resolution better and can never make another
worse.

**AND THE MACHINE'S OWN MEASUREMENT STILL BEATS BOTH** (`load_layout`).
`calibration_local.json` is this display, this install, measured by this app
off a real match — it is an ANSWER where everything in this file is a good
starting guess. The order is: the local file, then `EXACT`, then `GROUPS`,
then `DraftLayout()`'s own defaults.

**`radiant_x` IS DERIVED, NOT MEASURED, AND THAT IS THE STRONGER NUMBER.**
The bar is centred on the HUD span: predict the left bank's origin as the
mirror of the right one — `1 - (dire_x + 4*pitch + slot_w)` — and it lands
within 1 to 4 PIXELS of the measured origin on all seven frames that
located ten portraits. Measuring it directly is the one reading with a
known failure: `banks_from` reads a bank's origin off the FIRST portrait it
finds in that bank, so a single missed leading portrait moves it by a whole
pitch and moves nothing else. That is exactly why `radiant_x` is the
fraction that never settled in the screenshot sweep while the other five
did. `mirrored_x` is therefore what fills it, and a row states the four it
measured.
"""

from dataclasses import dataclass, replace

from .layout import DraftLayout, HUD_ASPECT


def mirrored_x(dire_x: float, slot_w: float, pitch: float) -> float:
    """The left bank's origin, from the right bank's, assuming a centred bar.

    Spelled once. `bank_span` is four pitches plus one portrait — the
    arithmetic this project has already got wrong once by writing
    `4 * slot_w + 4 * pitch`, which hung a bank off the right of the
    screen.
    """
    return 1.0 - (dire_x + 4 * pitch + slot_w)


@dataclass(frozen=True)
class Reading:
    """One measured pick bar, and where the measurement came from.

    The four fractions are the ones a located frame reads directly.
    `radiant_x` is not among them deliberately — see the module note.
    """

    dire_x: float
    y: float
    slot_w: float
    slot_h: float
    pitch: float
    source: str
    frames: int = 1

    def layout(self) -> DraftLayout:
        """A full `DraftLayout`, with the left bank mirrored in.

        `role_dy` and `role_h` keep `DraftLayout`'s own values: there is
        no role strip in a located pick bar, so nothing here has ever
        measured them and inventing a number would be worse than
        inheriting one.
        """
        return replace(
            DraftLayout(),
            radiant_x=round(mirrored_x(self.dire_x, self.slot_w, self.pitch), 4),
            dire_x=self.dire_x,
            y=self.y,
            slot_w=self.slot_w,
            slot_h=self.slot_h,
            pitch=self.pitch,
        )


@dataclass(frozen=True)
class Group:
    """One display SHAPE, and the band of aspects it answers for.

    Half-open on the low side (`low <= aspect < high`) so the groups
    partition every aspect there can be and no display falls between
    them — a gap here would be a fresh install with no answer at all,
    which is the state this file exists to end. There are three of
    them now, and a 16:10 laptop reading one hero of ten is what the
    third one cost.
    """

    label: str
    low: float
    high: float
    reading: Reading

    def covers(self, aspect: float) -> bool:
        return self.low <= aspect < self.high


# The four fractions a frame reads, per shape. Measured by
# `tools/find_portraits.py` over the owner's own screenshots, except the
# 16:9-and-wider vertical, which is the app's OWN `autocal` measurement
# off a real 3440x1440 match — two independent routes agreeing that the
# shipped y=0.0330 / slot_h=0.0930 were never measured by anything.

# **"16:9" PANELS THAT ARE NOT 16:9.** 1360x768 is 1.77083 against
# HUD_ASPECT's 1.77778, so an exact boundary put it BELOW the step - and
# it is one of the two screenshots `WIDE` itself was measured from, which
# means the group did not cover its own evidence. It was landing on the
# narrow numbers and taking `dire_x` 0.5926 against its measured 0.5708,
# a 31-pixel miss, for as long as this table has existed. The tolerance
# is a hundredth of an aspect: enough for every panel sold as 16:9
# (1360x768 is the worst offender at 0.007) and nowhere near 16:10 at
# 1.6, which is 0.18 away.
NEARLY_WIDE = HUD_ASPECT - 0.01

WIDE = Group(
    label="16:9 and wider",
    low=NEARLY_WIDE,
    high=float("inf"),
    reading=Reading(
        dire_x=0.5710,      # 0.5708, 0.5713
        y=0.0056,
        slot_w=0.0617,      # 0.0615, 0.0618
        slot_h=0.0611,
        pitch=0.0650,       # 0.0645, 0.0654
        source="1360x768 and 1920x1080 screenshots; vertical from a real "
               "3440x1440 match measured by autocal",
        frames=3,
    ),
)
SIXTEEN_TEN = Group(
    label="16:10 and 3:2",
    low=1.5,
    high=NEARLY_WIDE,
    reading=Reading(
        dire_x=0.5938,      # 0.5938 and 0.5938
        y=0.0057,           # 0.0058, 0.0056
        slot_w=0.0679,      # 0.0682, 0.0676
        slot_h=0.0615,      # 0.0617, 0.0612 - the fraction TALL got wrong
        pitch=0.0719,       # 0.0719 and 0.0719
        source="bot drafts at 1920x1200 and 2560x1600, five strategy "
               "frames each, all ten calibrated boxes landing",
        frames=10,
    ),
)
# **THE LOW EDGE IS 3:2, AND IT IS A BET RATHER THAN A READING.** Both
# measurements above are at 1.6 exactly; the only other display shape
# between 4:3 and 16:9 that anybody sells is 3:2 (1.5 — Surface and
# friends), and nothing has ever drafted on one. Which band should hold
# it is therefore arithmetic: under the span reading in the module note
# a 3:2 display wants slot_h 0.0579, which is 0.0036 from this group and
# 0.0054 from `TALL` — so it is nearer here, and here it goes. Said out
# loud because it is the one number in this file no frame produced.
TALL = Group(
    label="4:3 and 5:4",
    low=0.0,
    high=1.5,
    reading=Reading(
        dire_x=0.5926,      # 0.5914 - 0.5938
        y=0.0052,           # worst miss 0.0008 across the sweep
        slot_w=0.0692,      # 0.0674 - 0.0711
        slot_h=0.0525,
        pitch=0.0709,       # 0.0703 - 0.0715
        source="800x600, 1024x768, 1280x1024, 1440x900 and 1600x1200 "
               "screenshots (the 1440x900 one is 16:10 and was the bad "
               "fit of that sweep, so this is a 4:3/5:4 reading)",
        frames=5,
    ),
)

GROUPS: tuple[Group, ...] = (WIDE, SIXTEEN_TEN, TALL)

# One row per resolution somebody has actually drafted on, keyed by the
# frame size the app captures. A row is produced by playing a bot draft at
# that resolution and running `tools/measure_recording.py <folder> --row`,
# which prints the line to paste here. It is deliberately EMPTY rather than
# pre-filled with the group's own numbers: a row that merely repeats its
# group says a measurement was taken when none was, and the fallback below
# already covers every resolution.
EXACT: dict[tuple[int, int], Reading] = {
    # **ONE ROW, FOR ONE FRACTION: `slot_h`.** There were two, and the
    # other one is gone rather than lost — 1920x1200 was here because it
    # missed `TALL`'s `slot_h` by 0.0092, and a second 16:10 bot draft
    # (2560x1600) then measured the same thing to within 0.0005. Two
    # machines agreeing about a SHAPE is a group, not a pair of rows, so
    # both readings went into `SIXTEEN_TEN` and now serve every 16:10 and
    # 3:2 display instead of the two that were played on. That is the
    # table working as designed, and it is why no row was added for the
    # display that prompted all this.
    #
    # 5:4 stays a row: it matches `TALL` on `dire_x`, `y`, `slot_w` and
    # `pitch` within 0.0019 and reads `slot_h` 0.0488 against the group's
    # 0.0525 — the same fraction again, in the other direction, which is
    # what the module note's span reading predicts and nothing else here
    # explains.
    (1280, 1024): Reading(
        dire_x=0.5922,
        y=0.0059,
        slot_w=0.0695,
        slot_h=0.0488,      # TALL says 0.0525; six frames say this
        pitch=0.0703,
        source="bot draft at 1280x1024, six strategy frames, all ten "
               "calibrated boxes landing",
        frames=6,
    ),
}


def group_for(width: int, height: int) -> Group:
    """The measured shape this display belongs to.

    Never None: the groups partition every aspect, and a zero-height frame
    (which `hud_box` also guards against) is treated as the commonest case
    rather than as an error, since a capture that has not started yet must
    not be able to raise out of a layout lookup.
    """
    if not width or not height:
        return WIDE
    aspect = float(width) / float(height)
    for group in GROUPS:
        if group.covers(aspect):
            return group
    return WIDE


def reading_for(width: int, height: int) -> Reading:
    """This resolution's own measurement if one was taken, else its shape's."""
    exact = EXACT.get((int(width), int(height)))
    return exact if exact is not None else group_for(width, height).reading


def layout_for(width: int, height: int) -> DraftLayout:
    """Where to look for the portraits on a display of this size."""
    return reading_for(width, height).layout()


def describe(width: int, height: int) -> str:
    """One line for the diagnostic paste, naming which answer was used.

    Which of the three levels a reading came from is invisible in the six
    numbers, and it is the first thing worth knowing when the boxes are
    wrong — an exact row being wrong is a bad bot game, a group being
    wrong is a shape nobody has measured.
    """
    if not width or not height:
        return "crop boxes: no frame yet, so no resolution to match"
    exact = EXACT.get((int(width), int(height)))
    if exact is not None:
        return (f"crop boxes: {width}x{height} measured directly "
                f"({exact.frames} frame(s), {exact.source})")
    group = group_for(width, height)
    return (f"crop boxes: no row for {width}x{height}, using "
            f"\"{group.label}\" ({group.reading.frames} frame(s), "
            f"{group.reading.source})")
