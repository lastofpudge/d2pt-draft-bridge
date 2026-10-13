"""Windows-only helpers around the Dota window handle (ctypes, no pywin32
needed at import time on other platforms). READ-ONLY: this module measures
and enumerates windows; nothing anywhere sends input to the game or touches
its process (see CLAUDE.md).
"""

import sys

# The Dota 2 client's window title is exactly this. Anything else with
# "dota" in the title is some other program (a browser tab, a guide, a
# video) and must never be captured by accident.
DOTA_TITLE = "Dota 2"


def list_window_titles() -> list[str]:
    """Visible top-level window titles, in z-order as EnumWindows reports."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    titles: list[str] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value.strip():
                    titles.append(buf.value)
        return True

    user32.EnumWindows(enum_proc, 0)
    # De-duplicate while preserving order; identical titles cannot be told
    # apart by name anyway.
    seen, out = set(), []
    for t in titles:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def find_dota_window_title() -> str | None:
    """The Dota client's window, or None.

    Deliberately EXACT: an earlier version fell back to 'the only visible
    window with dota in its title', which happily bound a browser playing a
    Dota video and fed the recogniser garbage pixels. A near miss here is
    worse than no match, because the failure looks like broken recognition
    rather than a wrong capture source.
    """
    return DOTA_TITLE if DOTA_TITLE in list_window_titles() else None


def dota_like_titles() -> list[str]:
    """Other windows mentioning Dota — offered as suggestions in the UI's
    capture-source picker, never bound automatically."""
    return [t for t in list_window_titles()
            if "dota" in t.lower() and t != DOTA_TITLE]


def client_size(title: str) -> tuple[int, int] | None:
    """Client-area size of the window with this title, from its handle —
    dimensions are always measured, never assumed from a resolution."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return None
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.right - rect.left, rect.bottom - rect.top


