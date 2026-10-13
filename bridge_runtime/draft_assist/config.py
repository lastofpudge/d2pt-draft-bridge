"""Paths and runtime configuration. The Stratz key is read from .env at
runtime, never hardcoded."""

import os
from pathlib import Path

from dotenv import load_dotenv

from . import debugdir

REPO_ROOT = Path(__file__).resolve().parent.parent

# THE APP'S NAME, SPELLED ONCE. It is the WINDOW TITLE, the application
# name, the Start-menu shortcut's name and the string a second launch
# searches for with `FindWindowW` to bring the first copy to the front —
# and that last one is why it may not be spelled twice: the search is an
# EXACT title match, so a rename in one place and not the other finds
# nothing and the second launch quietly does nothing at all, which is
# the one outcome the raise exists to prevent. The same rule the font
# family and the shortcut name already follow.
# It lives HERE rather than in `ui/` because `ui/single.py` runs before
# the QApplication and must not import anything that reaches Qt.
APP_NAME = "Dota Draft Assist"
# WHERE A PROBLEM REPORT GOES. A dedicated address for this app rather
# than the owner's own, which is the standing rule in CLAUDE.md
# ("NOTHING IN THIS REPOSITORY IDENTIFIES ITS OWNER") and also the
# practical answer: this string ships in everybody's copy, so a personal
# address here is one a spam harvester reads off the first public clone.
SUPPORT_EMAIL = "dotadraftassist@outlook.com"

# WHERE A COPY OF THIS APP UPDATES ITSELF FROM, spelled ONCE. The
# updater, the version check behind the banner and the shareable zip's
# own build stamp all read these — three places that must agree about
# which repository and which branch, or the app checks one thing and
# updates from another.
GITHUB_OWNER = "bijankle"
GITHUB_REPO = "Dota_Draft_Assist"

# What a downloaded copy follows. Development happens on branches and is
# merged here when it is fit to hand to somebody, so a stranger's copy
# tracks finished work rather than somebody's half-written branch.
RELEASE_BRANCH = "main"

# What the ZIP path wrote last time, and what a shareable zip is stamped
# with so a fresh unzip knows which build it is. Gitignored: it describes
# one machine's install and nobody else's.
INSTALL_RECORD = "installed_version.json"

DATA_CACHE = REPO_ROOT / "data_cache"
RAW_DUMP_DIR = DATA_CACHE / "raw"
# EVERYTHING YOU WOULD SEND SOMEBODY LIVES UNDER `debug/`, and the
# layout is `draft_assist/debugdir.py` rather than four constants here:
# the recordings, the crash tracebacks, the launcher's own transcript
# and the zip that gets mailed were in three places with three naming
# conventions, so "send me what went wrong" took a paragraph to explain.
# These names stay because the whole app already reads them; what moved
# is where they point.
#
# One folder per recording session: payloads, frames and the app's
# own reading of both, kept together so one game is one piece of
# evidence rather than three scattered ones.
RECORDINGS_DIR = debugdir.folder("recordings")
CAPTURES_DIR = REPO_ROOT / "captures"
# Pictures the diagnostic tools draw. Named `debug_out` until it moved
# in; `debugdir.migrate` carries an existing one across.
DEBUG_OUT = debugdir.folder("scratch")
ASSETS_DIR = REPO_ROOT / "assets"
PORTRAITS_DIR = ASSETS_DIR / "portraits"
ITEMS_DIR = ASSETS_DIR / "items"


def item_slug(name: str) -> str:
    """'Black King Bar' -> 'black_king_bar'.

    Icon files are named by a slug of the item's DISPLAY name so that
    `rules/items.yaml` can go on saying "Black King Bar" the way a person
    writes it, rather than carrying an internal key to suit the loader.
    Lives here, not in the UI, because the downloader is a plain script and
    must not have to import Qt to work out a filename.
    """
    import re
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
RULES_FILE = REPO_ROOT / "rules" / "items.yaml"
LAYOUT_FILE = REPO_ROOT / "draft_assist" / "vision" / "layout_default.json"
# Local calibration nudges (gitignored); overrides the default layout.
CALIBRATION_FILE = REPO_ROOT / "calibration_local.json"

# Which rank brackets the statistics are drawn from.
#
# This is a DATA-PULL setting, not a display one: the baselines and the
# interaction matrices are built for the chosen brackets, so changing it
# means rebuilding the dataset. The choice is stored in preferences.json
# (gitignored) and read at call time, so the app and the pull subprocess
# always agree.
#
# The default follows the original reasoning: aim one bracket above where
# you play, so the advice reflects the games you are trying to win rather
# than the ones you already do. Two adjacent brackets are combined for
# sample size.
ALL_BRACKETS = ("HERALD", "GUARDIAN", "CRUSADER", "ARCHON",
                "LEGEND", "ANCIENT", "DIVINE", "IMMORTAL")
