"""Standalone installer, copied outside the running bundle before launch."""

from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def archive_version(archive: Path) -> str:
    with zipfile.ZipFile(archive) as handle:
        source = handle.read("omnicom/__init__.py").decode("utf-8")
    match = re.search(r"""__version__\s*=\s*["']([0-9][0-9.]*)["']""", source)
    if not match:
        raise ValueError("Missing package version")
    return match.group(1)


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(temporary, path)


def prepare_files(source: Path, target: Path, progress=None):
    """Copy candidates and recovery files without modifying the live bundle."""
    source, target = source.resolve(), target.resolve()
    if source == target or source in target.parents:
        raise ValueError("Invalid update source")
    entries = []
    for base, folders, files in os.walk(source):
        folders[:] = [
            name for name in folders if name.lower() != "omnicom-data"
        ]
        for name in files:
            path = Path(base) / name
            relative = path.relative_to(source)
            destination = target / relative
            if path.is_symlink() or any(
                parent.is_symlink() for parent in path.parents
            ):
                raise ValueError(f"Refusing symbolic link: {path}")
            if target not in destination.resolve().parents:
                raise ValueError(f"Unsafe destination: {destination}")
            if relative.parts[0].lower() == "plugins" and destination.exists():
                continue
            entries.append((path, relative, destination))
    work = Path(tempfile.mkdtemp(prefix=".omnicom-update-", dir=target))
    try:
        for index, (path, relative, destination) in enumerate(entries, 1):
            fresh, backup = work / "new" / relative, work / "old" / relative
            fresh.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, fresh)
            if destination.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, backup)
            with path.open("rb") as original, fresh.open("rb") as candidate:
                if (hashlib.file_digest(original, "sha256").digest()
                        != hashlib.file_digest(candidate, "sha256").digest()):
                    raise OSError(f"Prepared file verification failed: {relative}")
            if progress is not None:
                progress(index, len(entries))
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return entries, work


def apply_files(source: Path, target: Path, prepared=None, progress=None) -> list[str]:
    """Commit a prepared update after the old process has released its files."""
    entries, work = prepared or prepare_files(source, target)
    committed = []
    rollback_failed = False
    try:
        for _path, _relative, destination in entries:
            if destination.exists():
                descriptor = os.open(destination, os.O_WRONLY)
                os.close(descriptor)
        try:
            for index, (_path, relative, destination) in enumerate(entries, 1):
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(work / "new" / relative, destination)
                committed.append((relative, destination))
                if progress is not None:
                    progress(index, len(entries) * 2)
            for index, (path, relative, destination) in enumerate(entries, 1):
                with (
                    path.open("rb") as original,
                    destination.open("rb") as installed,
                ):
                    if (
                        hashlib.file_digest(original, "sha256").digest()
                        != hashlib.file_digest(installed, "sha256").digest()
                    ):
                        raise OSError(
                            f"Update verification failed: {relative}"
                        )
                if progress is not None:
                    progress(len(entries) + index, len(entries) * 2)
        except BaseException:
            for relative, destination in reversed(committed):
                try:
                    backup = work / "old" / relative
                    if backup.exists():
                        os.replace(backup, destination)
                    else:
                        destination.unlink()
                except OSError:
                    rollback_failed = True
            if rollback_failed:
                raise OSError(
                    f"Rollback incomplete; recovery files retained at {work}"
                ) from None
            raise
    finally:
        if not rollback_failed:
            shutil.rmtree(work, ignore_errors=True)
    return [str(relative) for _path, relative, _destination in entries]


def parent_handle(pid: int):
    if sys.platform != "win32":
        return os.pidfd_open(pid)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_ulong,
    ]
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def wait_parent(handle, timeout: int = 120000) -> None:
    if sys.platform != "win32":
        import select

        try:
            if not select.select([handle], [], [], timeout / 1000)[0]:
                raise TimeoutError("The old application did not exit")
        finally:
            os.close(handle)
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    try:
        status = kernel.WaitForSingleObject(handle, timeout)
        if status != 0:
            raise TimeoutError(f"The old application did not exit: {status}")
    finally:
        kernel.CloseHandle(handle)


