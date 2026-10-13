"""Slot recognition: crop each of the ten pick slots, perceptual-hash the
crop (plus a small grid of shifted windows, since pHash is not translation
invariant and crop boxes are never pixel-perfect), nearest-match against the
portrait library, and apply the confidence margin rule.

UNKNOWN IS A LEGITIMATE STATE, NOT AN ERROR. If the Hamming margin between
the best match and the best match belonging to a *different* hero is too
small, the slot is marked unknown and scoring proceeds with the slots that
resolved. Silent about one slot beats asserting the wrong hero and inverting
the recommendation.
"""

from dataclasses import dataclass

import cv2
import numpy as np

from .layout import DraftLayout, SlotRect
from .library import EMPTY_SLOT, Library, RecognitionParams
from .phash import phash


@dataclass
class SlotRead:
    rect: SlotRect
    hero_id: int | None      # resolved hero id; EMPTY_SLOT; None = unknown
    best_label: str          # closest library entry (for debugging)
    distance: int            # Hamming distance to it
    margin: int              # distance gap to the closest DIFFERENT hero
    crop: np.ndarray | None = None   # kept when debugging


@dataclass
class DraftRead:
    slots: list[SlotRead]

    def team_ids(self, team: str) -> list[int]:
        return [s.hero_id for s in self.slots
                if s.rect.team == team and s.hero_id is not None
                and s.hero_id != EMPTY_SLOT]

    def unknown_count(self) -> int:
        return sum(1 for s in self.slots if s.hero_id is None)


def crop_rect(frame: np.ndarray, rect: SlotRect) -> np.ndarray:
    h, w = frame.shape[:2]
    x, y, cw, ch = rect.to_pixels(w, h)
    x, y = max(0, x), max(0, y)
    return frame[y:y + ch, x:x + cw]


def _match_queries(queries: list[np.ndarray], lib: Library,
                   params: RecognitionParams) -> tuple[int | None, str, int, int]:
    """Best entry over all query hashes; margin against the closest entry of
    a different hero (min-per-entry across queries first)."""
    q = np.stack(queries)                                # (Q, bits)
    dists = np.count_nonzero(lib.bits[None, :, :] != q[:, None, :], axis=2)
    per_entry = dists.min(axis=0)                        # (N,)
    best = int(np.argmin(per_entry))
    best_id = int(lib.hero_ids[best])
    best_d = int(per_entry[best])
    others = per_entry[lib.hero_ids != best_id]
    margin = int(others.min() - best_d) if others.size else params.bits
    if best_d <= params.max_distance and margin >= params.min_margin:
        return best_id, lib.labels[best], best_d, margin
    return None, lib.labels[best], best_d, margin


def match_crop(crop: np.ndarray, lib: Library,
               params: RecognitionParams) -> tuple[int | None, str, int, int]:
    """Single-crop match (no shift search) — used for library images and
    harvested crops that are already tightly framed."""
    if crop.size == 0:
        return None, "(empty crop — layout off screen?)", 10 ** 6, 0
    grey = crop if crop.ndim == 2 else cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if float(grey.std()) < params.flat_std:
        return EMPTY_SLOT, "(flat placeholder)", 0, params.bits
    return _match_queries([phash(crop, params.hash_size)], lib, params)


def match_slot(frame: np.ndarray, rect: SlotRect, lib: Library,
               params: RecognitionParams) -> tuple[int | None, str, int, int]:
    """Match one slot with the shift-search grid around its nominal rect."""
    fh, fw = frame.shape[:2]
    x, y, cw, ch = rect.to_pixels(fw, fh)
    central = frame[max(0, y):y + ch, max(0, x):x + cw]
    if central.size == 0:
        return None, "(empty crop — layout off screen?)", 10 ** 6, 0
    grey = cv2.cvtColor(central, cv2.COLOR_BGR2GRAY)
    if float(grey.std()) < params.flat_std:
        return EMPTY_SLOT, "(flat placeholder)", 0, params.bits

    queries = []
    steps = range(-params.shift_steps, params.shift_steps + 1)
    for ky in steps:
        for kx in steps:
            ox = x + round(kx * params.shift_frac * cw)
            oy = y + round(ky * params.shift_frac * ch)
            ox = min(max(ox, 0), fw - cw)
            oy = min(max(oy, 0), fh - ch)
            queries.append(phash(frame[oy:oy + ch, ox:ox + cw],
                                 params.hash_size))
    return _match_queries(queries, lib, params)


def read_draft(frame: np.ndarray, layout: DraftLayout, lib: Library,
               params: RecognitionParams, keep_crops: bool = False) -> DraftRead:
    slots = []
    for rect in layout.slots():
        hero_id, label, dist, margin = match_slot(frame, rect, lib, params)
        slots.append(SlotRead(
            rect=rect, hero_id=hero_id, best_label=label,
            distance=dist, margin=margin,
            crop=crop_rect(frame, rect) if keep_crops else None))
    return DraftRead(slots=slots)
