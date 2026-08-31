"""Is PySide6 present, and install it if the user agrees.

Split out of ffmwiz/runtime.py for file size. It is a self-contained subsystem:
the ONLY thing it shares with the rest of runtime.py is nothing at all -- the
progress renderer next door has its own module state, and this module owns
`_PYSIDE6_AVAILABLE_CACHE` together with both functions that read and write it,
which is what keeps the `global` legal after the split.

`_pyside6_available` is deliberately exported despite the leading underscore:
`import *` skips underscore names unless __all__ names them, and two callers
(guibridge_b, support/ext04_inspect) reach it exactly that way.
"""
from __future__ import annotations

import os
import subprocess
import sys

from ffmwiz.core.constants import (PYSIDE6_DISPLAY_NAME, PYSIDE6_PIP_SPEC,
                                   REQUIREMENTS_FILE_NAME)
from ffmwiz import appio  # noqa: F401
from ffmwiz.support import L00_misc  # noqa: F401  (defines _probe_pyside6)
from ffmwiz.support import L01_misc  # noqa: F401  (defines the path helpers)


_PYSIDE6_AVAILABLE_CACHE: bool | None = None


def _pyside6_available() -> bool:
    """Cached PySide6 detection."""
    global _PYSIDE6_AVAILABLE_CACHE
    if _PYSIDE6_AVAILABLE_CACHE is not None:
        return _PYSIDE6_AVAILABLE_CACHE
    if not L01_misc._ffmwiz_gui_path().exists():
        _PYSIDE6_AVAILABLE_CACHE = False
        return False
    _PYSIDE6_AVAILABLE_CACHE = L00_misc._probe_pyside6()
    return _PYSIDE6_AVAILABLE_CACHE


def ensure_pyside6_installed(interactive: bool = True) -> bool:
    """Make sure PySide6 is importable. On first run, offers to install it
    automatically with pip. Returns True if PySide6 is available afterwards.

    Environment overrides:
        FFMWIZ_NO_AUTO_INSTALL=1   Skip the install prompt entirely; active
                                    GUI prompts remain unavailable.
        FFMWIZ_AUTO_INSTALL=1      Skip the confirmation and install
                                    without asking (good for unattended
                                    setups, CI, scripts).
    """
    global _PYSIDE6_AVAILABLE_CACHE
    if _pyside6_available():
        return True

    if os.environ.get("FFMWIZ_NO_AUTO_INSTALL"):
        return False

    # Make sure the GUI file is present; installing the runtime is pointless
    # if the actual GUI module is missing.
    if not L01_misc._ffmwiz_gui_path().exists():
        return False

    auto = bool(
        os.environ.get("FFMWIZ_AUTO_INSTALL")
        or os.environ.get("FFMWIZ_AUTO_INSTALL_PYSIDE")
    )

    print()
    appio.note(
        f"{PYSIDE6_DISPLAY_NAME} is not installed. The active FFmWiz graphical "
        f"editors need it for smooth playback and a professional UI."
    )

    proceed = auto
    if not auto and interactive:
        try:
            choice = input(
                f"Install {PYSIDE6_DISPLAY_NAME} now via pip? [Y/n] "
                "(Enter=Yes; set FFMWIZ_NO_AUTO_INSTALL=1 to skip in the future): "
            ).strip().lower()
            proceed = choice in {"", "y", "yes"}
        except (EOFError, KeyboardInterrupt):
            proceed = False

    if not proceed:
        appio.note(
            f"Skipping. FFmWiz will keep graphical editor prompts unavailable for now. "
            f"Install later with:  py -3 -m pip install -r {REQUIREMENTS_FILE_NAME}"
        )
        return False

    # Try the system-wide install first. If pip cannot write to the
    # interpreter's site-packages (very common on Windows for
    # installations under "Program Files"), automatically retry with
    # --user so the install succeeds for the current user.
    requirements_path = L01_misc._requirements_path()
    install_target = ["-r", str(requirements_path)] if requirements_path.exists() else [PYSIDE6_PIP_SPEC]
    base_cmd = [sys.executable, "-m", "pip", "install", "--upgrade"]
    attempts: list[list[str]] = [
        base_cmd + install_target,
        base_cmd + ["--user"] + install_target,
    ]
    install_ok = False
    for attempt_idx, cmd in enumerate(attempts):
        print()
        appio.note("Running: " + " ".join(cmd))
        print()
        try:
            # Inherit stdout/stderr so the user sees pip's progress live.
            # The install can be ~150 MB and the user needs visibility.
            result = subprocess.run(cmd, check=False)
        except FileNotFoundError as exc:
            appio.error(f"Could not run pip ({exc}). Graphical editor prompts will remain unavailable.")
            return False
        except Exception as exc:
            appio.error(f"Pip install failed: {exc}.")
            continue
        if result.returncode == 0:
            install_ok = True
            break
        if attempt_idx + 1 < len(attempts):
            appio.note(
                f"pip install exited with code {result.returncode}. "
                "Retrying with --user (per-user install) ..."
            )

    if not install_ok:
        appio.error(
            f"{PYSIDE6_DISPLAY_NAME} install failed. Graphical editor prompts will remain unavailable. "
            f"You can retry manually with:  py -3 -m pip install --user -r {REQUIREMENTS_FILE_NAME}"
        )
        return False

    # Re-probe so the cache picks up the newly installed package.
    _PYSIDE6_AVAILABLE_CACHE = None
    if _pyside6_available():
        appio.note(f"{PYSIDE6_DISPLAY_NAME} installed. The new GUI is now active.")
        return True
    appio.error(
        f"{PYSIDE6_DISPLAY_NAME} install completed but the package still cannot "
        "be imported. Graphical editor prompts will remain unavailable."
    )
    return False


__all__ = [
    'ensure_pyside6_installed',
    '_pyside6_available',
]
