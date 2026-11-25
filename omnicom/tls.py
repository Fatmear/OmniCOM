"""HTTPS trust for the portable build.

The portable folder ships the official CPython *embeddable* runtime, whose
``python313._pth`` switches ``site`` off.  On Windows that is normally fine:
OpenSSL falls back to the system certificate store.  It stops being fine on a
machine whose store is missing the roots (a trimmed corporate image, a
freshly imaged VM, or a proxy that re-signs traffic) - then every HTTPS call
fails with::

    [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed:
    unable to get local issuer certificate

which is exactly what the update check reported (R-342).  The development
machine never showed it because ``certifi`` happened to be installed in the
*user* site-packages, and the embeddable runtime ignores those.

So the bundle carries its own CA file and every HTTPS request uses a context
that trusts **both** the bundled roots and the system store.  Trusting the
system store as well matters: a corporate proxy installs its own root there,
and dropping it would break updates on exactly the machines that need them.
"""

from __future__ import annotations

import contextlib
import os
import ssl
import sys
import urllib.request
from pathlib import Path

#: Name of the CA bundle shipped next to the archive (``assets/cacert.pem``).
CA_FILENAME = "cacert.pem"

_context: ssl.SSLContext | None = None


def _candidates() -> list[Path]:
    """Every place the CA file may live, most specific first."""
    paths: list[Path] = []
    override = (os.environ.get("OMNICOM_CA_BUNDLE") or "").strip()
    if override:
        paths.append(Path(override))
    # ``paths`` imports this module, so it is imported lazily to avoid a cycle.
    try:
        from . import paths as app_paths

        for base in (app_paths.resource_dir(), app_paths.app_dir()):
            if base:
                paths.append(Path(base) / "assets" / CA_FILENAME)
                paths.append(Path(base) / CA_FILENAME)
    except Exception:  # pragma: no cover - never break import for a path probe
        pass
    here = Path(__file__).resolve().parent
    paths.append(here.parent / "assets" / CA_FILENAME)
    paths.append(here / CA_FILENAME)
    return paths


def ca_bundle() -> str | None:
    """Path to the bundled CA file, or ``None`` when it is not shipped."""
    for candidate in _candidates():
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return str(candidate)
        except OSError:
            continue
    return None


def context() -> ssl.SSLContext:
    """A verifying context that trusts the bundled roots *and* the system store.

    Built once: creating a context loads every certificate, and the update
    check runs on a timer.
    """
    global _context
    if _context is not None:
        return _context
    bundle = ca_bundle()
    if bundle is None:
        # No shipped file: keep the platform default (system store) rather than
        # silently disabling verification.
        _context = ssl.create_default_context()
        return _context
    _context = ssl.create_default_context(cafile=bundle)
    # ``create_default_context(cafile=...)`` replaces the system store, so a
    # corporate proxy root would stop being trusted.  Load it as well.
    with contextlib.suppress(Exception):
        _context.load_default_certs()
    return _context


def install() -> None:
    """Make ``urllib`` use :func:`context` for every HTTPS request.

    ``urllib.request.urlopen`` builds its own opener with the *default*
    context, so the only way to apply the bundle to code that already calls
    ``urlopen`` is to install a module-level opener.  Idempotent.
    """
    if getattr(urllib.request, "_omnicom_tls", False):
        return
    handler = urllib.request.HTTPSHandler(context=context())
    opener = urllib.request.build_opener(handler)
    urllib.request.install_opener(opener)
    urllib.request._omnicom_tls = True  # type: ignore[attr-defined]


def describe() -> dict:
    """Diagnostics for the self-test and the update dialog."""
    bundle = ca_bundle()
    return {
        "bundle": bundle,
        "system_certs": _system_count(),
        "openssl": ssl.OPENSSL_VERSION,
        "executable": sys.executable,
    }


def _system_count() -> int:
    try:
        stats = ssl.create_default_context().cert_store_stats()
    except Exception:  # pragma: no cover - platform specific
        return 0
    return int(stats.get("x509", 0))
