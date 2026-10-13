"""Measure where the portraits are, using heroes the game has named.

Calibrating by hand needs someone looking at the screen, and this side of
the project cannot: the crop boxes were hundreds of pixels off on an
ultrawide for days with no way to see it. But at strategy time the app
holds both halves of the answer at once — a captured frame, and the ten
heroes the minimap named in it — so the geometry can be measured instead.

Each known hero's portrait is searched for in the top strip by normalised
cross-correlation. Scale is unknown, so one hero is used to find it (over a
grid of widths and aspect ratios) and the rest are then searched at that
size. Ten rectangles give the bank starts, the pitch, the size and the top
edge directly, with no assumption about resolution or HUD scaling.

It never invents a layout: too few confident hits, or a strip that does not
fall into two banks, and it returns nothing with a reason.
"""

from dataclasses import dataclass, field

import cv2
import numpy as np

from .layout import DraftLayout, hud_box

# How far down the frame the pick bar can reach, as a fraction of its
# height. It hugs the top edge — measured at 6.0% on a real 3440x1440
# client, so this is twice what the bar takes and covers a HUD scaled well
# past anything the slider offers.
#
# It is deliberately a fraction of the WINDOW rather than of the HUD box,
# which is the one place that is safe: the bar's height in pixels is set by
# the HUD's own scale, so on a display NARROWER than 16:9 the bar takes a
# SMALLER share of the (taller) window, never a larger one. A bound that is
# right on 16:9 is therefore loose on 16:10 and 5:4, which is the direction
# a bound wants to be wrong in.
BAR_FRACTION = 0.15
# Width as a fraction of the HUD box, height as a fraction of the frame.
# Both are searched, because the portrait's aspect ON SCREEN is not the
# aspect of the stored image — Dota stretches it into its own box, and
# matching is sharply scale-sensitive: at the true size a portrait scores
# 0.99 and four pixels out it scores 0.12. A grid this coarse only finds
# the neighbourhood; _refine walks in from there a pixel at a time.
WIDTHS = tuple(round(0.028 + 0.003 * i, 4) for i in range(19))
HEIGHTS = tuple(round(0.050 + 0.006 * i, 4) for i in range(17))
# `locate` needs a strip TALLER THAN THE TALLEST TEMPLATE IT SEARCHES, and
# that is not the same number as where the bar ends. Cutting this to the
# bar's own 0.15 left the 0.146 template with four rows to slide in — a
# correlation that can only match at the very top of the frame, which is a
# search in name only. Derived from the grid rather than typed, so
# narrowing one can never quietly starve the other.
TOP_FRACTION = max(BAR_FRACTION, max(HEIGHTS) * 1.5)
MIN_SCORE = 0.35
MIN_FOUND = 8
# How much of `locate` is spent in `find_scale` - measured at roughly
# two thirds, and only used to make a progress bar honest.
SCALE_SHARE = 0.65
# The coarse scale grid runs on a strip decimated to about this width. The
# grid only has to find the NEIGHBOURHOOD; `_refine` walks in from there at
# full resolution, so nothing is lost and the search stops being measured
# in tens of seconds.
SEARCH_WIDTH = 900
TEAM_SIZE = 5


@dataclass
class Located:
    hero_id: int
    x: int
    y: int
    w: int
    h: int
    score: float


@dataclass
class Calibration:
    layout: DraftLayout | None = None
    found: list = field(default_factory=list)
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.layout is not None


def _grey(image):
    return (cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3
            else image)


def _best_at(strip, template, width, height):
    if (width < 8 or height < 8 or height >= strip.shape[0]
            or width >= strip.shape[1]):
        return None
    resized = cv2.resize(template, (width, height),
                         interpolation=cv2.INTER_AREA)
    result = cv2.matchTemplate(strip, resized, cv2.TM_CCOEFF_NORMED)
    _mn, score, _ml, location = cv2.minMaxLoc(result)
    return float(score), location


