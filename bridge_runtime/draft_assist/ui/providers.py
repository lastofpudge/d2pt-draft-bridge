"""Draft state providers: the UI polls one of these on a timer and doesn't
care whether the state comes from live capture (Windows), replayed frames on
disk, or the scripted demo — which is what keeps the whole interface
iterable with no game running.
"""

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..capture import session as session_mod
from ..vision.lineup import TEAM_SIZE
from ..capture.window import DOTA_TITLE
from ..gsi.state import DRAFTING_STATES
from ..data.store import Dataset
from ..vision.library import EMPTY_SLOT
from ..vision.recognize import DraftRead
from .demo import DemoDraft
from .manual import ManualDraft, merge

# How often the "why is Dota silent" diagnosis is recomputed. It reads
# Steam's config off disk and opens a socket, so not on every tick.
DIAGNOSE_PERIOD = 20.0
# Checks whose failure is NOT something to put a banner up about. Dota
# being closed is silence with nothing wrong, and so is a match not having
# started: both are normal, and a banner that is up all evening is one
# nobody reads on the night it matters.
NOT_A_FAULT = frozenset({"Dota is running", "Payloads received"})


@dataclass
class Snapshot:
    left: list[int] = field(default_factory=list)    # left bank hero ids
    right: list[int] = field(default_factory=list)   # right bank hero ids
    unknown: int = 0
    mode: str = "idle"          # idle | draft | forced | demo | replay
    frame: np.ndarray | None = None
    read: DraftRead | None = None            # stabilised
    read_raw: DraftRead | None = None        # per-frame, for the debug view
    gate_score: float = float("inf")
    stalled: bool = False
    # How long the captured PICTURE has been identical. `stalled` says a
    # frame did not ARRIVE; this says the one that did was the same one
    # again - a dead capture that looks perfectly healthy.
    frozen_for: float = 0.0
    frames_arrived: int = 0
    source: str = ""
    warning: str = ""
    # GSI-specific: what the game itself reported, and what it could not.
    game_state: str = ""
    # True when left/right already MEAN ally/enemy (game data or manual
    # entry), so the user must never be asked which side they are on.
    sides_known: bool = False
    player_name: str = ""
    my_team: str = ""
    gsi_live: bool = False
    # The feed is silent AND the reason is something the user has to fix —
    # a missing config, the wrong port, no launch option. Dota simply not
    # being open is silence too, and is not a fault, so it does not set
    # this: a banner that is up whenever the game is closed is a banner
    # nobody reads when it matters.
    gsi_setup_broken: bool = False
    gsi_notes: list[str] = field(default_factory=list)
    gsi_capabilities: dict = field(default_factory=dict)
    # Which part of the feed the line-ups came out of, so the
    # UI can say so rather than leave it looking like magic.
    lineup_source: str = ""
    # The ten heroes are known but the split between them is
    # inferred; the UI offers a swap rather than asserting it.
    sides_certain: bool = True
    match_id: str = ""
    needs_manual: bool = False
    # The calibrated crop boxes are demonstrably NOT on the portraits: the
    # game named the ten heroes and the boxes could not be matched to any
    # of them. A fault the user can fix, so the banner says so — see
    # `_resolve_sides_by_sight`.
    crop_boxes_wrong: bool = False
    # WHAT THE VISION PIPELINE IS DOING, for the status line. With the
    # game feed filling the slots at strategy time, nothing on screen
    # distinguished "recognition read the board" from "recognition never
    # ran" - and the portrait search takes SECONDS on a worker, which is
    # long enough to close the app during: "I may close it before it's
    # done". A long job with no sign of life reads as a hung one.
    vision_note: str = ""


class DemoProvider:
    def __init__(self, ds: Dataset):
        self.ds = ds
        self.draft = DemoDraft(ds)

    def start(self) -> str:
        return "demo draft (restarts when it completes)"

    def stop(self) -> None:
        pass

    def set_forced(self, forced: bool) -> None:
        pass

    def poll(self) -> Snapshot:
        left, right, unknown = self.draft.current()
        if len(left) == 5 and len(right) + unknown == 5:
            if time.monotonic() - self.draft.started > 60:
                self.draft = DemoDraft(self.ds)
        return Snapshot(left=left, right=right, unknown=unknown,
                        mode="demo", source="demo")


class SessionProvider:
    """Shared logic for live capture and replay: both run a CaptureSession,
    one fed by Windows Graphics Capture, the other by frames from disk."""

    def __init__(self, session):
        self.session = session

    def set_forced(self, forced: bool) -> None:
        self.session.set_forced(forced)

    def set_required(self, required: bool) -> None:
        self.session.set_required(required)

    def stop(self) -> None:
        self.session.stop()

    def poll(self) -> Snapshot:
        state = self.session.tick()
        read = state.last_read
        snap = Snapshot(
            mode=("forced" if state.forced else
                  "draft" if state.mode == "active" else "idle"),
            frame=state.last_frame, read=read,
            read_raw=state.last_read_raw,
            gate_score=state.gate_score, stalled=state.stalled,
            frozen_for=state.frozen_for,
            frames_arrived=state.frames_arrived)
        if read is not None:
            snap.left = read.team_ids("radiant")
            snap.right = read.team_ids("dire")
            snap.unknown = read.unknown_count()
        return snap


