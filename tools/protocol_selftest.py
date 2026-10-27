"""Thin wrapper around :mod:`omnicom.selftest` (X/Y/ZMODEM loopback).

    python tools/protocol_selftest.py

The actual checks live in the package so the CLI and the GUI tests can run the
exact same proof.  Exit code 0 means every check passed.
"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from omnicom.selftest import run_protocol_checks  # noqa: E402


def main() -> int:
    started = time.time()
    checks = run_protocol_checks(verbose=lambda text: print(text))
    failed = [item for item in checks if not item["ok"]]
    for item in checks:
        detail = f"  [{item['detail']}]" if item.get("detail") not in (None, "") else ""
        print(("  PASS  " if item["ok"] else "  FAIL  ") + item["name"] + detail)
    print()
    print(f"{len(checks) - len(failed)}/{len(checks)} 项通过，用时 {time.time() - started:.1f}s")
    if failed:
        print("失败项：")
        for item in failed:
            print(f"  - {item['name']} {item.get('detail')}")
        return 1
    print("ALL PROTOCOL SELFTESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
