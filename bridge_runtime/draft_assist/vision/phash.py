"""DCT perceptual hashing on numpy/cv2 — no OCR anywhere in hero
identification (see CLAUDE.md).

A portrait crop is greyscaled, resized to 4x the hash size, DCT'd, and the
top-left hash_size x hash_size block (minus the DC term) is thresholded at
its median into a bit vector. Hamming distance between bit vectors is the
match metric. hash_size is tunable (8 -> 64 bits, 16 -> 256 bits); the
proving ground picks the operating point.
"""

import cv2
import numpy as np


def phash(image_bgr: np.ndarray, hash_size: int = 8) -> np.ndarray:
    """Perceptual hash of a BGR (or grey) image -> uint8 bit vector of
    length hash_size**2."""
    if image_bgr.ndim == 3:
        grey = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    else:
        grey = image_bgr
    size = hash_size * 4
    resized = cv2.resize(grey, (size, size), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(resized.astype(np.float32))
    block = dct[:hash_size, :hash_size].flatten()
    block = block[1:]  # drop the DC term (overall brightness)
    median = np.median(block)
    bits = (block > median).astype(np.uint8)
    # Keep a fixed length of hash_size**2 by padding the dropped DC slot.
    return np.concatenate([[0], bits]).astype(np.uint8)


def hamming(a: np.ndarray, b: np.ndarray) -> int:
    return int(np.count_nonzero(a != b))