class LiveProvider(SessionProvider):
    """Live Windows Graphics Capture, bound to a chosen window.

    Binding failure is NOT fatal: the app opens anyway with the capture
    source unbound so the user can pick a window from the Debug tab. Dying
    at startup hides the one fact that explains most apparent recognition
    failures — which window is actually being captured.
    """

    # How often to look for the Dota window again when not bound to it.
    REBIND_EVERY = 3.0

    def __init__(self, session, title: str | None = None):
        super().__init__(session)
        self.requested_title = title
        self.error = ""
        self._last_rebind = 0.0

    def start(self) -> str:
        return self.rebind(self.requested_title)

    def rebind(self, title: str | None) -> str:
        """(Re)bind capture; title None means 'find the Dota client'.
        Never raises — the message it returns is shown in the UI."""
        try:
            bound = self.session.start(title)
            self.error = ""
            return f"capturing window '{bound}'"
        except Exception as exc:  # binding failure must not kill the app
            self.error = str(exc)
            first_line = str(exc).splitlines()[0]
            return f"capture not bound — {first_line}"

    def available_sources(self) -> list[str]:
        from ..capture.window import list_window_titles
        titles = list_window_titles()
        # Surface the Dota client first when present; it is the only one
        # that is ever bound without the user asking.
        return ([DOTA_TITLE] if DOTA_TITLE in titles else []) + \
            [t for t in titles if t != DOTA_TITLE]

    def poll(self) -> Snapshot:
        self._rebind_to_dota_if_possible()
        snap = super().poll()
        title = self.session.capture_title
        if title:
            snap.source = f"capturing '{title}'"
            if title != DOTA_TITLE:
                snap.warning = (f"captured window is '{title}', NOT the Dota "
                                "client — recognition results are meaningless")
        else:
            snap.source = "waiting for the Dota window"
            snap.warning = ("Dota is not running, or not in borderless "
                            "windowed mode — capture binds itself as soon "
                            "as its window appears")
        return snap

    def _rebind_to_dota_if_possible(self) -> None:
        """Bind to Dota the moment its window exists.

        A real session spent a whole draft bound to a File Explorer window
        called "Dota_Draft_Assist" and captured nothing useful, because
        binding happened once at startup — before Dota was running — and
        never again. The user should never have to pick a window: Dota's
        title is exact, so when it appears it is unambiguous.
        """
        if self.requested_title is not None:
            return                      # the user asked for a specific one
        if self.session.capture_title == DOTA_TITLE:
            return
        now = time.monotonic()
        if now - self._last_rebind < self.REBIND_EVERY:
            return
        self._last_rebind = now
        from ..capture.window import find_dota_window_title
        try:
            if find_dota_window_title() is None:
                return
        except Exception:
            return                      # not Windows, or enumeration failed
        self.rebind(None)


class ReplayProvider(SessionProvider):
    def __init__(self, session, frames_dir: Path, period: float = 1.0):
        super().__init__(session)
        self.paths = (sorted(frames_dir.glob("*.png"))
                      + sorted(frames_dir.glob("*.jpg")))
        if not self.paths:
            raise SystemExit(f"no frames in {frames_dir}")
        self.i = 0
        self.period = period
        self._next = 0.0

    def start(self) -> str:
        return f"replaying {len(self.paths)} frames"

    def poll(self) -> Snapshot:
        import cv2
        now = time.monotonic()
        if now >= self._next:
            self._next = now + self.period
            frame = cv2.imread(str(self.paths[self.i]), cv2.IMREAD_COLOR)
            self.i = (self.i + 1) % len(self.paths)
            if frame is not None:
                self.session.inject_frame(frame)
        snap = super().poll()
        snap.source = self.paths[(self.i - 1) % len(self.paths)].name
        return snap


