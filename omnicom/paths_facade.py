"""Read-only folder facade handed to plugins (R-322).

``omnicom.paths`` mixes readers with *setters* that redirect where the whole
application writes.  A plugin only ever needs the readers, so this object
exposes exactly those and refuses the rest.
"""

from __future__ import annotations

from . import paths

#: Readers only.  ``set_logs_dir`` stays host-only: a plugin must not redirect
#: where every window writes its captures.
READERS = (
    "app_dir", "resource_dir", "resource_file", "web_dir", "data_dir",
    "default_logs_dir", "logs_dir", "logs_dir_is_custom", "received_dir",
    "archive_dir",
)


class PathsFacade:
    """Read-only view of the host folders a plugin may use."""

    __slots__ = ()

    def __getattr__(self, name: str):
        if name in READERS:
            return getattr(paths, name)
        raise AttributeError(f"paths.{name} is not part of the plugin API (R-322)")

    def __setattr__(self, name: str, value) -> None:
        raise AttributeError("host paths are read-only for plugins")
