"""Portrait hash library — MANY entries TO ONE hero id, because personas and
arcanas change portraits. Empty-slot appearances are entries too, mapped to
EMPTY_SLOT, so 'no pick yet' is recognised the same way heroes are.

Sources, all ingested by rebuild():
  assets/portraits/base/<hero_id>_<name>.png       downloaded base portraits
  assets/portraits/variants/<hero_id>/*.png        arcana/persona images and
                                                   crops harvested from real
                                                   frames (self-training)
  assets/portraits/variants/empty/*.png            empty-slot appearances

The library is rebuilt from these folders whenever hashing parameters change
(cheap: a few hundred images), and cached to library.npz.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from ..config import PORTRAITS_DIR
from .phash import phash

EMPTY_SLOT = -1

BASE_DIR = PORTRAITS_DIR / "base"
VARIANTS_DIR = PORTRAITS_DIR / "variants"
LIBRARY_FILE = PORTRAITS_DIR / "library.npz"
PARAMS_FILE = PORTRAITS_DIR / "recognition_params.json"


@dataclass
class RecognitionParams:
    hash_size: int = 8
    # Max Hamming distance (fraction of bits) for the best match to count.
    max_distance_frac: float = 0.30
    # Min margin (fraction of bits) between best and best-other-hero match;
    # below this the slot is UNKNOWN rather than guessed. Unknown is a
    # legitimate state, not an error.
    min_margin_frac: float = 0.05
    # Grey-level stddev below which a crop is a flat placeholder (empty
    # slot), classified directly: pHash of a near-flat image is noise, so
    # hashing it would produce random matches.
    flat_std: float = 6.0
    # Shift search: pHash is not translation invariant, so each slot is also
    # hashed at windows offset by k * shift_frac (k in -steps..steps, both
    # axes) and the best distance per library entry wins. Absorbs residual
    # calibration error without loosening the distance ceiling.
    shift_frac: float = 0.03
    shift_steps: int = 2

    @property
    def bits(self) -> int:
        return self.hash_size ** 2

    @property
    def max_distance(self) -> int:
        return round(self.max_distance_frac * self.bits)

    @property
    def min_margin(self) -> int:
        return max(1, round(self.min_margin_frac * self.bits))


def load_params() -> RecognitionParams:
    if PARAMS_FILE.exists():
        return RecognitionParams(
            **json.loads(PARAMS_FILE.read_text(encoding="utf-8")))
    return RecognitionParams()


def save_params(params: RecognitionParams) -> None:
    PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PARAMS_FILE.write_text(json.dumps({
        "hash_size": params.hash_size,
        "max_distance_frac": params.max_distance_frac,
        "min_margin_frac": params.min_margin_frac,
    }, indent=2), encoding="utf-8")


@dataclass
class Library:
    bits: np.ndarray        # (N, hash_size**2) uint8
    hero_ids: np.ndarray    # (N,) int32; EMPTY_SLOT for empty-slot entries
    labels: list[str]       # entry provenance, e.g. "base/1_antimage.png"
    hash_size: int

    def __len__(self) -> int:
        return len(self.labels)


def _iter_source_images(base_dir: Path = BASE_DIR,
                        variants_dir: Path = VARIANTS_DIR):
    """Yields (hero_id, label, path) for every library source image."""
    if base_dir.is_dir():
        for p in sorted(base_dir.glob("*.png")) + sorted(base_dir.glob("*.jpg")):
            m = re.match(r"(\d+)_", p.name)
            if not m:
                raise ValueError(
                    f"{p}: base portraits must be named '<hero_id>_<name>.png'")
            yield int(m.group(1)), f"base/{p.name}", p
    if variants_dir.is_dir():
        for folder in sorted(variants_dir.iterdir()):
            if not folder.is_dir():
                continue
            if folder.name == "empty":
                hid = EMPTY_SLOT
            elif folder.name.isdigit():
                hid = int(folder.name)
            else:
                raise ValueError(
                    f"{folder}: variant folders must be a hero id or 'empty'")
            for p in sorted(folder.glob("*.png")) + sorted(folder.glob("*.jpg")):
                yield hid, f"variants/{folder.name}/{p.name}", p


def rebuild(hash_size: int, base_dir: Path = BASE_DIR,
            variants_dir: Path = VARIANTS_DIR,
            save_to: Path | None = LIBRARY_FILE) -> Library:
    bits_rows, ids, labels = [], [], []
    for hid, label, path in _iter_source_images(base_dir, variants_dir):
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"unreadable image in library sources: {path}")
        bits_rows.append(phash(img, hash_size))
        ids.append(hid)
        labels.append(label)
    if not bits_rows:
        raise FileNotFoundError(
            f"No portrait sources under {base_dir} / {variants_dir}. "
            "Restore the original portrait sources and builder to rebuild.")
    lib = Library(bits=np.stack(bits_rows), hero_ids=np.array(ids, np.int32),
                  labels=labels, hash_size=hash_size)
    if save_to is not None:
        save_to.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(save_to, bits=lib.bits, hero_ids=lib.hero_ids,
                            labels=np.array(labels), hash_size=hash_size)
    return lib


def newest_source(base_dir: Path = BASE_DIR,
                  variants_dir: Path = VARIANTS_DIR) -> float:
    """When the newest portrait on disk was written."""
    newest = 0.0
    for folder in (base_dir, variants_dir):
        if folder.is_dir():
            for path in folder.rglob("*.png"):
                newest = max(newest, path.stat().st_mtime)
    return newest


def load(path: Path = LIBRARY_FILE, expected_hash_size: int | None = None,
         base_dir: Path = BASE_DIR, variants_dir: Path = VARIANTS_DIR) -> Library:
    if not path.exists():
        raise FileNotFoundError(
            f"No portrait library at {path}; restore it from a backup or the original project.")
    z = np.load(path, allow_pickle=False)
    lib = Library(bits=z["bits"], hero_ids=z["hero_ids"],
                  labels=[str(x) for x in z["labels"]],
                  hash_size=int(z["hash_size"]))
    if expected_hash_size is not None and lib.hash_size != expected_hash_size:
        return rebuild(expected_hash_size)
    # A portrait added since the cache was written is a portrait nobody can
    # match. Crops learned by elimination and files dropped in by hand both
    # land in the variants folder and both used to sit there doing nothing
    # until somebody remembered to rebuild.
    if newest_source(base_dir, variants_dir) > path.stat().st_mtime:
        return rebuild(lib.hash_size, base_dir, variants_dir, path)
    return lib