class GsiProvider:
    """Draft state from Dota's own Game State Integration feed.

    This is the sanctioned data path: Dota volunteers JSON to a local port
    because a config file asks it to. Nothing is injected, no memory is
    read, no pixels are interpreted, and there is no per-frame compute.

    What the feed carries is discovered, not assumed. Whatever GSI reports
    is used; whatever it omits is filled from manually entered slots, and
    the Snapshot says which is which so the UI can be honest about it.
    """

    def __init__(self, dataset: Dataset, server,
                 manual: ManualDraft | None = None):
        self.ds = dataset
        self.server = server
        self.manual = manual if manual is not None else ManualDraft()
        self.last_state = None
        # A complete minimap line-up is kept for the rest of the match. One
        # recorded session read a correct split at 16s and a scrambled one
        # at 43s from the same game: after strategy time the minimap holds
        # real units, so the first good reading is the one to trust.
        self.latched: tuple[list[int], list[int]] | None = None
        self.latched_match = ""
        # A failed bind must be sticky, not a status message that scrolls
        # away: an unbound listener looks exactly like Dota being silent.
        self.bind_error = ""
        # The diagnosis behind "no data from Dota yet", refreshed rarely
        # because it reads Steam's config off disk and pokes the port.
        self._silence_reason = ""
        self._silence_is_a_fault = False
        self._diagnosed_at = 0.0

    def start(self) -> str:
        try:
            self.server.start()
            self.bind_error = ""
        except OSError as exc:
            self.bind_error = (
                f"port {self.server.port} is already in use — another copy "
                "of this app is probably already running. Close the other "
                "one and restart this one.")
            return f"could not open the GSI port {self.server.port}: {exc}"
        return f"listening for Dota game data on 127.0.0.1:{self.server.port}"

    def stop(self) -> None:
        self.server.stop()

    def set_forced(self, forced: bool) -> None:
        """No gate to override: the game tells us when a draft is happening."""

    def _why_silent(self) -> str:
        """WHICH link is broken, not a checklist to guess through.

        GSI has several independent requirements and no feedback when one
        is missing — Dota simply says nothing — so the symptom is the same
        four words whichever link is down, and the app was handing back a
        list of every step to try. `diagnose.run_checks` already tests each
        link separately; this is that answer in one line, and it names the
        launch option only when the launch option is the thing that is
        actually missing.

        Refreshed at most every DIAGNOSE_PERIOD seconds: it reads Steam's
        config off disk and opens a socket, which is not something to do
        four times a second for a line nobody is reading yet.
        """
        now = time.monotonic()
        if self._silence_reason and now - self._diagnosed_at < DIAGNOSE_PERIOD:
            return self._silence_reason
        self._diagnosed_at = now
        try:
            from ..gsi import diagnose
            checks = diagnose.run_checks(self.server)
            failing = [c for c in checks if c.ok is False]
            if failing:
                first = failing[0]
                self._silence_reason = (
                    f"{first.name}: {first.fix or first.detail}")
                self._silence_is_a_fault = first.name not in NOT_A_FAULT
            else:
                self._silence_reason = (
                    "every GSI check passes, so this is Dota being quiet — "
                    "it sends nothing from the main menu, only once you are "
                    "in a match")
                self._silence_is_a_fault = False
        except Exception:               # noqa: BLE001 - never worth a crash
            self._silence_reason = (
                "run Settings ▸ Game data ▸ Set up game data (GSI), add "
                "-gamestateintegration to Dota's launch options, and "
                "restart Dota")
            self._silence_is_a_fault = False
        return self._silence_reason

    def set_dataset(self, dataset: Dataset) -> None:
        """Take the dataset that has just been loaded from disk.

        THE DATASET IS CAPTURED AT CONSTRUCTION AND THE APP RELOADS IT
        UNDERNEATH. `MainWindow.reload_backend` re-read the statistics
        into its OWN `self.ds` and left the provider holding whatever
        existed when the window was built -- which on a fresh install is
        the EMPTY dataset, because there are no statistics until the
        wizard has fetched them.

        Everything on screen looked right, because everything on screen
        reads the window's copy: 126 heroes, a full suggestion strip,
        real scores. What reads the PROVIDER's copy is
        `_hero_id_by_internal_name`, so `name_to_id` was `{}` and every
        hero the minimap named came back unknown -- all ten of them, for
        the whole match, on every fresh install until the app was next
        restarted. Your own hero went on resolving throughout, because
        that one comes from `hero.id` in the payload and never touches
        the map, which is exactly why the board showed one hero and no
        line-up.
        """
        self.ds = dataset

    def poll(self) -> Snapshot:
        from ..gsi import state as gsi_state

        reception = self.server.snapshot()
        snap = Snapshot(mode="idle", source="game data (GSI)")
        snap.frames_arrived = reception.count
        snap.gsi_live = reception.live

        if self.bind_error:
            snap.warning = self.bind_error
            snap.needs_manual = True
            snap.sides_known = True
            snap.left = merge([], self.manual.entered("ally"))
            snap.right = merge([], self.manual.entered("enemy"))
            snap.mode = "manual" if not self.manual.is_empty else "idle"
            return snap

        if reception.payload is None:
            snap.sides_known = True
            snap.warning = "no data from Dota yet — " + self._why_silent()
            snap.gsi_setup_broken = self._silence_is_a_fault
            snap.needs_manual = True
            snap.left = merge([], self.manual.entered("ally"))
            snap.right = merge([], self.manual.entered("enemy"))
            snap.mode = "manual" if not self.manual.is_empty else "idle"
            return snap

        parsed = gsi_state.parse(reception.payload, self.ds)
        self.last_state = parsed
        snap.game_state = parsed.game_state
        snap.match_id = parsed.match_id
        snap.sides_known = True
        snap.player_name = parsed.my_name
        snap.my_team = parsed.my_team
        snap.gsi_notes = parsed.notes
        snap.gsi_capabilities = parsed.capabilities
        snap.source = f"game data (GSI) · {parsed.summary()}"

        allies, enemies = parsed.allies, parsed.enemies
        source = parsed.lineup_source
        if parsed.game_state.endswith("HERO_SELECTION") or (
                parsed.match_id and parsed.match_id != self.latched_match):
            self.latched, self.latched_match = None, parsed.match_id
        if (source == "minimap" and len(allies) + len(enemies) >= 10
                and self.latched is None):
            # The FIRST complete reading, and only the first. Re-latching on
            # every payload let the split and the order wobble tick to tick
            # for the whole of strategy time — one real recording showed the
            # reading changing eight times in thirty seconds, moving heroes
            # between teams while the user was looking at it.
            self.latched = (list(allies), list(enemies))
            self.latched_match = parsed.match_id
        elif self.latched is not None:
            allies, enemies = self.latched
            source = "minimap"
            # parsed.summary() counts what THIS payload carried, which is
            # your own hero and nothing else once strategy time has passed.
            # Printing that beside "(held)" read as "1 pick, held" when ten
            # are being held.
            held = len(allies) + len(enemies)
            snap.source = (f"game data (GSI) · {parsed.short_summary()} · "
                           f"{held} picks held from the draft")

        snap.lineup_source = source
        snap.sides_certain = (parsed.sides_certain if source != "minimap"
                              else False)
        snap.left = merge(allies, self.manual.entered("ally"))
        snap.right = merge(enemies, self.manual.entered("enemy"))
        snap.needs_manual = len(snap.left) + len(snap.right) < 9
        snap.mode = "draft" if parsed.drafting else (
            "manual" if not self.manual.is_empty else "idle")

        if not reception.live:
            snap.warning = ("Dota has stopped sending data (game closed, or "
                            "the -gamestateintegration launch option is "
                            "missing)")
        elif reception.rejected and not reception.count:
            snap.warning = reception.last_error
        return snap


