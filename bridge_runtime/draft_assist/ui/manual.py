"""Manually entered draft slots.

GSI hands over your own hero, team, match and game state, but the enemy
line-up is very probably not in a player's own feed (the `draft` component
is a spectator one). Rather than fall back to guessing at pixels, the app
lets you click the picks in — five clicks during a draft, with the hero list
filtered as you type.

Manual entries are an OVERLAY, not a replacement: anything the game reports
wins, and manual slots fill the rest. So as soon as a payload proves GSI
does carry more, those slots stop needing to be typed and nothing else has
to change.
"""

from dataclasses import dataclass, field


@dataclass
class ManualDraft:
    allies: list[int | None] = field(default_factory=lambda: [None] * 5)
    enemies: list[int | None] = field(default_factory=lambda: [None] * 5)

    def clear(self) -> None:
        self.allies = [None] * 5
        self.enemies = [None] * 5

    def set_slot(self, side: str, index: int, hero_id: int | None) -> None:
        slots = self.allies if side == "ally" else self.enemies
        if 0 <= index < len(slots):
            slots[index] = hero_id

    def entered(self, side: str) -> list[int]:
        slots = self.allies if side == "ally" else self.enemies
        return [h for h in slots if h is not None]

    # A TILE IS ADDRESSED BY ITS HERO, NEVER BY ITS POSITION. `entered`
    # drops the empties and `merge` puts the game's picks in front, so the
    # tile at position 2 on screen has no relationship to `allies[2]`.
    # Clearing "slot 0" therefore cleared a slot that was already empty and
    # the hero stayed put, and changing it wrote into that empty slot and
    # produced a SECOND hero beside the one being changed. Both are what
    # "I can't remove Slardar and I can't replace him" was.
    def slot_of(self, side: str, hero_id: int | None) -> int | None:
        """Which of the five holds this hero — None if we did not enter it.

        A hero the GAME reported is not ours to remove: it will be back on
        the next payload, so the caller says so rather than appearing to
        do nothing.
        """
        if hero_id is None:
            return None
        slots = self.allies if side == "ally" else self.enemies
        for index, held in enumerate(slots):
            if held == hero_id:
                return index
        return None

    def first_free(self, side: str) -> int | None:
        slots = self.allies if side == "ally" else self.enemies
        for index, held in enumerate(slots):
            if held is None:
                return index
        return None

    def replace(self, side: str, hero_id: int | None,
                new_id: int | None) -> bool:
        """Put `new_id` where `hero_id` is. False if that hero is not ours."""
        index = self.slot_of(side, hero_id)
        if index is None:
            return False
        self.set_slot(side, index, new_id)
        return True

    @property
    def is_empty(self) -> bool:
        return not (self.entered("ally") or self.entered("enemy"))


def merge(from_game: list[int], manual: list[int]) -> list[int]:
    """Game-reported picks first, manual entries after, no duplicates."""
    out = list(from_game)
    for hero_id in manual:
        if hero_id not in out:
            out.append(hero_id)
    return out[:5]