def launch(command: list[str], cwd: str) -> None:
    subprocess.Popen(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=(0x08000000 if sys.platform == "win32" else 0),
    )


def finish_job(job: dict, handle, prepared, *, restart: bool = True, progress=None) -> int:
    source, target = Path(job["source"]), Path(job["target"])
    marker, result_path = Path(job["marker"]), Path(job["result"])
    try:
        wait_parent(handle)
    except Exception as error:
        write_json(
            result_path,
            {"ok": False, "error": str(error), "version": job["version"]},
        )
        mark_failed(marker, source, error)
        shutil.rmtree(prepared[1], ignore_errors=True)
        return 1
    try:
        written = apply_files(source, target, prepared, progress=progress)
        if archive_version(target / "OmniCOM.pyz") != job["version"]:
            raise ValueError("Installed version does not match")
        write_json(
            result_path,
            {"ok": True, "version": job["version"], "files": len(written),
             "ready": job.get("ready", "")},
        )
        if marker.is_file():
            pending = json.loads(marker.read_text(encoding="utf-8"))
            if Path(pending.get("source", "")).resolve() == source.resolve():
                marker.unlink()
    except Exception as error:
        write_json(
            result_path,
            {"ok": False, "error": str(error), "version": job["version"],
             "ready": job.get("ready", "")},
        )
        mark_failed(marker, source, error)
    if not marker.exists():
        with contextlib.suppress(OSError):
            shutil.rmtree(source)
    return restart_job(job) if restart else 0


def restart_job(job: dict) -> int:
    target = Path(job["target"])
    result_path = Path(job["result"])
    try:
        with zipfile.ZipFile(target / "OmniCOM.pyz") as archive:
            try:
                consumer = b"def _show_update_result(" in archive.read(
                    "omnicom/ui/__init__.py")
            except KeyError:
                consumer = False
        if not consumer:
            Path(job["result"]).unlink(missing_ok=True)
        launch(job["restart"], str(target))
    except OSError as error:
        write_json(
            result_path,
            {
                "ok": False,
                "error": f"Restart failed: {error}",
                "version": job["version"],
            },
        )
        return 1
    return 0


