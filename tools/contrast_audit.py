"""Contrast audit for the light theme.

Every foreground/background pair the UI uses must reach WCAG AA for normal
text (4.5:1).  Run it after touching ``omnicom/ui/theme.py``:

    python tools/contrast_audit.py

Exit code 0 means every pair passes; 1 lists the offenders.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from omnicom.ui import theme  # noqa: E402

MIN_RATIO = 4.5


def luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str) -> float:
    a, b = luminance(foreground), luminance(background)
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


def pairs() -> list[tuple[str, str, str]]:
    card = theme.CARD
    return [
        ("正文 INK / 卡片", theme.INK, card),
        ("次级文字 INK2 / 卡片", theme.INK2, card),
        ("提示文字 DIM / 卡片", theme.DIM, card),
        ("提示文字 DIM / 背景", theme.DIM, theme.BG),
        ("计数 RX（TEAL）/ 卡片", theme.TEAL, card),
        ("计数 TX（VIOLET）/ 卡片", theme.VIOLET, card),
        ("关键词胶囊 / 胶囊底", theme.TEAL, theme.TEAL_SOFT),
        ("状态：正常 / 底色", theme.OK, theme.OK_SOFT),
        ("状态：告警 / 底色", theme.AMBER, theme.AMBER_SOFT),
        ("状态：错误 / 底色", theme.DANGER, theme.DANGER_SOFT),
        ("日志 TX 颜色 / 卡片", "#3A2F86", card),
        ("搜索高亮 / 高亮底", "#0A3F3A", "#BFF0E6"),
        # R-077: worst case of a frosted pane / status plate over a black photo
        ("提示文字 DIM / 玻璃面板最暗", theme.DIM, theme.glass_floor()),
        ("次级文字 INK2 / 玻璃面板最暗", theme.INK2, theme.glass_floor()),
        # R-082: the caption row paints nothing, so it sits on the page itself -
        # the darkest possible page is the veil over a black photo.  That is why
        # the captions use INK and not the grey hint colour (which fails here).
        ("正文 INK / 页面最暗", theme.INK, theme.page_floor()),
    ]


def main() -> int:
    failed = []
    for name, foreground, background in pairs():
        ratio = contrast(foreground, background)
        flag = "PASS" if ratio >= MIN_RATIO else "FAIL"
        if ratio < MIN_RATIO:
            failed.append(name)
        print(f"  {flag}  {ratio:5.2f}  {name}  ({foreground} on {background})")
    if failed:
        print(f"\n{len(failed)} 组对比度低于 {MIN_RATIO}:1：" + "、".join(failed))
        return 1
    print(f"\nALL {len(pairs())} CONTRAST PAIRS >= {MIN_RATIO}:1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
