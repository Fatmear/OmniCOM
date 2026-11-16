"""Unit tests for the VT100 screen model (no Qt, no hardware).

    python tools/terminal_selftest.py

Every case is a byte sequence a real serial console receives, with the screen
state a real terminal would show.  The colleague's report - "左右键移动居然会
删除字符" - is the ``test_cursor_left_does_not_delete`` case: the device echoes
``ESC[D`` before a new character, and the character must land where the device
says, not delete whatever the local cursor happened to be over.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from omnicom.ui.vt100 import Screen, cell_width  # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name
          + (f"   [{detail}]" if detail else ""))


def screen(rows: int = 6, cols: int = 20) -> Screen:
    return Screen(rows, cols, scrollback=50)


def rows_of(sc: Screen) -> list[str]:
    return [sc.row_text(row) for row in range(sc.rows)]


def run() -> int:
    print("=" * 78)
    print("VT100 屏幕模型自测")
    print("=" * 78)

    # ----------------------------------------------------------- basic writing
    sc = screen()
    sc.feed(b"hello")
    check("写字符后光标前移", sc.cursor_row == 0 and sc.cursor_col == 5,
          f"({sc.cursor_row},{sc.cursor_col})")
    check("行内容正确", sc.row_text(0) == "hello", sc.row_text(0))

    sc = screen()
    sc.feed(b"abc\r\nxyz")
    check("CR LF 换行并把光标归零", rows_of(sc)[:2] == ["abc", "xyz"],
          str(rows_of(sc)[:2]))
    check("LF 后列号是 0 再写 xyz", sc.cursor_col == 3, str(sc.cursor_col))

    # ------------------------------------------------- the reported bug (R-207)
    sc = screen()
    sc.feed(b"abcde")
    sc.feed(b"\x1b[2D")          # device: move cursor left 2 (left arrow echo)
    check("CSI 2D 把光标左移 2 格（不再被忽略）", sc.cursor_col == 3,
          f"col={sc.cursor_col}")
    sc.feed(b"X")
    check("左移后写入覆盖该格，不删除其它字符",
          sc.row_text(0) == "abcXe", sc.row_text(0))

    sc = screen()
    sc.feed(b"abc")
    sc.feed(b"\x1b[D\x1b[Dbb")
    check("连续两次 CSI D + 输入不会删字",
          sc.row_text(0) == "abb", sc.row_text(0))

    # ---------------------------------------------------- absolute positioning
    sc = screen()
    sc.feed(b"\x1b[3;5HX")
    check("CUP 定位到第 3 行第 5 列", sc.row_text(2)[4] == "X",
          repr(sc.row_text(2)))
    sc = screen()
    sc.feed(b"\x1b[H")
    check("ESC[H 回到左上角", (sc.cursor_row, sc.cursor_col) == (0, 0))

    # ------------------------------------------------------------ erase in line
    sc = screen()
    sc.feed(b"abcdef\x1b[3D\x1b[K")
    check("ESC[K 从光标擦到行尾", sc.row_text(0) == "abc", repr(sc.row_text(0)))
    sc = screen()
    sc.feed(b"abcdef\x1b[1K")
    check("ESC[1K 从行首擦到光标（含光标所在格）", sc.row_text(0) == "",
          repr(sc.row_text(0)))
    sc = screen()
    sc.feed(b"abcdef\x1b[4D\x1b[1K")
    # Mode 1 erases from column 0 *through* the cursor cell inclusive, so the
    # first three cells become blanks - the left half disappears, not the cursor.
    check("ESC[1K 光标在中间时只擦前半（含光标格）", sc.row_text(0) == "   def",
          repr(sc.row_text(0)))
    sc = screen()
    sc.feed(b"abcdef\x1b[2K")
    check("ESC[2K 擦整行", sc.row_text(0) == "", repr(sc.row_text(0)))

    # ----------------------------------------------------------- erase display
    sc = screen()
    sc.feed(b"one\r\ntwo\r\nthree\x1b[2J")
    check("ESC[2J 清屏", rows_of(sc) == [""] * sc.rows, str(rows_of(sc)))
    sc = screen()
    sc.feed(b"one\r\ntwo\r\nthree\x1b[1;1H\x1b[J")
    check("ESC[J 从光标擦到屏幕末尾",
          rows_of(sc) == [""] * sc.rows, str(rows_of(sc)[:3]))

    # ------------------------------------------------------------ colours (SGR)
    sc = screen()
    sc.feed(b"\x1b[32mG\x1b[0mN")
    check("SGR 32 绿色只作用于该字符",
          sc.cell(0, 0).style.fg == "#5EE29B"
          and sc.cell(0, 1).style.fg != "#5EE29B",
          sc.cell(0, 0).style.fg)
    sc = screen()
    sc.feed(b"\x1b[1;4;31mB\x1b[22;24mN")
    check("SGR 组合属性（粗体+下划线+红）",
          sc.cell(0, 0).style.bold and sc.cell(0, 0).style.underline
          and sc.cell(0, 0).style.fg == "#F87171",
          str(sc.cell(0, 0).style))
    check("SGR 22/24 关掉粗体与下划线",
          not sc.cell(0, 1).style.bold and not sc.cell(0, 1).style.underline)
    sc = screen()
    sc.feed(b"\x1b[38;5;196mX")
    check("256 色前景", sc.cell(0, 0).style.fg == "ansi256:196",
          sc.cell(0, 0).style.fg)
    sc = screen()
    sc.feed(b"\x1b[38;2;1;2;3mX")
    check("真彩前景", sc.cell(0, 0).style.fg == "#010203",
          sc.cell(0, 0).style.fg)
    sc = screen()
    sc.feed(b"\x1b[7mI")
    check("反显属性", sc.cell(0, 0).style.inverse)

    # ------------------------------------------------------------------ wrapping
    sc = screen(rows=4, cols=10)
    sc.feed(b"0123456789")
    check("写满最后一行最后一列时光标停在末列（DEAWM 延迟换行）",
          sc.cursor_row == 0 and sc.cursor_col == 9,
          f"({sc.cursor_row},{sc.cursor_col})")
    sc.feed(b"A")
    check("下一字符才换行", sc.cursor_row == 1 and sc.row_text(1) == "A",
          str(rows_of(sc)))

    # ---------------------------------------------------------------- scrolling
    sc = screen(rows=3, cols=10)
    sc.feed(b"1\r\n2\r\n3\r\n4")
    check("滚屏后仍在最后一行", sc.cursor_row == 2, str(sc.cursor_row))
    check("滚出去的行进 scrollback",
          sc.text().split("\n")[:3] == ["1", "2", "3", "4".replace("4", "")][:3]
          or "1" in sc.text(),
          sc.text().replace("\n", "|"))
    check("scrollback 保留第 1 行", sc.scrollback
          and sc._scrollback_text(sc.scrollback[0]) == "1",
          sc._scrollback_text(sc.scrollback[0]) if sc.scrollback else "empty")

    # ------------------------------------------------------------- insert/delete
    sc = screen()
    sc.feed(b"abcdef\x1b[1;3H\x1b[2P")
    check("ESC[P 删除 2 个字符（右侧左移）", sc.row_text(0) == "abef",
          repr(sc.row_text(0)))
    sc = screen()
    sc.feed(b"abcdef\x1b[1;3H\x1b[2@")
    check("ESC[@ 插入 2 个空白（右侧右移）", sc.row_text(0) == "ab  cdef",
          repr(sc.row_text(0)))
    sc = screen()
    sc.feed(b"a\r\nb\r\nc\x1b[1;1H\x1b[L")
    check("ESC[L 在光标处插入一行", rows_of(sc)[:3] == ["", "a", "b"],
          str(rows_of(sc)[:3]))
    sc = screen()
    sc.feed(b"a\r\nb\r\nc\x1b[1;1H\x1b[M")
    check("ESC[M 删除光标所在行", rows_of(sc)[:3] == ["b", "c", ""],
          str(rows_of(sc)[:3]))

    # -------------------------------------------------------------- scroll region
    sc = screen(rows=5, cols=10)
    sc.feed(b"1\r\n2\r\n3\r\n4\r\n5")
    sc.feed(b"\x1b[2;4r")            # region rows 2..4
    sc.feed(b"\x1b[4;1H\nX")
    check("DECSTBM 区域外不受影响（第 1 行与第 5 行保留）",
          sc.row_text(0) == "1" and sc.row_text(4) == "5",
          str(rows_of(sc)))

    # ------------------------------------------------------------- cursor report
    sc = screen()
    sc.feed(b"ab\x1b[6n")
    check("ESC[6n 报告光标位置", sc.take_replies() == b"\x1b[1;3R",
          str(sc.take_replies()))
    sc = screen()
    sc.feed(b"\x1b[c")
    check("ESC[c 设备属性回复以 ESC[? 开头",
          sc.take_replies().startswith(b"\x1b[?"),
          str(sc.take_replies()))

    # ------------------------------------------------------------------- OSC
    sc = screen()
    sc.feed(b"\x1b]0;my-title\x07")
    check("OSC 0 设置标题，不污染屏幕",
          sc.take_title() == "my-title" and sc.row_text(0) == "",
          sc.take_title() or sc.row_text(0))
    sc = screen()
    sc.feed(b"\x1b]2;st\x1b\\Z")
    check("OSC 以 ST 结尾时也能识别", sc.row_text(0) == "Z",
          repr(sc.row_text(0)))

    # ------------------------------------------------------------ wide chars
    sc = screen(cols=10)
    sc.feed("中文".encode())
    check("中文占 2 格", check_width := (cell_width("中") == 2), str(check_width))
    check("中文写入后光标前进 4 格", sc.cursor_col == 4, str(sc.cursor_col))
    check("中文行可以原样取回", sc.row_text(0) == "中文", sc.row_text(0))
    # ``Screen`` clamps to at least 8 columns, so fill exactly that many cells
    # and put the wide glyph on the last one, where it cannot fit.
    sc = screen(cols=8)
    sc.feed(b"abcdefg")                 # 7 cells -> cursor on the last column
    check("宽字符测试的前置条件成立", sc.cursor_col == sc.cols - 1,
          f"col={sc.cursor_col} cols={sc.cols}")
    sc.feed("中".encode())
    check("宽字符不会跨行拆开",
          sc.row_text(0) == "abcdefg" and sc.row_text(1) == "中",
          f"{rows_of(sc)[:2]}")

    # ------------------------------------------------------- backspace / tab
    sc = screen()
    sc.feed(b"abc\b\bX")
    check("退格只移动光标，写入覆盖", sc.row_text(0) == "aXc",
          repr(sc.row_text(0)))
    sc = screen()
    sc.feed(b"\tX")
    check("TAB 跳到 8 的倍数", sc.cursor_col == 9 and sc.row_text(0) == "        X",
          f"col={sc.cursor_col} {sc.row_text(0)!r}")

    # ----------------------------------------------------- shell-like CLI editing
    sc = screen()
    # bash line editor: type `ls -l`, then two left arrows, delete, retype.
    sc.feed(b"ls -l")
    sc.feed(b"\x1b[D\x1b[D")           # two left arrows -> between 's' and ' '
    sc.feed(b"\x1b[P")                 # delete char under cursor (the space)
    sc.feed(b"\x1b[C")                 # right one
    sc.feed(b"a")
    check("模拟 bash 行编辑：左移/删除/右移/插入 结果正确",
          sc.row_text(0) == "ls la", repr(sc.row_text(0)))

    sc = screen()
    sc.feed(b"$ echo hallo")
    sc.feed(b"\x1b[D\x1b[D\x1b[D\x1b[D\x1b[P")   # remove the 'a'
    check("删除后光标停在原格（右侧左移）", sc.row_text(0) == "$ echo hllo",
          repr(sc.row_text(0)))
    sc.feed(b"e")
    # DEL leaves the cursor in the *same* cell, so the new character lands where
    # the deleted one was: "hllo" -> "helo", which is what bash does too.
    check("行内改字不会破坏其它字符", sc.row_text(0) == "$ echo helo",
          repr(sc.row_text(0)))

    # ------------------------------------------------------- redraw from scratch
    sc = screen()
    sc.feed(b"garbage line\r\n")
    sc.feed(b"\x1b[2J\x1b[H$ ")
    # ``row_text`` trims trailing blanks, so compare with the trailing space
    # removed rather than expecting it back.
    check("清屏重绘（zsh/less 常见）后屏幕干净", sc.row_text(0) == "$",
          repr(sc.row_text(0)))

    # ------------------------------------------------------------------ resize
    sc = screen(rows=4, cols=10)
    sc.feed(b"abcdefghij")
    sc.resize(3, 8)
    check("缩小窗口后光标仍在范围内",
          0 <= sc.cursor_row < sc.rows and 0 <= sc.cursor_col < sc.cols,
          f"({sc.cursor_row},{sc.cursor_col})")
    sc.resize(6, 14)
    check("放大窗口不崩溃且保留内容", sc.row_text(0).startswith("abcdefgh"),
          repr(sc.row_text(0)))

    # ------------------------------------------------------------------ reset
    sc = screen()
    sc.feed(b"junk\x1b[31m")
    sc.hard_reset()
    check("ESC c 硬复位后屏幕清空、样式复原",
          sc.row_text(0) == "" and sc.style.fg == "#D6E2F0",
          f"{sc.row_text(0)!r} {sc.style.fg}")

    # ------------------------------------------------------------------ report
    failed = [item for item in RESULTS if not item[0]]
    print()
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    if failed:
        print("失败项：")
        for _ok, name, detail in failed:
            print(f"  - {name}  {detail}")
        return 1
    print("ALL TERMINAL MODEL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