def run_progress(job: dict, handle, prepared) -> int:
    import threading

    from PySide6.QtCore import QObject, Qt, QTimer, Signal
    from PySide6.QtGui import QIcon, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QDialog,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QProgressBar,
        QVBoxLayout,
    )

    class Relay(QObject):
        finished = Signal(int)
        progressed = Signal(int, int)

    class ProgressDialog(QDialog):
        def reject(self):
            pass

        def closeEvent(self, event):
            event.ignore()

    appearance = job["progress_ui"]
    if sys.platform == "win32":
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
        shell.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
        if shell.SetCurrentProcessExplicitAppUserModelID("OmniCOM.Installer"):
            raise OSError("Could not set installer taskbar identity")
    app = QApplication([])
    app.setApplicationName("OmniCOM")
    app.setOrganizationName("OmniCOM")
    app.setStyle("Fusion")
    app.setStyleSheet(appearance["stylesheet"])
    assets = Path(job["target"]) / "assets"
    icon = QIcon(appearance.get("icon", str(assets / "omnicom.ico")))
    app.setWindowIcon(icon)
    dialog = ProgressDialog()
    dialog.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.CustomizeWindowHint
                          | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowStaysOnTopHint)
    dialog.setWindowTitle(appearance["title"])
    dialog.setWindowIcon(icon)
    dialog.resize(540, 220)
    center = appearance.get("center")
    if center:
        dialog.move(int(center[0] - 270), int(center[1] - 110))
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 20, 24, 24)
    layout.setSpacing(16)
    header = QHBoxLayout()
    header.setSpacing(14)
    logo = QLabel()
    bitmap = QPixmap(appearance.get("logo", str(assets / "omnicom.png")))
    logo.setPixmap(bitmap.scaled(52, 52, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    header.addWidget(logo)
    titles = QVBoxLayout()
    titles.setSpacing(3)
    brand = QLabel("OmniCOM")
    brand.setStyleSheet("font-size: 22px; font-weight: 650;")
    titles.addWidget(brand)
    titles.addWidget(QLabel(appearance["title"]))
    header.addLayout(titles, 1)
    layout.addLayout(header)
    label = QLabel(appearance["message"])
    label.setWordWrap(True)
    layout.addWidget(label)
    progress = QProgressBar()
    progress.setRange(0, 0)
    layout.addWidget(progress)
    events = []

    def trace(event):
        if not appearance.get("trace"):
            return
        native_icon = None
        if app.platformName() == "windows":
            user = ctypes.WinDLL("user32", use_last_error=True)
            user.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                         ctypes.c_size_t, ctypes.c_ssize_t]
            user.SendMessageW.restype = ctypes.c_ssize_t
            native_icon = bool(user.SendMessageW(int(dialog.winId()), 0x7F, 1, 0))
        events.append({"event": event, "visible": dialog.isVisible(),
                       "maximum": progress.maximum(), "value": progress.value(),
                       "icon": not dialog.windowIcon().isNull(),
                       "logo": not logo.pixmap().isNull(), "native_icon": native_icon,
                       "taskbar_window": dialog.windowType() == Qt.WindowType.Window})
        write_json(Path(appearance["trace"]), {"events": events})

    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    app.processEvents()
    if appearance.get("snapshot"):
        dialog.grab().save(appearance["snapshot"])
    trace("visible")
    print("VISIBLE", flush=True)
    sys.stdout.close()
    relay = Relay()
    def finished(code):
        if code:
            result = json.loads(Path(job["result"]).read_text(encoding="utf-8"))
            QMessageBox.warning(dialog, appearance["title"], str(result.get("error", "")))
            app.exit(1)
        else:
            progress.setRange(0, 100)
            progress.setValue(100)
            result = json.loads(Path(job["result"]).read_text(encoding="utf-8"))
            label.setText(appearance.get("complete", appearance["message"])
                          if result.get("ok") else appearance["message"])
            progress.repaint()
            trace("complete")
            QTimer.singleShot(0, restart)

    def restart():
        dialog.hide()
        app.processEvents()
        trace("restart")
        code = restart_job(job)
        if code:
            result = json.loads(Path(job["result"]).read_text(encoding="utf-8"))
            QMessageBox.warning(dialog, appearance["title"], str(result.get("error", "")))
        app.exit(code)

    def work():
        try:
            code = finish_job(job, handle, prepared, restart=False,
                              progress=relay.progressed.emit)
        except Exception as error:
            write_json(Path(job["result"]), {"ok": False, "error": str(error), "version": job["version"]})
            code = 1
        relay.finished.emit(code)

    def on_progress(done, total):
        progress.setRange(0, 100)
        progress.setValue(min(99, done * 99 // max(1, total)))

    relay.progressed.connect(on_progress)
    relay.finished.connect(finished)
    thread = threading.Thread(target=work, daemon=False)
    thread.start()
    exit_code = app.exec()
    thread.join()
    return exit_code


def run_job(job: dict) -> int:
    source, target = Path(job["source"]), Path(job["target"])
    if archive_version(source / "OmniCOM.pyz") != job["version"]:
        raise ValueError("Staged version does not match the requested update")
    handle = parent_handle(job["pid"])

    def progress(done, total):
        print(json.dumps({"progress": done, "total": total}), flush=True)

    prepared = prepare_files(source, target, progress)
    print("READY", flush=True)
    if job.get("wait_confirmation") and sys.stdin.readline().strip() != "INSTALL":
        shutil.rmtree(prepared[1], ignore_errors=True)
        return 0
    if job.get("progress_ui"):
        return run_progress(job, handle, prepared)
    if job.get("wait_confirmation"):
        print("VISIBLE", flush=True)
    sys.stdout.close()
    return finish_job(job, handle, prepared)


def mark_failed(marker: Path, source: Path, error: Exception) -> None:
    with contextlib.suppress(OSError, ValueError):
        pending = json.loads(marker.read_text(encoding="utf-8"))
        if Path(pending.get("source", "")).resolve() == source.resolve():
            pending["failed"] = str(error)
            write_json(marker, pending)


def main(job_path: str) -> int:
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    lock_path = Path(job["result"]).parent / "installer.lock"
    with lock_path.open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return run_job(job)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