def _refine(strip, template, width, height, reach=6):
    """Walk out from a coarse hit a pixel at a time.

    Worth the extra passes: the coarse grid lands near the right size, and
    the difference between near and exact is the difference between 0.2 and
    0.99.
    """
    best = (width, height, -1.0)
    for w in range(max(8, width - reach), width + reach + 1):
        for h in range(max(8, height - reach), height + reach + 1):
            hit = _best_at(strip, template, w, h)
            if hit is not None and hit[0] > best[2]:
                best = (w, h, hit[0])
    return best


def find_scale(strip, templates, span: int, frame_height: int):
    """(width, height, score) of the portrait size that matches best.

    Found once, from a few heroes, rather than per hero: every portrait is
    the same size on screen, so searching the grid ten times over is ten
    times the work for one answer.

    **The grid is searched SHRUNK.** 19 widths x 17 heights x 2 probes is
    646 template matches, and on a 3440x1440 frame each one is a
    correlation over a 3440x432 strip — measured at 25.6 SECONDS on one
    tick of a real session, which is the freeze the user reported. The
    answer that costs nothing is that finding the neighbourhood does not
    need full resolution: the coarse pass runs on a strip decimated to
    about `SEARCH_WIDTH` across, which is 10-20x less work, and the result
    is then walked back to exact size at full resolution. Accuracy is
    unchanged because `_refine` at the end does the same pixel walk it
    always did — it just starts from a scaled-up estimate rather than from
    a grid point.
    """
    shrink = max(1, int(round(strip.shape[1] / SEARCH_WIDTH)))
    if shrink > 1:
        small = cv2.resize(strip, (strip.shape[1] // shrink,
                                   max(1, strip.shape[0] // shrink)),
                           interpolation=cv2.INTER_AREA)
    else:
        small = strip

    best = None
    for template in templates:
        for width_frac in WIDTHS:
            width = max(8, int(round(span * width_frac)) // shrink)
            for height_frac in HEIGHTS:
                height = max(8, int(round(frame_height * height_frac))
                             // shrink)
                hit = _best_at(small, template, width, height)
                if hit is None:
                    continue
                if best is None or hit[0] > best[2]:
                    best = (width, height, hit[0])
    if best is None:
        return None
    if shrink > 1:
        # Sharpen the estimate where it is cheap, then once at full size.
        best = _refine(small, templates[0], best[0], best[1], reach=2)
        best = (best[0] * shrink, best[1] * shrink, best[2])
    return _refine(strip, templates[0], best[0], best[1],
                   reach=max(3, shrink))


def locate(frame, portraits: dict[int, np.ndarray],
           progress=None) -> list[Located]:
    """Find each named hero on the top strip.

    `progress` is called with a fraction 0..1 as it goes. This runs on a
    worker and takes SECONDS, and with the game feed filling the slots
    at the same time there was nothing anywhere saying it was running -
    "I may close it before it's done". A long job with no sign of life
    is indistinguishable from a hung one.
    """
    height, width = frame.shape[:2]
    _left, span = hud_box(width, height)
    strip = _grey(frame)[:max(1, int(height * TOP_FRACTION)), :]
    greys = {hid: _grey(image) for hid, image in portraits.items()
             if image is not None and image.size}
    if not greys:
        return []

    if progress:
        progress(0.0)
    probes = [greys[hid] for hid in list(greys)[:2]]
    scale = find_scale(strip, probes, int(span), height)
    if scale is None:
        return []
    # The scale grid is most of the work - hundreds of correlations
    # against ten per-hero passes - so it is most of the bar.
    if progress:
        progress(SCALE_SHARE)
    box_w, box_h, _score = scale

    out = []
    for number, (hero_id, template) in enumerate(greys.items(), 1):
        if progress:
            progress(SCALE_SHARE
                     + (1.0 - SCALE_SHARE) * number / max(1, len(greys)))
        hit = _best_at(strip, template, box_w, box_h)
        if hit is None:
            continue
        score, (x, y) = hit
        if score >= MIN_SCORE:
            out.append(Located(hero_id, x, y, box_w, box_h, score))
    out.sort(key=lambda item: item.x)
    return out


def layout_from(found: list[Located], width: int, height: int,
                base: DraftLayout | None = None) -> Calibration:
    base = base or DraftLayout()
    if len(found) < MIN_FOUND:
        return Calibration(found=found, note=(
            f"only {len(found)} of the ten portraits were found — not "
            "enough to place the boxes"))

    left, span = hud_box(width, height)
    xs = [item.x for item in found]
    steps = [b - a for a, b in zip(xs, xs[1:])]
    split = steps.index(max(steps)) + 1
    if not (2 <= split <= len(found) - 2):
        return Calibration(found=found, note=(
            "the portraits found do not fall into two banks — this frame is "
            "probably not a pick or strategy screen"))

    banks = (xs[:split], xs[split:])
    within = [b - a for bank in banks for a, b in zip(bank, bank[1:])]
    if not within:
        return Calibration(found=found, note="not enough portraits per bank")
    pitch = float(np.median(within))
    if pitch <= 0:
        return Calibration(found=found, note="portraits overlap; no pitch")

    # THE VERTICAL IS A FRACTION OF THE FRAME'S HEIGHT, exactly as
    # `SlotRect.to_pixels` reads it back. This briefly divided by the HUD
    # box's height instead; the real screenshots put those boxes on the
    # player NAME strip at every resolution, so it went back. A
    # measurement stored under a convention nothing renders it with is
    # worse than no measurement. On 16:9 and wider the two are the same
    # number anyway.
    layout = DraftLayout(
        radiant_x=(banks[0][0] - left) / span,
        dire_x=(banks[1][0] - left) / span,
        y=float(np.median([i.y for i in found])) / height,
        slot_w=float(np.median([i.w for i in found])) / span,
        slot_h=float(np.median([i.h for i in found])) / height,
        pitch=pitch / span,
        role_dy=base.role_dy, role_h=base.role_h,
    )
    for value in (layout.radiant_x, layout.dire_x, layout.y, layout.slot_w,
                  layout.slot_h, layout.pitch):
        if not 0.0 <= value <= 1.0:
            return Calibration(found=found, note=(
                "the measured layout falls outside the frame; ignoring it"))
    return Calibration(layout=layout, found=found, note=(
        f"measured from {len(found)} portraits, mean confidence "
        f"{np.mean([i.score for i in found]):.2f}"))


# How far outside a dragged rectangle to look for the real edges. A hand
# drawn box is a few pixels out on every side, and a pitch derived from a
# span that is 2% wide is 8% of a portrait out by the fifth one.
SNAP = 14
# The pitch is between a fifth and a quarter of a five-portrait bank: five
# portraits with no gap is exactly a fifth, and anything wider than a
# quarter would leave the fifth portrait outside the box.
PITCH_LO, PITCH_HI = 1 / 6.0, 1 / 4.0
# How much the weakest of a fit's ten predicted boundaries counts against
# its total. Enough that "all ten land on an edge" beats "nine land on five
# edges counted twice".
WEAKEST_WEIGHT = 5.0


def _edge_profile(grey) -> np.ndarray:
    """Per-column edge strength: where the vertical borders are.

    Portrait CONTENT is different for every hero, so nothing about it
    repeats. The borders between portraits do, which is the only periodic
    thing in a pick bar and therefore the only thing worth measuring.

    A zero is prepended so index i means "the boundary at the left of
    column i". `diff` puts the step between columns i and i+1 at i, which
    would put every measured edge one pixel low.
    """
    if grey.shape[1] < 2:
        return np.zeros(grey.shape[1], np.float32)
    columns = np.abs(np.diff(grey.astype(np.float32), axis=1)).mean(axis=0)
    return np.concatenate([[0.0], columns]).astype(np.float32)


def _row_profile(grey) -> np.ndarray:
    """The same thing horizontally, for the top and bottom edges."""
    if grey.shape[0] < 2:
        return np.zeros(grey.shape[0], np.float32)
    rows = np.abs(np.diff(grey.astype(np.float32), axis=0)).mean(axis=1)
    return np.concatenate([[0.0], rows]).astype(np.float32)


def _tolerant(profile: np.ndarray, reach: int = 2) -> np.ndarray:
    """Each position carries the strongest edge within `reach` of it.

    So a fit is scored on whether it lands NEAR an edge rather than exactly
    on one, which is what makes a hand-drawn box work, and it turns the
    inner loop into an array index instead of a slice and a max.
    """
    if profile.size == 0:
        return profile
    out = profile.copy()
    for shift in range(1, reach + 1):
        out[:-shift] = np.maximum(out[:-shift], profile[shift:])
        out[shift:] = np.maximum(out[shift:], profile[:-shift])
    return out


def measure_bank(frame, rect, slots: int = TEAM_SIZE):
    """Fit five evenly spaced portraits inside a hand-drawn rectangle.

    One box round a whole bank is, on its own, one equation for two
    unknowns: the box spans four pitches plus one portrait, and the gap
    between portraits could be anything. So the gap is MEASURED rather than
    assumed — the borders are the strongest vertical edges in the strip,
    and the fit that lands all ten of them on an edge is the right one.

    The edges are fitted rather than taken from the drag, on all four
    sides, because a hand-drawn box is several pixels out and a span 2% too
    wide misplaces the fifth portrait by a tenth of a portrait. Returns
    (x, y, w, h, pitch) in frame pixels, or None and a reason.
    """
    x, y, width, height = (int(v) for v in rect)
    top = max(0, y - SNAP)
    bottom = min(frame.shape[0], y + height + SNAP)
    left = max(0, x - SNAP)
    right = min(frame.shape[1], x + width + SNAP)
    # Judged on the DRAG, not on the padded search window: the padding is
    # ours and would let a twelve-pixel box look big enough.
    if height < 8 or width < 8 * slots:
        return None, "that rectangle is too small to hold five portraits"
    if bottom - top < 8 or right - left < 8 * slots:
        return None, "that rectangle falls outside the picture"
    grey = _grey(frame)[top:bottom, left:right]
    profile = _tolerant(_edge_profile(grey))
    if profile.size < 8 * slots:
        return None, "that rectangle is too small to measure"

    span = float(width)
    origin = x - left
    starts = range(max(0, origin - SNAP), origin + SNAP + 1)
    pitches = range(int(span * PITCH_LO), int(span * PITCH_HI) + 1)
    best = None
    for pitch in pitches:
        for begin in starts:
            lefts = begin + np.arange(slots) * pitch
            if lefts.min() < 0 or lefts.max() >= profile.size:
                continue
            # Clip the widths that would run off the end rather than
            # discarding the whole candidate: dropping a (start, pitch)
            # pair because its WIDEST portrait overruns threw away the
            # correct fit whenever the drag sat near the right of the
            # search window, and the wrong fit that survived was a whole
            # portrait out.
            room = profile.size - 1 - lefts.max()
            widths = np.arange(int(pitch * 0.60), min(pitch, room) + 1)
            if not widths.size:
                continue
            rights = lefts[:, None] + widths[None, :]
            edges = np.concatenate(
                [np.repeat(profile[lefts][:, None], widths.size, axis=1),
                 profile[rights]], axis=0)
            # Sum AND the weakest of the ten. A fit is only right if EVERY
            # boundary it predicts is on an edge, and the sum alone cannot
            # tell that apart from a fit whose portrait width equals its
            # pitch — that one predicts each right edge on top of the next
            # left edge, scoring the same five edges twice and landing a
            # whole portrait out.
            scores = edges.sum(axis=0) + WEAKEST_WEIGHT * edges.min(axis=0)
            index = int(np.argmax(scores))
            if best is None or scores[index] > best[0]:
                best = (float(scores[index]), begin, pitch,
                        int(widths[index]))
    if best is None:
        return None, "no portrait edges found inside that rectangle"

    score, begin, pitch, slot_w = best
    # A flat picture fits nothing in particular, so the honest answer is
    # the even split rather than whatever noise happened to win.
    if score <= float(profile.mean()) * (2 * slots + WEAKEST_WEIGHT):
        pitch = int(round(span / slots))
        slot_w, begin = pitch, origin
        note = ("no portrait borders stood out, so the bank was split into "
                "five equal slots — check the boxes")
    else:
        note = f"{slots} portraits, {slot_w}px wide, {pitch}px apart"

    # And the same fit vertically, so a box drawn a few pixels tall or
    # short does not carry that error into every crop.
    band = _tolerant(_row_profile(
        grey[:, begin:begin + (slots - 1) * pitch + slot_w]))
    y_origin, y_span = y - top, height
    # Pulled towards the drawn box, so a weak or spurious row edge cannot
    # drag the height ten pixels off. The horizontal fit needs no such
    # anchor: it has ten edges agreeing with each other, and this has two.
    anchor = float(band.mean()) * 0.08
    best_y = None
    for y0 in range(max(0, y_origin - SNAP), y_origin + SNAP + 1):
        for y1 in range(y0 + max(8, y_span - SNAP), y0 + y_span + SNAP + 1):
            if y1 >= band.size:
                break
            value = (float(band[y0] + band[y1])
                     - anchor * (abs(y0 - y_origin) + abs(y1 - y0 - y_span)))
            if best_y is None or value > best_y[0]:
                best_y = (value, y0, y1 - y0)
    if best_y is not None and best_y[0] > float(band.mean()) * 2:
        _v, y_origin, y_span = best_y
    return (left + begin, top + y_origin, slot_w, y_span, pitch), note


def calibrate(frame, portraits: dict[int, np.ndarray],
              base: DraftLayout | None = None) -> Calibration:
    if frame is None or not portraits:
        return Calibration(note="no frame, or the game has named no heroes")
    height, width = frame.shape[:2]
    return layout_from(locate(frame, portraits), width, height, base)


def base_portraits(hero_ids) -> dict[int, np.ndarray]:
    """The downloaded base portrait for each of these heroes.

    Base only, never persona or arcana variants: those are alternative
    appearances of the same hero, and one of them matching in the wrong
    place would move the boxes rather than confirm them.
    """
    import cv2

    from . import library as library_mod

    wanted = set(hero_ids)
    out: dict[int, np.ndarray] = {}
    try:
        for hero_id, label, path in library_mod._iter_source_images():
            if hero_id not in wanted or not label.startswith("base/"):
                continue
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is not None and image.size:
                out[hero_id] = image
    except (FileNotFoundError, ValueError):
        return {}
    return out


# ---------------------------------------------------------------------------
# Finding the bar with nothing to go on but the picture
# ---------------------------------------------------------------------------
#
# `measure_bank` above fits five portraits INSIDE a rectangle somebody drew.
# That is the same fit this does, minus the drawing: the pick bar is the
# only periodic thing on the screen, so the two banks can be found rather
# than pointed at — which is the whole reason a stranger should never have
# to drag anything.
#
# THE MIRROR IS WHAT MAKES IT TRACTABLE. Ten free rectangles is a search
# nobody can afford; two banks reflected about the HUD's centre line is
# THREE numbers — where the first portrait starts, how far apart they are,
# and how wide one is — with twenty predicted boundaries to score them on.
# Confirmed against a real 3440x1440 client: the Radiant bank ran
# 0.1137-0.4336 of the HUD box and the Dire bank 0.5664-0.8863, which is
# the same two numbers reflected to within a pixel.
#
# It also kills the false positive. Plenty of HUDs have a row of evenly
# spaced boxes in them somewhere; almost none have two runs of five that
# are each other's reflection about the centre of the screen.

# Where the first Radiant portrait's left edge may sit, as a fraction of
# the HUD box. Measured 0.114 on a real client; the range covers a HUD
# scaled well either side of that.
BANK_START = (0.02, 0.26)
# Pitch between portraits in a bank, same units. Measured 0.065.
BANK_PITCH = (0.038, 0.105)
# A bank may not reach the middle of the HUD: the timer lives there.
BANK_REACH = 0.47
# A portrait is Dota's own 16:9 top-bar crop, which is what the downloaded
# base images are. Dota fits rather than stretches it, so this is a PULL on
# the vertical fit and never a constraint — the height is still measured.
PORTRAIT_ASPECT = 16 / 9
# How hard the two vertical anchors pull, in units of the strip's mean edge
# strength. Horizontally there are twenty boundaries agreeing with each
# other; vertically there are two, so they need help or a single bright row
# of HUD chrome wins.
ASPECT_PULL = 2.5
TOP_PULL = 4.0
# Every portrait has a GUTTER after it, and the gutter is the discriminator.
# A fit whose portrait width equals its pitch predicts each right edge on
# top of the next left edge: all twenty of its boundaries land on the ten
# real LEFT edges, counted twice, so it scores as well as the truth and
# lands a whole portrait out. Scoring the gutters too is what tells them
# apart — a real gutter is flat background, and the degenerate fit has no
# gutter to be flat. Measured on a real client the gutter is about 9% of
# the pitch, so a fit claiming a portrait wider than this has not found
# one.
WIDEST_SLOT = 0.95
# What a bright gutter costs, per gutter, against the twenty boundaries.
GUTTER_WEIGHT = 1.5
# A fit has to beat the picture's own noise by this much before it is
# believed. Twenty boundaries plus the weakest-edge weighting, all at the
# mean, is what a flat rectangle scores.
FIT_MARGIN = 1.35


def _mirrored_fit(profile: np.ndarray, gutters: np.ndarray,
                  width: int, starts, pitches, narrowest: float = 0.55):
    """Best (score, start, pitch, slot_w) for two mirrored banks of five.

    `narrowest` is the slimmest portrait allowed, as a share of its own
    pitch, and it is THE GUARD AGAINST A HARMONIC. A fit at half the true
    pitch puts twenty boundaries on ten real edges and ten more on
    nothing, which the sum alone can still like — but it can only do that
    by claiming a portrait about half the width of its own spacing. Real
    portraits nearly touch. The live path keeps the loose 0.55 it was
    measured with, because there a drawn rectangle already pins the scale;
    a search with the start AND the scale free needs the tighter bar.

    Scored as `measure_bank` scores one bank — the sum of the boundaries
    AND the weakest of them — plus the gutters, which is the half
    `measure_bank` does not need because a drawn rectangle already pins the
    start. Here the start is free, so the fit that is one portrait out has
    to be beaten on evidence: it puts twenty boundaries on ten real edges
    and leaves no gutter at all.

    `gutters` is the RAW edge profile, not the tolerant one: a gutter is
    only a tenth of a pitch wide and smearing every edge by two pixels
    either way would fill it in.

    Every candidate START is evaluated at once rather than in a Python
    loop. There are a couple of hundred of them per pitch and sixty
    pitches, and looping both took 1.4 SECONDS a frame — which matters
    even for a setup step, because the point of running it during a whole
    draft is to fit dozens of frames and keep only what they agree on.
    """
    index = np.arange(TEAM_SIZE)
    limit = profile.size - 1
    starts = np.asarray(starts, dtype=np.int64)
    if not starts.size:
        return None
    best = None
    for pitch in pitches:
        widths = np.arange(max(4, int(pitch * narrowest)),
                           max(5, int(pitch * WIDEST_SLOT)) + 1)
        if not widths.size:
            continue
        # Mid-gutter, as an offset from a portrait's own left edge.
        mid = widths + (pitch - widths) // 2                       # (W,)
        span = 4 * pitch + widths                                  # (W,)

        lefts = starts[:, None] + index[None, :] * pitch           # (S, 5)
        rights = lefts[:, :, None] + widths[None, None, :]         # (S, 5, W)
        # The reflection: whatever gap the Radiant bank leaves on the left,
        # the Dire bank leaves on the right.
        dire_x = width - starts[:, None] - span[None, :]           # (S, W)
        dire_lefts = dire_x[:, None, :] + (index * pitch)[None, :, None]
        dire_rights = dire_lefts + widths[None, None, :]

        ok = ((rights.max(axis=1) < width * BANK_REACH)
              & (dire_lefts.min(axis=1) > width * (1 - BANK_REACH))
              & (dire_rights.max(axis=1) <= limit)
              & (lefts.min(axis=1)[:, None] >= 0))                 # (S, W)
        if not ok.any():
            continue

        wide = np.broadcast_to(profile[lefts][:, :, None],
                               rights.shape)                       # (S, 5, W)
        edges = np.concatenate([
            wide,
            profile[np.clip(rights, 0, limit)],
            profile[np.clip(dire_lefts, 0, limit)],
            profile[np.clip(dire_rights, 0, limit)],
        ], axis=1)                                                 # (S, 20, W)
        # Four gutters per bank — there is none after the fifth portrait,
        # where the bar simply ends.
        holes = np.concatenate([
            gutters[np.clip(lefts[:, :-1, None] + mid[None, None, :],
                            0, limit)],
            gutters[np.clip(dire_lefts[:, :-1, :] + mid[None, None, :],
                            0, limit)],
        ], axis=1)                                                 # (S, 8, W)
        scores = (edges.sum(axis=1) + WEAKEST_WEIGHT * edges.min(axis=1)
                  - GUTTER_WEIGHT * holes.sum(axis=1))             # (S, W)
        scores = np.where(ok, scores, -np.inf)
        flat = int(np.argmax(scores))
        row, col = divmod(flat, scores.shape[1])
        if np.isfinite(scores[row, col]) and (
                best is None or scores[row, col] > best[0]):
            best = (float(scores[row, col]), int(starts[row]), int(pitch),
                    int(widths[col]))
    return best


def _vertical_fit(strip_grey: np.ndarray, columns: np.ndarray,
                  slot_w: int):
    """Top and bottom of the portraits, from the rows all ten share.

    Read off the bank COLUMNS only. The gutters between portraits and the
    middle of the bar carry the timer and the mode name, whose rows are
    nothing to do with where a portrait starts.

    Two anchors, because two edges cannot corroborate each other the way
    twenty can: a portrait is about 16:9, and the pick bar hugs the top of
    the screen. Both are pulls rather than rules — the answer is still
    whichever pair of rows is actually brightest, nudged.
    """
    if not columns.size:
        return None
    band = _tolerant(_row_profile(strip_grey[:, columns]))
    rows = band.size
    if rows < 8:
        return None
    want = max(4.0, slot_w / PORTRAIT_ASPECT)
    floor = float(band.mean()) or 1e-6

    y0 = np.arange(rows)[:, None]
    y1 = np.arange(rows)[None, :]
    height = y1 - y0
    ok = (height >= max(6, int(want * 0.5))) & (height <= want * 2.0)
    value = (band[y0] + band[y1]
             - floor * ASPECT_PULL * np.abs(height - want) / want
             - floor * TOP_PULL * (y0 / rows))
    value = np.where(ok, value, -np.inf)
    if not np.isfinite(value).any():
        return None
    top, bottom = np.unravel_index(int(np.argmax(value)), value.shape)
    return int(top), int(bottom - top)


def find_banks(frame, base: DraftLayout | None = None):
    """The whole layout from one frame, with no heroes and no game data.

    This is the step that removes the drag. It needs no portrait library,
    no GSI and no minimap — only a frame with a pick bar in it — so it can
    run DURING hero selection rather than after the draft is over, and it
    can run on every frame of one and be believed only where they agree.

    Returns (DraftLayout, note) or (None, reason). It never invents a
    layout: a picture with no mirrored pair of five in it scores at the
    noise floor and is refused.
    """
    base = base or DraftLayout()
    if frame is None or not getattr(frame, "size", 0):
        return None, "there is no picture to measure"
    height, width = frame.shape[:2]
    left_edge, span = hud_box(width, height)
    if span < 8 * 2 * TEAM_SIZE or height < 16:
        return None, "the frame is too small to hold a pick bar"

    x0 = int(round(left_edge))
    strip = _grey(frame)[:max(8, int(height * BAR_FRACTION)),
                         x0:x0 + int(round(span))]
    if strip.shape[1] < 8 * 2 * TEAM_SIZE:
        return None, "the frame is too small to hold a pick bar"

    # Coarse pass on a decimated strip, for the same reason `find_scale`
    # does it: the grid only has to find the neighbourhood, and a 3440-wide
    # strip is ten times the work for the same answer.
    shrink = max(1, int(round(strip.shape[1] / SEARCH_WIDTH)))
    small = (strip if shrink == 1 else
             cv2.resize(strip, (strip.shape[1] // shrink,
                                max(2, strip.shape[0] // shrink)),
                        interpolation=cv2.INTER_AREA))
    small_w = small.shape[1]
    raw = _edge_profile(small)
    profile = _tolerant(raw)
    coarse = _mirrored_fit(
        profile, raw, small_w,
        np.arange(int(small_w * BANK_START[0]),
                  int(small_w * BANK_START[1]) + 1),
        range(int(small_w * BANK_PITCH[0]), int(small_w * BANK_PITCH[1]) + 1))
    if coarse is None:
        return None, "no pick bar found in the top of this frame"
    if coarse[0] <= float(profile.mean()) * (4 * TEAM_SIZE
                                             + WEAKEST_WEIGHT) * FIT_MARGIN:
        return None, ("nothing in the top of this frame looks like two banks "
                      "of five portraits — is the pick screen up?")

    # Walk the coarse answer back to full resolution. Everything below is
    # in the strip's own pixels, which are the HUD box's.
    if shrink > 1:
        full_raw = _edge_profile(strip)
        full = _tolerant(full_raw)
        scale = strip.shape[1] / float(small_w)
        centre = [int(round(v * scale)) for v in coarse[1:]]
        # The two reaches are NOT the same, and one number for both left
        # 4K short. A coarse pitch is an integer at the decimated scale, so
        # it can be half a decimated pixel out — and the pitch MULTIPLIES:
        # by the fifth portrait that is four times the error, which walks
        # the bank further than a reach sized for the start alone can get
        # back. So the start is allowed the accumulated slack and the pitch
        # only its own.
        pitch_reach = shrink + 2
        start_reach = 3 * shrink + 2
        fine = _mirrored_fit(
            full, full_raw, strip.shape[1],
            np.arange(max(0, centre[0] - start_reach),
                      centre[0] + start_reach + 1),
            range(max(4, centre[1] - pitch_reach),
                  centre[1] + pitch_reach + 1))
        best = fine or coarse
        if fine is None:
            best = (coarse[0], *centre)
    else:
        best = coarse
    _score, start, pitch, slot_w = best

    # The vertical, read off the twenty columns the portraits occupy.
    index = np.arange(TEAM_SIZE)
    bank_span = 4 * pitch + slot_w
    dire_x = strip.shape[1] - start - bank_span
    columns = np.concatenate([
        np.concatenate([np.arange(s + i * pitch, s + i * pitch + slot_w)
                        for i in index])
        for s in (start, dire_x)])
    columns = columns[(columns >= 0) & (columns < strip.shape[1])]
    vertical = _vertical_fit(strip, columns, slot_w)
    if vertical is None:
        return None, "the portraits' top and bottom edges could not be found"
    top, slot_h = vertical

    # THE VERTICAL IS A FRACTION OF THE FRAME'S HEIGHT, exactly as
    # `SlotRect.to_pixels` reads it back. This briefly divided by the HUD
    # box's height instead; the real screenshots put those boxes on the
    # player NAME strip at every resolution, so it went back. A
    # measurement stored under a convention nothing renders it with is
    # worse than no measurement. On 16:9 and wider the two are the same
    # number anyway.
    layout = DraftLayout(
        radiant_x=start / span,
        dire_x=dire_x / span,
        y=top / height,
        slot_w=slot_w / span,
        slot_h=slot_h / height,
        pitch=pitch / span,
        role_dy=base.role_dy, role_h=base.role_h,
    )
    for name in ("radiant_x", "dire_x", "y", "slot_w", "slot_h", "pitch"):
        value = getattr(layout, name)
        if not 0.0 <= value <= 1.0:
            return None, (f"{name} came out at {value:.3f}, which is off "
                          "the frame")
    return layout, (f"{2 * TEAM_SIZE} portraits {slot_w}px wide, {pitch}px "
                    f"apart, {slot_h}px tall")
