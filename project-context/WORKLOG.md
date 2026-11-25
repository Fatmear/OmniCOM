# Worklog (newest first, live window)

One row per meaningful step, written in the same turn as the change.
Status: `done <sha>` | `open` | `in-progress` | `reverted <sha>` | `shipped <version>`
Keep the newest ~40 rows live; move older blocks to `archive/worklog-YYYY.md`.

| When | Area | What changed | Status |
| --- | --- | --- | --- |
| 11-25 | ui | 左栏顺序对调：窗口节移到数据记录之上（清除窗口/保存日志在上，开始记录/打开目录/清空记录在下） | done |
| 11-25 | docs | Added `PRODUCT.md`: scope tables (`S-1.1`..`S-9.4`, 45 rows), 6 `OUT` rows, scope-change log | done |
| 11-25 | docs | Scope discipline written into skill + `AGENTS.md`: add a feature = new `S-` row with a criterion; cancel a feature = **delete the row** + one change-log line | done |
| 11-25 | docs | `PRODUCT.md` wired into `README` read order, report budget (200), validator, and `templates.md` | done |
| 11-25 | build | 记录 R-342 打包门禁与 1.1.7 重发 (R-342) | done |
| 11-25 | tls | 把证书校验固化为打包门禁，并写入 AGENTS 约束 (R-342) | done |
| 11-25 | power | 实测确认关闭软件后不残留常亮 (R-343) | done |
| 11-25 | tls | 随包 CA 证书，修复换机器后 HTTPS 校验失败 (R-342) | done |
| 11-24 | chart | 记录 chart 1.1.5 重发结果 (R-341) | done |
| 11-24 | chart | 修复数据来源选择框悬停背景变黑 (R-341) | done |
| 11-24 | chart | 记录 chart 1.1.5 发布结果与 BOM/CRLF 经验 (R-336) | done |
| 11-24 | chart | 关闭绘制层自动抽点，历史区间逐点保持原样 (R-336 / chart 1.1.5) | done |
| 11-24 | docs | 记录 v1.1.7 第五次重发的资产与发布教训 | done |
| 11-24 | chart | 刷新 chart 1.1.4 目录哈希，并让发布脚本按内容判定是否重传 | done |
| 11-24 | chart | 满缓存波形抖动改用 minmax、框选整组拖动、清空数据与清空看板归位 | done |
| 11-24 | chart | 打开日志后看板错图，日志时间轴毫秒与墙钟优先级 | done |
| 11-24 | docs | 记录 1.1.7 第四次重发结果与上传中断教训 | done |
| 11-24 | store | 核心插件改为启用/禁用，修复启用无效与操作时闪现空白小窗 | done |
| 11-23 | docs | 记录 1.1.7 三次重发结果并补登记 R-301 | done |
| 11-23 | chart | 把本轮看板修复并入 1.1.7 发布说明 | done |
| 11-23 | chart | 上架看板 1.1.4（数值颜色映射灯） | done |
| 11-23 | chart | 指示灯支持数值颜色映射，修复多来源交互并重发 1.1.7 | done |
| 11-23 | docs | 完成 v1.1.7 重发核验与缺陷交接 | done |
| 11-23 | core | 修复帮助菜单显示键名与停用插件无法恢复，重发 1.1.7 | done |
| 11-20 | docs | 记录 1.1.7 发布结果与插件解耦台账 | done |
| 11-20 | core | 插件与宿主彻底解耦，发布 1.1.7 | done |
| 11-19 | docs | 记录 v1.1.6 已发布与最终验证结果 | done |
| 11-19 | docs | 1.1.6 补记终端网格尺寸根因与按键丢弃修复 | done |

Archived: nothing yet (this file was created 2026-11-25 from `git log`, so
the 24 most recent commits are the starting window).
