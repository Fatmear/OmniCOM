"""Plugin-owned translations (R-322).

The host table used to carry these strings; a plugin now ships its own
copy and registers it through ``api.register_text()``, so uninstalling
the plugin leaves no orphaned text behind and the host table stays
about the host.
"""

from __future__ import annotations

TEXT: dict[str, tuple[str, str]] = {
    'hex.badbytes': ('要写入的字节不是合法十六进制', 'The bytes to write are not valid hex'),
    'hex.badoffset': ('偏移量格式不对', 'That is not a valid offset'),
    'hex.batch': ('批量修改', 'Batch edit'),
    'hex.batchconfirm': ('从偏移 {offset} 开始处理 {n} 字节：', 'At offset {offset}, {n} bytes:'),
    'hex.batchdone': ('已写入，文件现有 {n} 字节', 'Written; the file now has {n} bytes'),
    'hex.batchinsert': ('插入', 'Insert'),
    'hex.batchoverwrite': ('覆盖', 'Overwrite'),
    'hex.copied': ('已复制选中内容', 'Selection copied'),
    'hex.copy': ('复制选中内容', 'Copy selection'),
    'hex.edit': ('写入字节', 'Write bytes'),
    'hex.editconfirm': ('从偏移 {offset} 起写入 {n} 字节？', 'Write {n} bytes at offset {offset}?'),
    'hex.find': ('查找', 'Find'),
    'hex.findhint': ('文本或十六进制，如 48 65 6C', 'Text or hex, e.g. 48 65 6C'),
    'hex.findnext': ('查找下一个', 'Find next'),
    'hex.goto': ('跳转', 'Go to'),
    'hex.gotohint': ('偏移量（0x10 或 16）', 'Offset (0x10 or 16)'),
    'hex.next': ('下一页', 'Next'),
    'hex.nofile': ('未打开文件', 'No file open'),
    'hex.notfound': ('找不到', 'Not found'),
    'hex.open': ('打开文件…', 'Open file…'),
    'hex.prev': ('上一页', 'Previous'),
    'hex.progress': ('拖动跳转到指定位置', 'Drag to jump to a position'),
    'hex.save': ('保存', 'Save'),
    'hex.saveas': ('另存为…', 'Save as…'),
    'hex.saved': ('已保存', 'Saved'),
    'hex.selectall': ('全选', 'Select all'),
    'hex.status': ('{name} · 第 {page}/{pages} 页 · {first}–{last} / 共 {total} 字节', '{name} · page {page}/{pages} · {first}–{last} of {total} bytes'),
}
