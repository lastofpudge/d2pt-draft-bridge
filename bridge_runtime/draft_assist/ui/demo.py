"""Fake state so the UI runs and iterates with no Dota, no network, no cache:
a plausible Dataset (procedurally generated deltas over real-ish hero names)
and a scripted draft that fills in over time.
"""

import time

import numpy as np

from ..data.store import Dataset

# Enough real names to make the demo readable; ids are arbitrary here.
DEMO_NAMES = [
    "Anti-Mage", "Axe", "Bane", "Bloodseeker", "Crystal Maiden", "Drow Ranger",
    "Earthshaker", "Juggernaut", "Mirana", "Morphling", "Shadow Fiend",
    "Phantom Lancer", "Puck", "Pudge", "Razor", "Sand King", "Storm Spirit",
    "Sven", "Tiny", "Vengeful Spirit", "Windranger", "Zeus", "Kunkka", "Lina",
    "Lion", "Shadow Shaman", "Slardar", "Tidehunter", "Witch Doctor", "Lich",
    "Riki", "Enigma", "Tinker", "Sniper", "Necrophos", "Warlock", "Beastmaster",
    "Queen of Pain", "Venomancer", "Faceless Void", "Wraith King",
    "Death Prophet", "Phantom Assassin", "Pugna", "Templar Assassin", "Viper",
    "Luna", "Dragon Knight", "Dazzle", "Clockwerk", "Leshrac", "Nature's Prophet",
    "Lifestealer", "Dark Seer", "Clinkz", "Omniknight", "Enchantress", "Huskar",
    "Night Stalker", "Broodmother", "Bounty Hunter", "Weaver", "Jakiro",
    "Batrider", "Chen", "Spectre", "Ancient Apparition", "Doom", "Ursa",
    "Spirit Breaker", "Gyrocopter", "Alchemist", "Invoker", "Silencer",
    "Outworld Destroyer", "Lycan", "Brewmaster", "Shadow Demon", "Lone Druid",
    "Chaos Knight", "Meepo", "Treant Protector", "Ogre Magi", "Undying", "Rubick",
    "Disruptor", "Nyx Assassin", "Naga Siren", "Keeper of the Light", "Io",
    "Visage", "Slark", "Medusa", "Troll Warlord", "Centaur Warrunner",
    "Magnus", "Timbersaw", "Bristleback", "Tusk", "Skywrath Mage", "Abaddon",
    "Elder Titan", "Legion Commander", "Techies", "Ember Spirit",
    "Earth Spirit", "Underlord", "Terrorblade", "Phoenix", "Oracle",
    "Winter Wyvern", "Arc Warden", "Monkey King", "Dark Willow", "Pangolier",
    "Grimstroke", "Hoodwink", "Void Spirit", "Snapfire", "Mars", "Dawnbreaker",
    "Marci", "Primal Beast", "Muerta", "Ringmaster", "Kez",
]

DEMO_ROLE_TAGS = {
    "Anti-Mage": ["Carry"], "Juggernaut": ["Carry"], "Sven": ["Carry"],
    "Storm Spirit": ["Carry", "Nuker"], "Puck": ["Nuker"],
    "Crystal Maiden": ["Support"], "Lion": ["Support", "Disabler"],
    "Axe": ["Initiator", "Durable"], "Tidehunter": ["Initiator", "Durable"],
}


def demo_dataset(seed: int = 3) -> Dataset:
    rng = np.random.default_rng(seed)
    hero_ids = list(range(1, len(DEMO_NAMES) + 1))
    n = len(hero_ids)
    index = {hid: i for i, hid in enumerate(hero_ids)}
    heroes = {hid: {"name": DEMO_NAMES[hid - 1],
                    "roles": DEMO_ROLE_TAGS.get(DEMO_NAMES[hid - 1], [])}
              for hid in hero_ids}
    baseline = 0.5 + rng.uniform(-0.04, 0.04, n)
    d_vs = rng.normal(0, 0.012, (n, n))
    d_vs = (d_vs - d_vs.T) / 2
    d_with = rng.normal(0, 0.008, (n, n))
    d_with = (d_with + d_with.T) / 2
    np.fill_diagonal(d_vs, 0)
    np.fill_diagonal(d_with, 0)
    return Dataset(hero_ids=hero_ids, index=index, heroes=heroes,
                   baseline=baseline, picks=np.full(n, 50_000),
                   delta_vs=d_vs.astype(np.float64),
                   delta_with=d_with.astype(np.float64),
                   meta={"pulled_at": time.time(), "demo": True,
                         "target_brackets": ["ANCIENT", "DIVINE"]})


class DemoDraft:
    """A draft that fills one slot every few seconds, with one slot left
    deliberately unknown to exercise honest-unknown display."""

    def __init__(self, ds: Dataset, step_seconds: float = 4.0):
        rng = np.random.default_rng(int(time.time()) % 10_000)
        # Degrade gracefully on a dataset with fewer than ten heroes (an
        # empty one before the first download) instead of raising.
        n = min(10, len(ds.hero_ids))
        picks = (rng.choice(ds.hero_ids, size=n, replace=False)
                 if n else np.zeros(0, dtype=int))
        self.radiant = [int(x) for x in picks[:5]]
        self.dire = [int(x) for x in picks[5:]]
        self.unknown_dire_slot = 4      # last dire slot never resolves
        self.step_seconds = step_seconds
        self.started = time.monotonic()

    def current(self) -> tuple[list[int], list[int], int]:
        steps = int((time.monotonic() - self.started) / self.step_seconds)
        # Alternate picks radiant/dire like a real AP draft.
        radiant = self.radiant[:min(5, (steps + 1) // 2)]
        dire_n = min(5, steps // 2)
        dire = [h for i, h in enumerate(self.dire[:dire_n])
                if i != self.unknown_dire_slot]
        unknown = 1 if dire_n > self.unknown_dire_slot else 0
        return radiant, dire, unknown
