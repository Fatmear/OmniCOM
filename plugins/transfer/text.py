"""Plugin-owned translations (R-322).

The host table used to carry these strings; a plugin now ships its own
copy and registers it through ``api.register_text()``, so uninstalling
the plugin leaves no orphaned text behind and the host table stays
about the host.
"""

from __future__ import annotations

TEXT: dict[str, tuple[str, str]] = {
    'xfer.abort': ('中止', 'Abort'),
    'xfer.busy': ('上一次传输还没结束', 'A transfer is still running'),
    'xfer.clearlog': ('清空', 'Clear'),
    'xfer.direction': ('方向', 'Direction'),
    'xfer.done': ('完成', 'Done'),
    'xfer.dropzone': ('点击选择文件，或把文件拖到这里', 'Click to choose a file, or drop it here'),
    'xfer.failed': ('失败', 'Failed'),
    'xfer.log': ('传输日志', 'Transfer log'),
    'xfer.pickfile': ('请先选择要发送的文件', 'Choose a file to send first'),
    'xfer.protocol': ('协议', 'Protocol'),
    'xfer.ready': ('就绪', 'Ready'),
    'xfer.recv': ('从设备接收', 'Receive from device'),
    'xfer.saved': ('已保存：{path}', 'Saved: {path}'),
    'xfer.savedir': ('接收到的文件保存到 {path}', 'Received files are saved to {path}'),
    'xfer.send': ('发送到设备', 'Send to device'),
    'xfer.start': ('开始传输', 'Start transfer'),
    'xfer.needport': ('请先打开串口', 'Open a port first'),
    'xfer.waiting': ('等待设备发起传输…', 'Waiting for the device to start…'),
}
