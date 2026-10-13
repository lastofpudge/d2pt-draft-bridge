"""Which five are yours, settled by looking at the screen.

The game feed hands over all ten heroes at strategy time but not, reliably,
whose five are whose — five recorded matches and no rule found, one of them
inverted (see CLAUDE.md). The screen has never had that ambiguity: Radiant
is always the LEFT bank of the pick bar and Dire the right, and
`player.team_name` says which of those is yours. So the split does not need
a rule at all. It needs the ten known heroes located on the bar.

That is an easier problem than recognition proper, and this module exists
because of the difference: normally the app asks "which of 126 heroes is in
this box"; here it asks "which of THESE TEN is in this box", with the
answer guaranteed to be a permutation. Ten known candidates and ten boxes
is a small assignment problem, and a wrong answer requires two heroes to
out-match each other in each other's slots rather than one hero to beat 125
rivals.

Two paths, cheap first:

* **Placed** — with calibrated crop boxes, each of the ten boxes is scored
  against each of the ten candidates and the best consistent assignment
  wins. A hundred correlations on small crops: milliseconds.
* **Searched** — no usable calibration, so every candidate is hunted across
  the top strip at an unknown scale (`autocal.locate`, which is where the
  expensive scale grid lives). Seconds, not milliseconds, so callers run it
  once per match rather than per frame.

It never guesses. Anything short of ten confident, distinct heroes falling
into two banks of five returns a reading that is `ok is False`, carrying the
reason, and the caller keeps whatever it had.
"""

from dataclasses import dataclass, field

import cv2
import numpy as np

from . import autocal
from .layout import DraftLayout

# A placed crop is scored against a portrait resized to the same box, so
# the bar is lower than a search's: the box may be a few pixels out and the
# competition is nine heroes rather than 125.
MIN_PLACED_SCORE = 0.25
# How far the best candidate must beat the runner-up for a box. Two heroes
# scoring the same means the crop is not actually showing either of them.
MIN_MARGIN = 0.03
TEAM_SIZE = 5

# NINE LOCATED PORTRAITS STILL DETERMINE THE SIDES, and requiring ten is
# what put a real draft on the wrong teams. `locate` found 9 of the 10 in
# match 8000000001, `read_searched` refused the lot, and the minimap's
# coin-flip split won instead — Axe and Storm Spirit came out on the enemy
# team and the board scored 6/10.
#
# It is elimination rather than a guess, and the same elimination
# `harvest.by_elimination` already runs: the game NAMED all ten, nine are
# on the bar in known positions, so the tenth is the one hero left and it
# belongs to whichever bank has four. `_banks` still refuses anything it
# cannot read off the geometry.
MIN_FOUND = 2 * TEAM_SIZE - 1

# How much wider than one step the gap BETWEEN the banks has to be before
# it is believed to be the bank boundary rather than a hole where a
# portrait was missed. A missed portrait leaves a two-step hole, so the
# threshold has to clear 2 — and the bar has room for it: on the measured
# 16:9 layout the banks are 4.87 steps apart against a pitch of one, so a
# hole is less than half the bank gap. Below this the reading is REFUSED,
# never split anyway: the whole point of the fix is a wrong split asserted
# confidently is worse than the guess it replaces.
BANK_GAP_STEPS = 2.5

# HOW MANY BOXES HAVE TO HOLD A NAMED HERO BEFORE THE BOXES THEMSELVES ARE
# BELIEVED. `read_placed` refuses unless ALL TEN land, which is right for a
# LINE-UP — a permutation with one hero guessed is a wrong team — and is a
# terrible test of the geometry. One hero wearing a persona, an arcana or a
# set the library has no picture of fails its box while the other nine sail
# through, and the refusal that comes out of that used to raise a banner
# reading "the app cannot find the pick portraits on your screen". Nine of
# them had just been found.
#
# So the count is carried and the two causes are told apart by it. The
# boxes are one rigid set at one pitch, so a whole bank's worth of them
# landing on portraits the game named is not something a wrong geometry
# does by accident: below that, the calibration is the suspect; at or above
# it, the ARTWORK is, and the variant harvester is what answers that.
BOXES_PROVE_THE_GEOMETRY = TEAM_SIZE


