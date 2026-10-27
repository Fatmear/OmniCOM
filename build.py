"""Build OmniCOM.exe with PyInstaller (native Qt app, no browser).

    ############################################################
    #  2026-10-27: THIS IS DISABLED BY POLICY (see R-019).      #
    #  The produced exe is blocked by corporate endpoint         #
    #  security ("拒绝访问") and will not run. Kept only as      #
    #  documentation of how a build would look; it requires an   #
    #  explicit acknowledgement flag before it will do anything. #
    ############################################################

    python build.py --i-understand-policy   # actually build (only where allowed)
    python build.py --help                  # see all options
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = "OmniCOM"


def build_args(onefile: bool, console: bool, icon: str | None) -> list[str]:
    sep = os.pathsep
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", NAME,
        "--onefile" if onefile else "--onedir",
        "--console" if console else "--windowed",
        # runtime assets (icon shown in Explorer / taskbar)
        "--add-data", f"{os.path.join(ROOT, 'assets')}{sep}assets",
        # lazily imported modules PyInstaller cannot see
        "--hidden-import", "serial.tools.list_ports",
        "--hidden-import", "PySide6.QtNetwork",
        # keep the bundle lean: none of these are used at runtime
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "numpy",
        "--exclude-module", "PIL",
        "--exclude-module", "pytest",
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.QtWebEngineWidgets",
        "--exclude-module", "PySide6.QtQml",
        "--exclude-module", "PySide6.QtQuick",
        "--exclude-module", "PySide6.Qt3DCore",
        "--exclude-module", "PySide6.QtMultimedia",
        "--exclude-module", "PySide6.QtCharts",
        os.path.join(ROOT, "run.py"),
    ]
    if icon and os.path.isfile(icon):
        args.extend(["--icon", icon])
    return args


def main() -> int:
    parser = argparse.ArgumentParser(description="Build OmniCOM.exe")
    parser.add_argument("--onefile", action="store_true", help="single-file executable")
    parser.add_argument("--console", action="store_true", help="keep a console window (debugging)")
    parser.add_argument("--icon", default=os.path.join(ROOT, "assets", "omnicom.ico"))
    parser.add_argument("--no-clean", action="store_true", help="keep build/ and dist/")
    parser.add_argument("--skip-icon", action="store_true", help="do not regenerate the .ico")
    parser.add_argument("--no-verify", action="store_true", help="skip the packaged --selftest")
    parser.add_argument("--i-understand-policy", action="store_true",
                        dest="policy_ack",
                        help="acknowledge that R-019 forbids shipping self-built exe files")
    options = parser.parse_args()

    if not options.policy_ack:
        print(__doc__)
        print("已取消：本机企业策略禁止运行自编译 exe（R-019）。")
        print("请直接运行源码：  start.cmd   或   python run.py")
        return 3

    if not options.skip_icon:
        with contextlib.suppress(OSError):
            subprocess.run([sys.executable, os.path.join(ROOT, "tools", "make_icon.py")],
                           cwd=ROOT, check=False)

    if not options.no_clean:
        for folder in ("build", "dist"):
            shutil.rmtree(os.path.join(ROOT, folder), ignore_errors=True)
        spec = os.path.join(ROOT, f"{NAME}.spec")
        if os.path.exists(spec):
            os.remove(spec)

    args = build_args(options.onefile, options.console, options.icon)
    print("PyInstaller:", " ".join(args))
    result = subprocess.run(args, cwd=ROOT)
    if result.returncode != 0:
        print("打包失败")
        return result.returncode

    target = os.path.join(ROOT, "dist", f"{NAME}.exe" if options.onefile
                          else os.path.join(NAME, f"{NAME}.exe"))
    if not os.path.exists(target):
        print("未找到输出文件，请检查 PyInstaller 日志")
        return 1
    size = os.path.getsize(target) / 1048576
    print(f"\n完成：{target}  ({size:.1f} MB)")

    if options.no_verify:
        return 0

    print("\n正在运行打包自检：", target, "--selftest")
    result_file = os.path.join(os.path.dirname(target), "omnicom-data", "selftest.json")
    if os.path.exists(result_file):
        os.remove(result_file)
    check = subprocess.run([target, "--selftest"], cwd=os.path.dirname(target),
                           capture_output=True, text=True, timeout=240)
    output = ((check.stdout or "") + (check.stderr or "")).strip()
    if output:
        print(output[-1200:])
    # a --windowed build has no stdout, so the verdict also comes from the exit code
    verdict = None
    try:
        with open(result_file, encoding="utf-8") as handle:
            verdict = json.load(handle)
    except (OSError, json.JSONDecodeError):
        pass
    if verdict is not None:
        print("自检结果：", "全部通过" if verdict.get("ok") else "存在失败项")
        for item in verdict.get("checks", []):
            print(("  PASS  " if item["ok"] else "  FAIL  ") + item["name"]
                  + (f"  [{item['detail']}]" if item.get("detail") else ""))
    if check.returncode != 0 or (verdict is not None and not verdict.get("ok")):
        print("打包自检失败")
        return 2
    print("\n打包完成，可直接双击 exe 使用（加 --demo 可启用虚拟演示串口）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
