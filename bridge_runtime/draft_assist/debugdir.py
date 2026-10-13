"""Everything you would send somebody, under one folder.

    debug/
        startup/    one folder per launcher run - the setup transcript
        crashes/    one folder per unhandled error - the traceback
        reports/    one folder per problem report - the zip that was mailed
        recordings/ one folder per recorded session - payloads and frames
        scratch/    pictures the diagnostic tools draw

WHY IT IS ONE FOLDER. The evidence used to be in three places with three
naming conventions - `recordings/`, `debug_out/`, and for the launcher
nowhere at all - so "send me what went wrong" needed a paragraph of
instructions. It is one sentence now: send what is in `debug`.

AND THE LAUNCHER'S HALF IS WHY THIS EXISTS AT ALL. A user answered Y to
the pip prompt, something failed, and the console closed before they
could read it - because `:launch` runs `start` and exits, so the window
goes the moment the app appears. On the SUCCESS path. Nothing was
written down, so there was nothing to send.

NOTHING HERE MAY IMPORT Qt. The crash hook runs before the QApplication
exists and the launcher's own steps run before the app is installed at
all; a module they both need cannot reach a QPixmap.
"""

import shutil
import time
from pathlib import Path

# Resolved through this name rather than captured at import, so a test
# can repoint it - the rule `load_layout` and `ui_settings.load` follow,
# and the reason a test run once wrote a stray file into the repository.
ROOT = Path(__file__).resolve().parent.parent

FOLDER = "debug"

# What each kind is called on disk, and how many of them are kept.
# None means "keep everything": a recording is the user's own evidence
# and a report is what they sent, so neither is ours to delete. Only the
# two that write on EVERY run are pruned.
KINDS = {
    "startup": 3,
    "crashes": 10,
    "reports": None,
    "recordings": None,
    "scratch": None,
}

# The same spelling everywhere, and it is the one `recordings/` has
# always used - so a folder that moves in keeps the name it had.
STAMP = "%Y-%m-%d_%H%M%S"

# Where these folders used to live, and what they are called now. The
# move happens once, on the first run after the update.
MOVED = {"recordings": "recordings", "scratch": "debug_out"}


def root() -> Path:
    return ROOT / FOLDER


def folder(kind: str) -> Path:
    """The parent for one kind of evidence. Not created here."""
    if kind not in KINDS:
        raise KeyError(f"no such debug folder: {kind}")
    return root() / kind


def new_run(kind: str, when: float | None = None) -> Path:
    """Make and return a fresh date-stamped folder, pruning old ones.

    PRUNED BEFORE THE NEW ONE IS MADE, so the count is what the user
    asked for rather than one more than it: keeping the last three and
    creating a fourth means the first is gone, not that four are on
    disk until the fifth arrives.
    """
    keep = KINDS[kind]
    if keep is not None:
        prune(kind, keep - 1)
    stamp = time.strftime(STAMP, time.localtime(when))
    made = folder(kind) / stamp
    # A SECOND RUN IN THE SAME SECOND IS NOT AN ERROR. Two launches a
    # second apart is unlikely and a crash loop is not - and losing the
    # second traceback to a name collision would lose exactly the one
    # worth reading.
    if made.exists():
        for extra in range(2, 100):
            candidate = folder(kind) / f"{stamp}-{extra}"
            if not candidate.exists():
                made = candidate
                break
    made.mkdir(parents=True, exist_ok=True)
    return made


def runs(kind: str) -> list[Path]:
    """Every folder of this kind, oldest first. The names sort by date,
    which is the whole reason they are spelled that way."""
    where = folder(kind)
    if not where.is_dir():
        return []
    return sorted((p for p in where.iterdir() if p.is_dir()),
                  key=lambda p: p.name)


def prune(kind: str, keep: int) -> int:
    """Delete all but the newest `keep`. Returns how many went.

    NEVER FATAL. A folder held open by a text editor is a folder this
    cannot remove, and one refusal must not stop the app starting - the
    whole point of this module is to be the thing that still works when
    something else has not.
    """
    if keep < 0:
        keep = 0
    gone = 0
    for old in runs(kind)[:-keep] if keep else runs(kind):
        try:
            shutil.rmtree(old)
            gone += 1
        except OSError:
            pass
    return gone


def migrate() -> list[str]:
    """Move the old top-level folders in, once. Returns what moved.

    The user's recordings are gigabytes of their own evidence, so this
    MOVES rather than copies and never merges: if the new folder already
    exists the old one is left exactly where it is, for somebody to look
    at rather than for this to guess about. Never fatal, for the reason
    `prune` is not.
    """
    moved = []
    for kind, was in MOVED.items():
        old, new = ROOT / was, folder(kind)
        if not old.is_dir() or new.exists():
            continue
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)
            moved.append(kind)
        except OSError:
            pass
    return moved


def describe() -> str:
    """One line for the diagnostic paste."""
    if not root().is_dir():
        return f"{FOLDER}/: nothing recorded yet"
    counts = [f"{kind} {len(runs(kind))}" for kind in KINDS
              if folder(kind).is_dir()]
    return f"{FOLDER}/: " + (", ".join(counts) if counts else "empty")