@dataclass
class ScreenLineup:
    """The pick bar, read left to right."""
    left: list[int] = field(default_factory=list)    # Radiant bank
    right: list[int] = field(default_factory=list)   # Dire bank
    confidence: float = 0.0
    how: str = ""            # "placed" | "searched"
    note: str = ""
    # Where the search found each portrait, when it was the search that
    # ran. Carried so ONE expensive search can also calibrate the crop
    # boxes: it has already measured every portrait's position and size,
    # and throwing that away means paying for it again next match.
    found: list = field(default_factory=list)
    # How many crop boxes held a hero the game had already named. Carried
    # on a REFUSAL as well as on a reading, because it is the only thing
    # that separates "the boxes are off the portraits" from "one hero is
    # wearing artwork we have never seen".
    matched: int = 0
    # The definite verdict on the calibration, and the only one this app
    # ever gets. Set by `read_placed` alone, which is the one place that
    # knows both what it was looking for and how much of it it found.
    boxes_wrong: bool = False

    @property
    def ok(self) -> bool:
        return len(self.left) == TEAM_SIZE and len(self.right) == TEAM_SIZE

    def sides_for(self, my_team: str) -> tuple[list[int], list[int]]:
        """(allies, enemies) given which team the player is on.

        Radiant is the left bank and Dire the right — that mapping is the
        whole reason this is worth doing, and it is not a guess.
        """
        if my_team == "dire":
            return (list(self.right), list(self.left))
        return (list(self.left), list(self.right))


def _grey(image):
    if image is None or image.size == 0:
        return None
    if image.ndim == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image


def _score(crop, template) -> float:
    """Correlation of one crop against one portrait, at the crop's size."""
    if crop is None or template is None or crop.size == 0:
        return -1.0
    height, width = crop.shape[:2]
    if height < 4 or width < 4:
        return -1.0
    resized = cv2.resize(template, (width, height),
                         interpolation=cv2.INTER_AREA)
    result = cv2.matchTemplate(crop, resized, cv2.TM_CCOEFF_NORMED)
    return float(result[0][0])


def _assign(scores: np.ndarray,
            hero_ids: list[int]) -> tuple[list[int] | None, int]:
    """Best consistent slot -> hero assignment, greedily, and how far it got.

    Greedy rather than optimal (Hungarian) on purpose: with ten heroes that
    are actually on screen the score matrix is strongly diagonal once
    permuted, so the best pair overall is right, and taking it removes both
    a row and a column. If the matrix is NOT strongly separated the margin
    check below rejects the whole reading anyway, which is the outcome we
    want — an ambiguous board should produce nothing, not a plausible
    permutation.

    THE COUNT IS RETURNED EVEN WHEN THE READING IS REFUSED, and it is not
    bookkeeping: greedy takes the strongest pair first, so the pairs placed
    before it gave up are the confident ones, and how many of those there
    were is what tells a bad crop box from a hero in a costume. A reading
    that stops at nine has found nine portraits exactly where the
    calibration said they would be.
    """
    remaining = scores.copy()
    placed: dict[int, int] = {}
    for _ in range(len(hero_ids)):
        slot, hero = np.unravel_index(int(np.argmax(remaining)),
                                      remaining.shape)
        best = remaining[slot, hero]
        if best < MIN_PLACED_SCORE:
            return None, len(placed)
        # The runner-up for this same slot, among heroes still unclaimed.
        rivals = np.delete(remaining[slot], hero)
        if rivals.size and best - float(rivals.max()) < MIN_MARGIN:
            return None, len(placed)
        placed[int(slot)] = hero_ids[int(hero)]
        remaining[slot, :] = -np.inf
        remaining[:, hero] = -np.inf
    return [placed[i] for i in range(len(hero_ids))], len(placed)


