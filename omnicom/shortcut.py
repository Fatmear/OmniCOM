"""Create a desktop shortcut for OmniCOM, with the app icon and its own name.

The portable folder ships ``桌面快捷方式.cmd`` next to ``OmniCOM.cmd``; it calls

    runtime\\python.exe OmniCOM.pyz shortcut

which lands a shortcut named *OmniCOM* on the desktop whose target is the bundled
``pythonw.exe`` (so no console window appears), whose working directory is the
unzipped folder, and whose icon is the application icon instead of the generic
Python one (R-097).

Nothing is written outside the desktop folder; ``--name`` renames the shortcut
and ``--desktop`` puts it somewhere else, which is what the tests use.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from . import paths

APP_NAME = "OmniCOM"


def default_desktop() -> str:
    """The real desktop folder - it can be redirected (OneDrive, roaming)."""
    if sys.platform.startswith("win"):
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "[Environment]::GetFolderPath('Desktop')"],
                capture_output=True, text=True, timeout=30)
            folder = (result.stdout or "").strip()
            if folder:
                return folder
        except (OSError, subprocess.SubprocessError):
            pass
    candidate = os.path.join(os.path.expanduser("~"), "Desktop")
    return candidate if os.path.isdir(candidate) else os.path.expanduser("~")


def shortcut_spec(folder: str | None = None) -> dict:
    """Work out target / arguments / icon for the folder we are running from."""
    base = os.path.abspath(folder or paths.app_dir())
    runtime = os.path.join(base, "runtime", "pythonw.exe")
    archive = os.path.join(base, f"{APP_NAME}.pyz")
    launcher = os.path.join(base, f"{APP_NAME}.cmd")
    source_launcher = os.path.join(base, "start.cmd")
    if os.path.isfile(runtime) and os.path.isfile(archive):
        target, arguments = runtime, f'"{archive}"'
    elif os.path.isfile(launcher):
        target, arguments = launcher, ""
    elif os.path.isfile(source_launcher):
        target, arguments = source_launcher, ""
    else:                                   # running from source
        target, arguments = sys.executable, f'"{os.path.join(base, "run.py")}"'
    icon = ""
    for candidate in (os.path.join(base, "assets", "omnicom.ico"),
                      os.path.join(base, "logo.png")):
        if os.path.isfile(candidate):
            icon = candidate
            break
    return {"target": target, "arguments": arguments, "workdir": base,
            "icon": icon}


def create_shortcut(desktop: str | None = None, name: str = APP_NAME,
                    folder: str | None = None) -> str:
    """Create ``<desktop>/<name>.lnk``; returns its path.  Raises on failure."""
    if not sys.platform.startswith("win"):
        raise OSError("desktop shortcuts are a Windows feature")
    spec = shortcut_spec(folder)
    destination = os.path.join(desktop or default_desktop(), f"{name}.lnk")
    os.makedirs(os.path.dirname(destination), exist_ok=True)

    icon_line = (f"$s.IconLocation = '{spec['icon']}'"
                 if spec["icon"] else "")
    # A .cmd target flashes a console window; a minimized one is less noisy.
    style = 7 if spec["target"].lower().endswith(".cmd") else 1
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{destination}')\n"
        f"$s.TargetPath = '{spec['target']}'\n"
        f"$s.Arguments = '{spec['arguments']}'\n"
        f"$s.WorkingDirectory = '{spec['workdir']}'\n"
        f"$s.Description = '{name} - serial workbench'\n"
        f"{icon_line}\n"
        f"$s.WindowStyle = {style}\n"
        "$s.Save()\n"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not os.path.isfile(destination):
        detail = (result.stderr or result.stdout or "").strip()[:300]
        raise OSError(detail or "could not create the shortcut")
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=f"{APP_NAME.lower()} shortcut",
        description="Put a shortcut to this OmniCOM folder on the desktop.")
    parser.add_argument("--name", default=APP_NAME, help="shortcut name")
    parser.add_argument("--desktop", default="", help="create it in this folder instead")
    parser.add_argument("--folder", default="", help="the OmniCOM folder to link to")
    options = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        path = create_shortcut(options.desktop or None, options.name,
                               options.folder or None)
    except OSError as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