class ManualProvider:
    """Draft entered entirely by hand — no game connection at all. Useful
    for planning a draft away from the client, and as the guaranteed
    fallback."""

    def __init__(self, manual: ManualDraft | None = None):
        self.manual = manual if manual is not None else ManualDraft()

    def start(self) -> str:
        return "manual entry — click the draft slots to fill them in"

    def stop(self) -> None:
        pass

    def set_forced(self, forced: bool) -> None:
        pass

    def poll(self) -> Snapshot:
        return Snapshot(left=self.manual.entered("ally"),
                        right=self.manual.entered("enemy"),
                        mode="manual", source="manual entry",
                        needs_manual=True, sides_known=True)


class HybridProvider:
    """Game data for the phase and your identity; the screen for the picks.

    This is the shape the evidence forces. Recordings of real matches show
    that during HERO_SELECTION the GSI feed names no hero anywhere at all —
    so a GSI-only app is blind for the whole of the draft, which is the
    only moment the advice is worth anything. Reading the screen is the one
    remaining source, and it is what the vision pipeline was built for.

    GSI does not become useless; it removes the two hardest parts of the
    vision problem:

    * WHEN. `game_state` says a draft is happening, so nothing has to be
      inferred from pixels about whether the pick screen is up. The gate
      stops being a guess.
    * WHOSE. `player.team_name` says which side you are on, so the left and
      right banks map to ally and enemy without ever asking the user.

    Precedence is strict, and never a blend: what the game reports outright
    (minimap line-ups) wins; the screen fills what the game did not report;
    hand-entered slots fill what the screen could not read. Whatever is
    still unknown stays unknown.
    """

    def __init__(self, gsi: "GsiProvider", vision: SessionProvider | None):
        self.gsi = gsi
        self.vision = vision
        self.manual = gsi.manual
        self.forced = False
        # Screen-read line-ups, latched per (match, the ten heroes): the
        # search path costs seconds, and the answer cannot change while the
        # same ten are on the same bar.
        self._sight: dict[tuple, object] = {}
        self._searched: set[tuple] = set()
        # The search runs on a WORKER, never on the caller. Measured on a
        # real 3440x1440 session it took 25.6 seconds in one tick, which is
        # the whole app frozen mid-draft. Shrinking the search grid cut
        # that to about three, and three is still a freeze, so the answer
        # arrives on a later tick instead of holding this one.
        self._search_lock = threading.Lock()
        self._search_done: dict[tuple, object] = {}
        self._search_running: tuple | None = None
        self._search_at: float = 0.0        # 0..1, for the status line
        # Set by a successful search so the UI can save the geometry it
        # measured: one search calibrates the boxes for good.
        self.measured_layout = None
        # The last match the screen was read for. A new one is the only
        # unambiguous "this board is over" the app gets — see `_new_match`.
        self._match = ""
        # WHICH SIDE THE SCREEN PUT EACH HERO ON, this match. The pick bar
        # is the one source that is CERTAIN about sides - Radiant is
        # always its left bank and the feed says which bank is yours - and
        # until now the app threw that away the moment strategy time
        # arrived. See `_orient_by_memory`.
        self._screen_side: dict[int, str] = {}

    def set_dataset(self, dataset: Dataset) -> None:
        """Hand the reloaded dataset to the half that reads it."""
        self.gsi.set_dataset(dataset)

    # The Debug tab and the capture menu reach for these.
    @property
    def session(self):
        return getattr(self.vision, "session", None)

    def start(self) -> str:
        message = self.gsi.start()
        if self.vision is not None:
            bound = self.vision.start()
            return f"{message}; {bound}"
        return message

    def stop(self) -> None:
        self.gsi.stop()
        if self.vision is not None:
            self.vision.stop()

    def set_forced(self, forced: bool) -> None:
        self.forced = forced
        if self.vision is not None:
            self.vision.set_forced(forced)

    def rebind(self, title: str | None) -> str:
        if self.vision is None or not hasattr(self.vision, "rebind"):
            return "screen capture is not available"
        return self.vision.rebind(title)

    def available_sources(self) -> list[str]:
        if self.vision is None or not hasattr(self.vision, "available_sources"):
            return []
        return self.vision.available_sources()

    def _resolve_sides_by_sight(self, snap) -> None:
        """Put the game's ten heroes into their positions on the pick bar.

        The minimap is reliable about WHICH ten and unreliable about whose
        five are whose; the screen is the other way round, because Radiant
        is always the left bank and `player.team_name` says which bank is
        yours. Neither source alone settles it and together they do, so this
        is a combination rather than a preference — hence the source name.

        Silent on failure by design: a bad reading leaves the minimap's
        guess exactly as it was, which is what the user was already
        correcting by hand.
        """
        ten = list(snap.left) + list(snap.right)
        if len(ten) != 10 or snap.my_team not in ("radiant", "dire"):
            return
        key = (snap.match_id or "", frozenset(ten))
        read = self._sight.get(key)
        if read is None:
            read = self._read_or_start_search(key, snap, ten)
        if read is None:
            return
        allies, enemies = read.sides_for(snap.my_team)
        snap.left = merge(allies, self.manual.entered("ally"))
        snap.right = merge(enemies, self.manual.entered("enemy"))
        snap.sides_certain = True
        snap.lineup_source = "minimap+screen"
        snap.source = (f"game data + screen · ten heroes from the game, "
                       f"sides read off the pick bar ({read.how})")

    def _orient_by_memory(self, snap) -> None:
        """Put the minimap's ten onto the sides the SCREEN already read.

        THE LAST RESORT BEFORE A COIN FLIP, and it answers the complaint
        it was built from: "sometimes at the end when drafting phase is
        over and everyone is locked in, the app gets 1 person on each team
        and switches them to the other team."

        `_split_by_lane_pairs` produces a clean five a side and cannot say
        WHICH half is yours; that is a coin flip this project has watched
        land wrong on real matches more than once. Meanwhile the screen
        spent the whole of hero selection reading the pick bar, where
        Radiant IS the left bank and there is nothing to guess - and all
        of it was discarded the moment strategy time arrived.

        Measured on the reported draft of 2026-10-10: at 22.4s the screen
        had allies Snapfire, Pudge, Mars, Phantom Lancer, Crystal Maiden;
        at 35.0s the minimap replaced them with Snapfire, Queen of Pain,
        Juggernaut, Necrophos, Undying. Four of the five moved to the
        enemy team. Scored against what the screen had read, the half the
        app chose agreed on 1 and contradicted on 3, and the half it threw
        away agreed on 4 and contradicted on 1.

        PER HERO, NOT PER HALF, because the right answer there was not
        either half: the player's own hero came from one side of its lane
        pair and the other four from the other. Nothing about the halves
        is consistently ordered, which is exactly what this project has
        recorded about object index all along - so what is honoured is
        each hero the screen actually saw, and the minimap is left to
        place only the heroes it never did.

        Declines rather than guessing whenever the memory cannot be made
        to fit five a side: a wrong split asserted confidently is worse
        than a coin flip the user is already correcting by hand.
        """
        ten = list(snap.left) + list(snap.right)
        if len(ten) != 2 * TEAM_SIZE or not self._screen_side:
            return
        seen = {hero: self._screen_side[hero] for hero in ten
                if hero in self._screen_side}
        if not seen:
            return
        allies = [hero for hero in ten if seen.get(hero) == "ally"]
        enemies = [hero for hero in ten if seen.get(hero) == "enemy"]
        if len(allies) > TEAM_SIZE or len(enemies) > TEAM_SIZE:
            return               # the screen contradicts itself: decline
        # A hero the GAME calls yours is never on the other side. That
        # outranks the screen, as it does everywhere else in this file.
        if snap.my_hero_id and snap.my_hero_id in enemies:
            return
        # Everything the screen never placed keeps the side the minimap
        # gave it, unless that side is already full.
        was_ally = set(snap.left)
        for hero in ten:
            if hero in allies or hero in enemies:
                continue
            first = allies if hero in was_ally else enemies
            second = enemies if first is allies else allies
            (first if len(first) < TEAM_SIZE else second).append(hero)
        if len(allies) != TEAM_SIZE or len(enemies) != TEAM_SIZE:
            return
        if set(allies) == was_ally:
            return               # the minimap already agreed; say nothing
        snap.left = merge(allies, self.manual.entered("ally"))
        snap.right = merge(enemies, self.manual.entered("enemy"))
        snap.sides_certain = True
        snap.lineup_source = "minimap+screen"
        snap.source = (f"game data + screen · ten heroes from the game, "
                       f"sides from the {len(seen)} the screen read while "
                       f"you were picking")
        snap.gsi_notes = list(getattr(snap, "gsi_notes", []) or []) + [
            f"the minimap's split disagreed with the pick bar on "
            f"{len(was_ally - set(allies))} hero(es); the screen was read "
            f"during hero selection, where Radiant is always the left "
            f"bank, so it wins"]

    def _read_or_start_search(self, key, snap, ten):
        """The cheap read now; the expensive one on a worker.

        `read_placed` is a hundred small correlations against calibrated
        boxes — microseconds, and it is tried on every tick. The SEARCH
        behind it hunts ten portraits across the top strip at an unknown
        scale, and on a real 3440x1440 session that was 25.6 seconds inside
        one tick with the window frozen. So it is started once per (match,
        the ten) and its answer is picked up on whichever later tick it is
        ready — the caller keeps the guess it had in the meantime, which is
        what it would have had anyway.
        """
        from ..vision import lineup as lineup_mod
        with self._search_lock:
            done = self._search_done.pop(key, None)
        if done is not None:
            if done.ok:
                self._sight[key] = done
                self._remember_measured_layout(done, snap)
                return done
            snap.gsi_notes = list(snap.gsi_notes) + [
                f"sides not readable from the screen: {done.note}"]
            return None

        if snap.frame is None:
            return None
        # THE BAR HAS TO BE ON THE SCREEN BEFORE IT CAN BE READ OFF IT.
        # The minimap's ten are LATCHED for the match, so this method goes
        # on being called through PRE_GAME and the whole game — with the
        # draft bar long gone and the in-game top bar in its place, which
        # is a different size at different coordinates. Every one of those
        # ticks failed, and each one raised a banner saying the app could
        # not find the pick portraits: perfectly true, and about a screen
        # that has none. Dota draws the pick bar in the drafting states
        # and that is the only place this question can be asked.
        if snap.game_state not in DRAFTING_STATES:
            return None
        layout = getattr(getattr(self.vision, "session", None), "layout", None)
        placed = lineup_mod.read_lineup(snap.frame, ten, layout,
                                        allow_search=False)
        if placed.ok:
            self._sight[key] = placed
            return placed
        # A DEFINITE VERDICT ON THE CALIBRATION, and the only one this app
        # ever gets. Everywhere else the boxes being wrong looks like
        # recognition being unlucky — but here the GAME has just named the
        # ten heroes that are on the screen, so a box that matches none of
        # them is not on a portrait. The whole draft is read through those
        # boxes, so this is raised where the user will see it rather than
        # left in the recording's notes, which is where it sat while a
        # real draft read two of ten slots for eighty seconds.
        #
        # `read_placed` decides it, not a search of its own message for a
        # phrase: it is the one place that knows how many boxes DID hold a
        # hero the game named, and nine of ten matching is a hero in a
        # costume rather than a calibration fault.
        # AND NOT WHEN THE PICTURE IS FROZEN. `boxes_wrong` means "the
        # game named ten heroes and too few of the boxes hold one", which
        # is proof about the GEOMETRY only while the frame is a live
        # picture of the draft. A capture stuck on the last thing it saw
        # fails every box for a reason that has nothing to do with where
        # they are - and the report that brought this in said "the app
        # could not find the hero portraits on screen" about boxes that
        # were sitting exactly where they should be, on a three-minute-old
        # still of the Dota main menu.
        if placed.boxes_wrong and snap.frozen_for < session_mod.FROZEN_AFTER:
            snap.crop_boxes_wrong = True

        with self._search_lock:
            if self._search_running is not None or key in self._searched:
                return None
            self._search_running = key
            self._search_at = 0.0
            self._searched.add(key)
        # The capture session overwrites its frame buffer, so the worker
        # gets a copy of its own rather than a view that changes underneath
        # a three-second correlation.
        frame = snap.frame.copy()
        threading.Thread(target=self._run_search, name="lineup-search",
                         args=(key, frame, list(ten), layout),
                         daemon=True).start()
        return None

    def _run_search(self, key, frame, ten, layout) -> None:
        """Worker body. Never raises into the thread: a failed search must
        cost the reading, never the app."""
        from ..vision import lineup as lineup_mod
        try:
            found = lineup_mod.read_lineup(
                frame, ten, layout, allow_search=True,
                progress=lambda share: setattr(self, "_search_at",
                                               float(share)))
        except Exception as exc:                # noqa: BLE001 - see above
            found = lineup_mod.ScreenLineup(note=f"search failed: {exc}")
        found.frame_shape = frame.shape[:2]
        with self._search_lock:
            self._search_done[key] = found
            self._search_running = None
            self._search_at = 1.0

    def vision_note(self, snap) -> str:
        """One short phrase: what the screen reader is doing right now.

        THREE STATES, and they are told apart because they fail for
        different reasons. A SEARCH is the slow one - seconds, on a
        worker - and it is the only thing here worth a percentage.
        Ordinary recognition is milliseconds a tick, so what matters
        there is how much of the board it got. And vision being switched
        off is neither of those and must not read as a failure.

        This exists because at strategy time the GAME fills the slots,
        so a board full of heroes says nothing whatever about whether
        recognition ran - and the one job that takes long enough to
        interrupt had nothing on screen at all.
        """
        # `self.vision is None` is how this class says vision is off.
        # An earlier draft asked for `use_vision`, which nothing defines -
        # the same trap as `frame_of` reaching for a `last_frame` no
        # provider ever had, and it raised on every tick.
        if self.vision is None:
            return ""
        with self._search_lock:
            running = self._search_running is not None
            share = self._search_at
        if running:
            return f"reading portraits {share:.0%}"
        # NOTHING HERE MAY RAISE. This is one cosmetic segment of the
        # status line, written four times a second from inside the
        # refresh loop - so a shape it did not expect must cost the
        # sentence, never the tick. `read` is whatever a provider chose
        # to publish, which is not always a `DraftRead`.
        read = snap.read or snap.read_raw
        slots = getattr(read, "slots", None) if read is not None else None
        if not slots:
            return "screen: not reading"
        total = len(slots)
        known = sum(1 for slot in slots
                    if getattr(slot, "hero_id", None) is not None
                    and getattr(slot, "hero_id", None) != EMPTY_SLOT)
        # DONE IS SAID OUTRIGHT. "10/10" is the same shape as "3/10" at a
        # glance, and the whole point is being able to tell without
        # counting whether it is safe to close the window.
        if known == total:
            return f"screen: all {total} read"
        return f"screen: {known}/{total} read"


    def _remember_measured_layout(self, read, snap) -> None:
        """A successful SEARCH has already measured the crop boxes.

        It found every portrait's position and size to do its job, so the
        geometry is free — and throwing it away is why the search kept
        running: the cheap path needs calibrated boxes and the boxes were
        never calibrated. `MainWindow` picks this up and saves it, after
        which the search never runs again on this machine.

        **ONLY FROM ALL TEN.** The SIDES survive a portrait the search
        missed, because the game named all ten and the one not located is
        the hero left over — elimination, not a guess. The GEOMETRY does
        not: `layout_from` reads a bank's origin off the first portrait IN
        it, so a miss at the start of a bank shifts that whole bank one
        pitch and every box after it. A measurement is adopted when there
        is no calibration file at all, so that would bake a shifted layout
        into a fresh install and the cheap path would be wrong for every
        match after it. Nine portraits answer the sides; it takes ten to
        answer where the boxes go.
        """
        from ..vision import lineup as lineup_mod
        if (read.how != "searched"
                or len(read.found) != 2 * lineup_mod.TEAM_SIZE):
            return
        shape = getattr(read, "frame_shape", None) or (
            snap.frame.shape[:2] if snap.frame is not None else None)
        if shape is None:
            return
        from ..vision import autocal
        height, width = shape
        base = getattr(getattr(self.vision, "session", None), "layout", None)
        result = autocal.layout_from(read.found, width, height, base)
        if result.ok:
            self.measured_layout = result

    def poll(self) -> Snapshot:
        """Stamped in ONE place, because `_poll` has five exits.

        Setting the note at each return is five chances to add a sixth
        and forget - and the one that got forgotten would be a state
        where the line silently says nothing, which is exactly the
        failure this field exists to remove.
        """
        snap = self._poll()
        snap.vision_note = self.vision_note(snap)
        return snap

    def _poll(self) -> Snapshot:
        snap = self.gsi.poll()
        if self.vision is None:
            return snap

        # The GAME says whether a draft is on screen, so the gate does not
        # have to guess. Its references are harvested from whichever screen
        # confirmed first, and one real session sat at 0.707 against a 0.50
        # threshold for the whole of hero selection — recognising nothing
        # until every pick was already in.
        require = getattr(self.vision, "set_required", None)
        if require is not None:
            # THREE answers, not two. A silent feed is not the game saying
            # "no draft" — it is the game saying nothing, and passing that
            # on as False put the gate back in sole charge, which is how a
            # ranked game with GSI down read four heroes and then went
            # blind for the rest of the draft. None lets the session probe.
            require(snap.game_state in DRAFTING_STATES
                    if snap.game_state else None)

        self._new_match(snap)
        return self._add_screen(snap, self.vision.poll())

    # Dota reports "0" for the match id outside a match, so an id is only
    # a new match when it is a real one. Blank is Dota saying nothing,
    # which is never evidence that anything ended.
    @staticmethod
    def _real_match(match_id: str) -> bool:
        return bool(match_id) and match_id != "0"

    def _new_match(self, snap) -> None:
        """A new match id means the screen's picks belong to a dead game.

        The capture session forgets its reading after thirty seconds of
        not-the-draft-screen, and that timer says in its own comment what
        it is standing in for: "thirty seconds of not-the-draft-screen is
        a different game". The match id IS that, with no wait — and the
        wait is not free. A real session opened on the PREVIOUS match's
        full ten heroes and showed them for the first nine seconds of a
        new draft, which is nine seconds of advice about a board that no
        longer existed, at the one moment the first pick is being made.

        Only the screen's memory goes. Everything else keyed to a match
        already keys on it: `_sight`, `_searched` and `_search_done` are
        all looked up by `(match, the ten)`, and `GsiProvider` clears its
        own minimap latch on the same change.
        """
        if not self._real_match(snap.match_id):
            return
        if snap.match_id == self._match:
            return
        was, self._match = self._match, snap.match_id
        if not was:
            return                  # the first match of the session
        self._screen_side.clear()
        forget = getattr(self.session, "forget_reading", None)
        if forget is not None:
            forget()

    def _add_screen(self, snap, screen):
        """Fold what the SCREEN saw into what the game already said.

        Its own method because it is the half that can be tested without
        a Dota window or a GSI listener - and because the bug it exists
        to prevent (your own hero disappearing when a team-mate picked)
        was invisible from the outside and obvious once this could be
        driven directly.
        """
        # Always carry the frame and the read: the Debug tab is how a
        # recognition problem gets diagnosed, and it must show what the app
        # is looking at even when the picks came from somewhere else.
        snap.frame = screen.frame
        snap.read = screen.read
        snap.read_raw = screen.read_raw
        snap.gate_score = screen.gate_score
        snap.stalled = screen.stalled
        snap.frozen_for = getattr(screen, "frozen_for", 0.0)
        snap.frames_arrived = max(snap.frames_arrived, screen.frames_arrived)
        if screen.warning and not snap.warning:
            snap.warning = screen.warning

        if snap.lineup_source == "minimap" and not snap.sides_certain:
            self._resolve_sides_by_sight(snap)
        if snap.lineup_source == "minimap" and not snap.sides_certain:
            self._orient_by_memory(snap)
        if snap.lineup_source:
            return snap                      # the game told us outright

        # WHAT THE GAME ALREADY SAID, KEPT. During HERO_SELECTION the feed
        # names exactly one hero - YOURS, once you have locked it in - and
        # that is not a line-up, so `lineup_source` is empty and the guard
        # above does not fire. Everything below then REPLACED snap.left
        # with the screen's reading, and a real draft went:
        #
        #     5.6s   none    allies=Rubick          the game named you
        #    17.3s   screen  allies=Disruptor       Rubick GONE
        #    33.3s   screen  allies=Disruptor, Rubick   back, 16s later
        #
        # The user watched their own hero vanish the moment a team-mate
        # picked, and it only returned when recognition happened to see
        # Rubick's portrait too - a quarter of the draft later.
        #
        # This is not the "never blended" rule giving way. That rule is
        # about LINE-UPS, where two complete answers disagreeing must not
        # be averaged into a third nobody reported. Here the game has not
        # reported a line-up; it has stated ONE FACT, and the screen
        # failing to see that hero is a gap in the screen rather than a
        # contradiction of the game.
        # THE GAME'S ALONE, with the hand-entered ones taken back out.
        # `snap.left` at this point is already game+manual, so keeping it
        # whole put TYPED heroes ahead of the screen - inverting the
        # documented order, which is game > screen > hand entry. Caught
        # by a test that had asserted that order for years.
        typed_ally = set(self.manual.entered("ally"))
        typed_enemy = set(self.manual.entered("enemy"))
        known_allies = [hero for hero in snap.left if hero not in typed_ally]
        known_enemies = [hero for hero in snap.right
                         if hero not in typed_enemy]

        read = screen.read
        if read is None:
            return snap

        radiant, dire = read.team_ids("radiant"), read.team_ids("dire")
        if not radiant and not dire:
            # Nothing seen. `snap.left` still holds what the game said,
            # which is the whole point of not having overwritten it.
            return snap
        # my_team is what makes the banks mean ally and enemy. Without it
        # the sides are still a question, so say so rather than pick one.
        if snap.my_team == "dire":
            allies, enemies = dire, radiant
        elif snap.my_team == "radiant":
            allies, enemies = radiant, dire
        else:
            snap.sides_known = False
            allies, enemies = radiant, dire

        # THE GAME'S IN FRONT, then the screen, then hand entry - the
        # order `merge` already uses, one source deeper.
        allies = merge(known_allies, allies)
        # AND A HERO THE GAME CALLS YOURS IS NEVER ON THE OTHER SIDE.
        # Recognition can put a portrait in the wrong bank; the feed
        # saying which hero is yours cannot be wrong about that, so the
        # enemy list gives way rather than the board showing one hero
        # twice.
        enemies = [hero for hero in merge(known_enemies, enemies)
                   if hero not in known_allies]

        snap.left = merge(allies, self.manual.entered("ally"))
        snap.right = merge(enemies, self.manual.entered("enemy"))
        snap.unknown = read.unknown_count()
        snap.lineup_source = ("screen" if not known_allies and not known_enemies
                              else "game data + screen")
        # KEPT FOR STRATEGY TIME. This is the moment the sides are known
        # for certain, and it is thrown away a minute later unless it is
        # written down now.
        for hero in snap.left:
            self._screen_side[hero] = "ally"
        for hero in snap.right:
            self._screen_side.setdefault(hero, "enemy")
        snap.needs_manual = len(snap.left) + len(snap.right) < 9
        snap.mode = "forced" if self.forced else (
            "draft" if snap.game_state or snap.left or snap.right else "idle")
        return snap
