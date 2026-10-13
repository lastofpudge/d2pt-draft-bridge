"""Cheap draft-screen detection gate.

At ~1 Hz the captured frame is heavily downscaled and compared against
reference signatures of the pick screen layout; when it trips, the session
steps up to the 2 Hz full recognition cycle, and drops back to idle when it
stops tripping. A manual override in the UI forces recognition on when the
gate fails — the gate is an economiser, never a hard gatekeeper.

The comparison is normalised cross-correlation on zero-mean unit-norm
signatures, so overall brightness and contrast (day/night UI themes, gamma)
cancel out; score = 1 - correlation, 0 for identical structure, ~1 for an
unrelated screen.

References live in assets/gate/*.png (gitignored, machine-specific) and are
harvested automatically: whenever full recognition resolves most of a
frame's slots, that frame IS the draft screen, so its signature is saved.
The gate may therefore start with zero references — the manual override
bootstraps the first draft, and every draft after that is gated
automatically. Pure numpy/cv2; testable from saved or synthetic frames.
"""

from pathlib import Path

import cv2
import numpy as np

from ..config import ASSETS_DIR

GATE_DIR = ASSETS_DIR / "gate"
SIG_W, SIG_H = 24, 14
# score (= 1 - correlation) at or below which a frame counts as the draft
# screen. Same-screen recaptures land near 0; unrelated screens near 1.
DEFAULT_THRESHOLD = 0.5
MAX_REFERENCES = 8


def signature(frame: np.ndarray) -> np.ndarray:
    """Zero-mean, unit-norm downscaled grey signature.

    Decimated by plain slicing BEFORE the colour conversion and the
    area-average resize. Both of those cost time proportional to the pixels
    they are handed, and on a 3440x1440 frame that was 97ms of every tick —
    for a 24x14 thumbnail. Slicing is a view, so it is free, and dropping
    three quarters of the rows and columns cannot change a 24x14 average
    enough to matter.
    """
    step = max(1, min(frame.shape[0] // (SIG_H * 8),
                      frame.shape[1] // (SIG_W * 8)))
    small = frame[::step, ::step]
    grey = (cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3
            else small)
    sig = cv2.resize(grey, (SIG_W, SIG_H),
                     interpolation=cv2.INTER_AREA).astype(np.float32)
    sig -= float(sig.mean())
    norm = float(np.linalg.norm(sig))
    return sig / norm if norm > 1e-6 else sig


def load_references(gate_dir: Path = GATE_DIR) -> list[np.ndarray]:
    refs = []
    if gate_dir.is_dir():
        for p in sorted(gate_dir.glob("*.png")):
            img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if img is not None and img.shape == (SIG_H, SIG_W):
                refs.append(signature(img))
    return refs


def score(frame: np.ndarray, refs: list[np.ndarray]) -> float:
    """1 - correlation with the closest reference (lower = more
    draft-screen-like); inf when no references exist yet."""
    if not refs:
        return float("inf")
    sig = signature(frame)
    return min(1.0 - float(np.sum(sig * ref)) for ref in refs)


def is_draft_screen(frame: np.ndarray, refs: list[np.ndarray],
                    threshold: float = DEFAULT_THRESHOLD) -> bool:
    return score(frame, refs) <= threshold


def save_reference(frame: np.ndarray, gate_dir: Path = GATE_DIR) -> Path | None:
    """Store this frame's signature as a gate reference, skipping near
    duplicates and capping the set. Called when recognition confirms a draft
    screen. Stored as an 8-bit image purely for inspectability; loading
    re-normalises, so quantisation scale doesn't matter."""
    refs = load_references(gate_dir)
    if refs and score(frame, refs) < 0.02:
        return None  # effectively identical to an existing reference
    if len(refs) >= MAX_REFERENCES:
        return None
    gate_dir.mkdir(parents=True, exist_ok=True)
    sig = signature(frame)
    span = max(float(np.ptp(sig)), 1e-6)
    stored = np.clip((sig - float(sig.min())) / span * 255, 0, 255)
    path = gate_dir / f"ref_{len(refs):02d}.png"
    cv2.imwrite(str(path), stored.astype(np.uint8))
    return path