DEFAULT_TARGET_BRACKETS = ("ANCIENT", "DIVINE")
PREFS_FILE = REPO_ROOT / "preferences.json"


# The site whose pairwise numbers the matrices are built from. One at a
# time, never blended — see data/build.py.
DEFAULT_PAIR_SOURCE = "stratz"
PAIR_SOURCES = ("stratz", "opendota")


def _prefs() -> dict:
    import json
    try:
        stored = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return stored if isinstance(stored, dict) else {}


def _write_prefs(**changes) -> None:
    """Merge into the file rather than replacing it: two settings live here
    now, and saving one used to wipe the other."""
    import json
    stored = _prefs()
    stored.update(changes)
    PREFS_FILE.write_text(json.dumps(stored, indent=2), encoding="utf-8")


def pair_source() -> str:
    """Which site supplies matchup/synergy counts, resolved at call time so
    the app and the pull subprocess always agree."""
    chosen = _prefs().get("pair_source")
    return chosen if chosen in PAIR_SOURCES else DEFAULT_PAIR_SOURCE


def save_pair_source(source: str) -> None:
    if source not in PAIR_SOURCES:
        raise ValueError(f"unknown pair source {source!r}; "
                         f"expected one of {PAIR_SOURCES}")
    _write_prefs(pair_source=source)


def target_brackets() -> tuple[str, ...]:
    """The brackets statistics are pulled for, resolved at call time."""
    chosen = _prefs().get("target_brackets")
    if not isinstance(chosen, list):
        return DEFAULT_TARGET_BRACKETS
    # Keep canonical rank order regardless of what order they were picked
    # in, and drop anything unrecognised rather than failing the pull.
    valid = tuple(b for b in ALL_BRACKETS if b in chosen)
    return valid or DEFAULT_TARGET_BRACKETS


def save_target_brackets(brackets) -> None:
    ordered = [b for b in ALL_BRACKETS if b in set(brackets)]
    if not ordered:
        raise ValueError("at least one bracket must be selected")
    _write_prefs(target_brackets=ordered)


# Backwards-compatible alias; prefer target_brackets() so a changed
# preference takes effect without a restart.
TARGET_BRACKETS = DEFAULT_TARGET_BRACKETS

# Cached data older than this is considered stale and triggers a warning in
# the UI (the pull itself is a manual/daily action; the live loop never
# makes network calls).
CACHE_MAX_AGE_HOURS = 36


ENV_FILE = REPO_ROOT / ".env"
KEY_NAME = "STRATZ_API_KEY"
PLACEHOLDER = "your-stratz-api-key-here"


def env_file() -> Path:
    """Resolved at CALL time, never bound as a default — the rule this
    codebase learned from `load_layout`, where a module-level default let
    a test write into the real repository."""
    return ENV_FILE


def has_stratz_key() -> bool:
    """Is there a real key on this machine? Used to decide whether the
    first-run setup has anything left to ask for."""
    try:
        return bool(stratz_api_key())
    except RuntimeError:
        return False


def save_stratz_key(key: str) -> None:
    """Write the key into `.env`, KEEPING whatever else is in there.

    Rewriting the file wholesale would drop any other variable the user
    has put beside it, and `.env` is exactly the sort of file people add
    lines to. So the KEY's line is replaced in place and everything else
    is left alone; a file that does not exist yet is created with the
    comment from `.env.example`, because a bare assignment with no note
    saying the file is gitignored invites somebody to commit it.
    """
    key = (key or "").strip()
    if not key:
        raise ValueError("the key is empty")
    path = env_file()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = ["# Written by the app's first-run setup.",
                 "# This file is gitignored and must never be committed."]
    out, replaced = [], False
    for line in lines:
        if line.strip().startswith(f"{KEY_NAME}=") and not replaced:
            out.append(f"{KEY_NAME}={key}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{KEY_NAME}={key}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    # The key is read through `load_dotenv`, which does NOT overwrite a
    # variable already in the environment — so a key entered after one was
    # read this session would otherwise be ignored until a restart.
    os.environ[KEY_NAME] = key


def stratz_api_key() -> str:
    load_dotenv(env_file())
    key = os.environ.get("STRATZ_API_KEY", "").strip()
    if not key or key == "your-stratz-api-key-here":
        raise RuntimeError(
            "STRATZ_API_KEY not set. Copy .env.example to .env and paste "
            "your key from stratz.com (the .env file is gitignored)."
        )
    return key
