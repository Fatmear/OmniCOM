"""Qt WebEngine prerequisites, isolated so every entry point can share them.

``QWebEngineView`` has three requirements that all have to be satisfied *before*
the first ``QApplication`` exists, and none of them are visible when they are
missing -- the page simply never finishes loading and the widget stays blank:

* ``Qt.AA_ShareOpenGLContexts`` must be set on the application *class* before
  the application object is constructed.
* ``QTWEBENGINE_CHROMIUM_FLAGS`` must contain ``--no-sandbox``.  Measured on
  this machine across seven combinations: every run without it reported
  ``loadFinished(False)`` and no JavaScript ever executed; every run with it
  rendered (ECharts painted 294 000 non-white pixels).  ``--disable-gpu`` keeps
  the rasteriser off the D3D path that logged ``ContextResult::kFatalFailure``;
  ``--in-process-gpu`` also survived and is kept because it removes the extra
  GPU process from the portable bundle's startup path.
* The variable is only *defaulted* here, never overwritten, so a user can
  override it from the environment.

The module is deliberately import-light: ``PySide6.QtWebEngineWidgets`` is never
imported at this level, because a plugin must be able to tell the user "the
dashboard needs an optional Qt module" instead of taking the window down with it
(R-032).
"""

from __future__ import annotations

import importlib.util
import os

#: Default Chromium switches.  ``--no-sandbox`` is not cosmetic: without it the
#: render process never reports a finished load (see the module docstring).
CHROMIUM_FLAGS = "--no-sandbox --disable-gpu --in-process-gpu"

_prepared = False


def available() -> bool:
    """Is the optional WebEngine package installed?"""
    try:
        return importlib.util.find_spec("PySide6.QtWebEngineWidgets") is not None
    except (ImportError, ValueError):
        return False


def prepare() -> bool:
    """Apply the prerequisites.  Call *before* creating ``QApplication``.

    Safe to call more than once; the second call reports the same answer.  It
    also records whether it ran early enough, so :func:`ready` can tell a
    correctly prepared process from a late one.
    """
    global _prepared

    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is not None:
        # Too late for the attribute.  Harmlessly return the module status so a
        # caller that only cares about availability still gets the right answer.
        return available()
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", CHROMIUM_FLAGS)
    from PySide6.QtCore import Qt

    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    _prepared = True
    return available()


def ready() -> bool:
    """True when :func:`prepare` ran before the application and the module exists."""
    return _prepared and available()