def read_placed(frame, hero_ids: list[int],
                layout: DraftLayout,
                portraits: dict[int, np.ndarray] | None = None
                ) -> ScreenLineup:
    """Assign the ten known heroes to the ten calibrated crop boxes."""
    if frame is None or len(hero_ids) != 2 * TEAM_SIZE:
        return ScreenLineup(note="need a frame and exactly ten named heroes")
    art = portraits if portraits is not None else \
        autocal.base_portraits(hero_ids)
    greys = {hid: _grey(image) for hid, image in art.items()}
    ordered = [hid for hid in hero_ids if greys.get(hid) is not None]
    if len(ordered) != len(hero_ids):
        return ScreenLineup(note=(
            f"{len(hero_ids) - len(ordered)} of the ten have no downloaded "
            "portrait to match against"))

    height, width = frame.shape[:2]
    picture = _grey(frame)
    slots = layout.slots()
    scores = np.full((len(slots), len(ordered)), -1.0, dtype=np.float64)
    for row, rect in enumerate(slots):
        x, y, w, h = rect.to_pixels(width, height)
        if x < 0 or y < 0 or x + w > width or y + h > height:
            # No doubt to weigh here: a box off the edge of the picture
            # cannot be on a portrait whatever the artwork looks like.
            return ScreenLineup(boxes_wrong=True, note=(
                "the crop boxes fall outside the frame — calibration is off"))
        crop = picture[y:y + h, x:x + w]
        for col, hid in enumerate(ordered):
            scores[row, col] = _score(crop, greys[hid])

    assignment, matched = _assign(scores, ordered)
    if assignment is None:
        # TWO CAUSES, ONE REFUSAL, and they want opposite answers. Boxes
        # that are off the bar match nothing; boxes that are exactly right
        # still refuse when ONE hero is wearing a persona, an arcana or a
        # set the library has no picture of — and that is the normal case
        # the variant harvester exists for, not a calibration fault.
        if matched >= BOXES_PROVE_THE_GEOMETRY:
            return ScreenLineup(matched=matched, note=(
                f"{matched} of the ten matched in the calibrated boxes, so "
                "the boxes are on the pick bar — the rest are wearing "
                "portraits the library does not have yet"))
        return ScreenLineup(matched=matched, boxes_wrong=True, note=(
            f"only {matched} of the ten heroes could be found in the crop "
            "boxes — the boxes are probably not on the portraits"))
    return ScreenLineup(
        left=assignment[:TEAM_SIZE], right=assignment[TEAM_SIZE:],
        confidence=float(np.mean([
            scores[i, ordered.index(hid)]
            for i, hid in enumerate(assignment)])),
        how="placed", matched=matched,
        note=f"matched {len(assignment)} portraits in the calibrated boxes")


def _banks_look_right(split: int, total: int) -> bool:
    """Does cutting here give two banks the pick bar could really have?

    Five a side, and with one portrait missed, five and four. Nine or ten
    located is all `read_searched` accepts, so the left bank is four or
    five and the right is whatever is left of the same.
    """
    left, right = split, total - split
    return left in (4, 5) and right in (4, 5) and left + right == total


def split_banks(found: list) -> tuple[int, str]:
    """Where the left bank ends, read off the gaps. (-1, reason) if it cannot be.

    `found` is sorted by x. Within a bank the step is one pitch; between
    the banks it is several (4.87 on the measured 16:9 layout), and a
    portrait the search missed leaves a hole of two. So the MEDIAN step is
    the pitch whichever of those is present — at most two of the eight or
    nine steps are anything else — and the bank boundary is the one step
    that clears `BANK_GAP_STEPS` of it.

    Exactly one step may clear it. Two would mean either a second bank or
    a bar this function cannot read, and inventing a boundary between them
    is how a confident wrong answer gets made.
    """
    xs = [item.x for item in found]
    steps = [b - a for a, b in zip(xs, xs[1:])]
    if len(steps) < 2:
        return -1, "too few portraits on the bar to find the gap between banks"
    pitch = float(np.median(steps))
    if pitch <= 0:
        return -1, "the portraits are stacked at one x — this is not a pick bar"
    wide = [i for i, step in enumerate(steps)
            if step >= BANK_GAP_STEPS * pitch]
    if not wide:
        return -1, (f"no gap on the bar is {BANK_GAP_STEPS:g}x the "
                    f"{pitch:.0f}px step, so the two banks cannot be told apart")
    # TWO WIDE GAPS IS NOT A REASON TO GIVE UP, and refusing on the COUNT
    # threw away most of the readings this app has ever taken. Across
    # twelve reported drafts the side reading failed seven times, and in
    # three of those the boxes were provably ON the bar - "9 of the ten
    # matched in the calibrated boxes" and then "2 gaps look like the
    # split between banks; this frame is probably not a pick bar".
    #
    # THE BAR IS FIVE A SIDE, which is a fact about Dota rather than a
    # threshold, and it is the constraint that was going unused. A hole
    # where a portrait was missed and the real boundary between banks are
    # both "wide"; only one of them cuts the bar into banks of five. So
    # the candidates are TESTED against that instead of counted, and the
    # ambiguity that remains - two gaps that would BOTH give five a side,
    # which needs a bar this function cannot read - is still refused.
    total = len(found)
    fits = [i for i in wide if _banks_look_right(i + 1, total)]
    if len(fits) == 1:
        return fits[0] + 1, ""
    if not fits:
        shape = ", ".join(f"{i + 1}/{total - i - 1}" for i in wide)
        many = "s" if len(wide) > 1 else ""
        return -1, (f"{len(wide)} gap{many} look like the split between "
                    f"banks and none of them is five a side ({shape}); "
                    "this frame is probably not a pick bar")
    return -1, (f"{len(fits)} gaps would each split the bar five a side; "
                "this frame is probably not a pick bar")


