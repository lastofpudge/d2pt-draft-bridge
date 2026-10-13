"""Disk cache for the built dataset. The live scoring loop reads only from
here and never makes network calls."""

import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..config import CACHE_MAX_AGE_HOURS, DATA_CACHE

MATRIX_FILE = DATA_CACHE / "dataset.npz"
META_FILE = DATA_CACHE / "dataset_meta.json"


@dataclass
class Dataset:
    hero_ids: list[int]              # sorted; row/col i belongs to hero_ids[i]
    index: dict[int, int]            # hero id -> row/col
    heroes: dict[int, dict]          # id -> {name, internal_name, img, icon}
    baseline: np.ndarray             # baseline winrate per hero (target bracket)
    picks: np.ndarray                # sample size behind each baseline
    delta_vs: np.ndarray             # NORMALISED matchup deltas (see normalize.py)
    delta_with: np.ndarray           # NORMALISED synergy deltas
    meta: dict

    def name(self, hero_id: int) -> str:
        return self.heroes.get(hero_id, {}).get("name", f"hero {hero_id}")

    @property
    def is_empty(self) -> bool:
        """True before any data has been downloaded. The UI opens in this
        state rather than refusing to start, and offers to fetch data."""
        return not self.hero_ids

    def age_hours(self) -> float:
        return (time.time() - self.meta["pulled_at"]) / 3600

    def is_stale(self) -> bool:
        return self.age_hours() > CACHE_MAX_AGE_HOURS


def save(ds: Dataset) -> None:
    DATA_CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        MATRIX_FILE,
        hero_ids=np.array(ds.hero_ids, dtype=np.int32),
        baseline=ds.baseline.astype(np.float32),
        picks=ds.picks.astype(np.int64),
        delta_vs=ds.delta_vs,
        delta_with=ds.delta_with,
    )
    META_FILE.write_text(json.dumps(
        {**ds.meta, "heroes": {str(k): v for k, v in ds.heroes.items()}},
        indent=2), encoding="utf-8")


def load(path: Path = MATRIX_FILE) -> Dataset:
    if not path.exists() or not META_FILE.exists():
        raise FileNotFoundError(
            f"No dataset cache at {path}. Run `python tools/pull_data.py` "
            "first (needs network + STRATZ_API_KEY in .env).")
    z = np.load(path)
    meta = json.loads(META_FILE.read_text(encoding="utf-8"))
    heroes = {int(k): v for k, v in meta.pop("heroes").items()}
    hero_ids = [int(x) for x in z["hero_ids"]]
    return Dataset(
        hero_ids=hero_ids,
        index={hid: i for i, hid in enumerate(hero_ids)},
        heroes=heroes,
        baseline=z["baseline"].astype(np.float64),
        picks=z["picks"],
        delta_vs=z["delta_vs"].astype(np.float64),
        delta_with=z["delta_with"].astype(np.float64),
        meta=meta,
    )


def pair_only_brackets(path: Path = META_FILE) -> bool | None:
    """Did the last build have to use Stratz's PAIRED bracket enum?

    THREE-VALUED, and the third is why this is not a bool. True is
    "Stratz could not express single ranks, so the pairwise data covers
    a wider band than was asked for", False is "it filtered exactly",
    and None is "no build has ever said" — a fresh install with no
    dataset, an OpenDota-sourced one, or a cache written before this was
    recorded. Collapsing None into either would have the rank picker
    assert something nobody measured, which is the mistake this
    codebase's `required`, `Profile.known` and `KeyCheck.ok` all exist
    to avoid.

    Read off the META file alone: the matrices are megabytes and this is
    one key, and the rank picker is drawn long before anything wants the
    numbers.
    """
    try:
        meta = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    chosen = meta.get("stratz_bracket_filter")
    if not isinstance(chosen, dict) or "exact" not in chosen:
        return None
    return not bool(chosen["exact"])


def empty_dataset() -> Dataset:
    """A valid but heroless dataset, so the application can open and explain
    itself before the first download instead of crashing on a missing file."""
    return Dataset(
        hero_ids=[], index={}, heroes={},
        baseline=np.zeros(0), picks=np.zeros(0, dtype=np.int64),
        delta_vs=np.zeros((0, 0)), delta_with=np.zeros((0, 0)),
        meta={"pulled_at": 0.0, "target_brackets": [], "empty": True},
    )


def load_or_empty(path: Path = MATRIX_FILE) -> Dataset:
    try:
        return load(path)
    except (FileNotFoundError, KeyError, ValueError):
        return empty_dataset()