def _place_missing(found: list, hero_ids: list[int],
                   split: int) -> tuple[list[int], list[int], str]:
    """The ten hero ids in bank order, with the unlocated one eliminated in.

    Nine located leaves exactly one hero unaccounted for and exactly one
    bank holding four, so which side it is on is not a guess. WHERE in
    that bank is: an interior hole shows as a double step and takes it,
    and otherwise it was at one end and the end cannot be told from the
    gaps alone. It goes last, and the note says so, because the order
    inside a team is the one thing here a drag already fixes.
    """
    left = [item.hero_id for item in found[:split]]
    right = [item.hero_id for item in found[split:]]
    missing = [hid for hid in hero_ids
               if hid not in {item.hero_id for item in found}]
    if not missing:
        return left, right, ""
    (absent,) = missing
    every = [item.x for item in found]
    pitch = float(np.median([b - a for a, b in zip(every, every[1:])]))
    short, xs = ((left, every[:split]) if len(left) < len(right)
                 else (right, every[split:]))
    steps = [b - a for a, b in zip(xs, xs[1:])]
    # A missed portrait leaves a step of two pitches where there should be
    # one; 1.5 is the midpoint between the two, and nothing else on a bank
    # is anywhere near it.
    holes = [i for i, step in enumerate(steps) if step >= 1.5 * pitch]
    if len(holes) == 1:
        short.insert(holes[0] + 1, absent)
        return left, right, "placed the tenth in the gap it left on the bar"
    short.append(absent)
    return left, right, ("placed the tenth by elimination; it was at one end "
                         "of its bank and which end cannot be read off the gaps")


def read_searched(frame, hero_ids: list[int],
                  portraits: dict[int, np.ndarray] | None = None,
                  progress=None) -> ScreenLineup:
    """Hunt each of the ten across the top strip, no calibration needed.

    This is the expensive path — the scale grid in `autocal.find_scale` is
    hundreds of correlations — so it is for once per match, never per frame.
    """
    if frame is None or len(hero_ids) != 2 * TEAM_SIZE:
        return ScreenLineup(note="need a frame and exactly ten named heroes")
    art = portraits if portraits is not None else \
        autocal.base_portraits(hero_ids)
    found = autocal.locate(frame, art, progress=progress)
    if len(found) < MIN_FOUND:
        return ScreenLineup(note=(
            f"found {len(found)} of the ten portraits on screen; "
            f"{MIN_FOUND} are needed before the sides can be read off"))

    split, why = split_banks(found)
    if split < 0:
        return ScreenLineup(note=why)
    sizes = (split, len(found) - split)
    if sorted(sizes) not in ([TEAM_SIZE, TEAM_SIZE],
                             [TEAM_SIZE - 1, TEAM_SIZE]):
        return ScreenLineup(note=(
            f"the gap between banks splits the bar {sizes[0]}/{sizes[1]}, "
            "which is not five a side — this frame is probably not a pick bar"))

    left, right, placed = _place_missing(found, hero_ids, split)
    mean = float(np.mean([item.score for item in found]))
    note = (f"located all ten on the bar, mean confidence {mean:.2f}"
            if len(found) == 2 * TEAM_SIZE else
            f"located {len(found)} of the ten on the bar, mean confidence "
            f"{mean:.2f}; {placed}")
    return ScreenLineup(
        left=left, right=right,
        confidence=mean,
        how="searched",
        found=found,
        note=note)


def read_lineup(frame, hero_ids: list[int],
                layout: DraftLayout | None = None,
                allow_search: bool = True,
                portraits: dict[int, np.ndarray] | None = None,
                progress=None) -> ScreenLineup:
    """Cheap path, then the expensive one if it is allowed and needed."""
    if layout is not None:
        placed = read_placed(frame, hero_ids, layout, portraits)
        if placed.ok:
            return placed
    else:
        placed = ScreenLineup(note="no calibration to place boxes with")
    if not allow_search:
        return placed
    searched = read_searched(frame, hero_ids, portraits,
                             progress=progress)
    if searched.ok:
        return searched
    return ScreenLineup(note=f"{placed.note}; then {searched.note}")
