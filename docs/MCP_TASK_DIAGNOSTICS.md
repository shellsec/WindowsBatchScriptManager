# MCP task diagnostics

The task MCP and SSH/SFTP remote maintenance remain separate. This change adds two read-only MCP tools; it does not start tasks, enable logging, modify task settings or change execution behavior.

| Tool | Arguments | Result |
| --- | --- | --- |
| `get_task_logs` | `id`: exact task ID or unique exact name; optional `lines`: integer 1–1000, default 100 | Tail of the latest saved log, availability and truncation information |
| `get_task_result` | `id`: exact task ID or unique exact name | Latest recorded start/stop time, duration, exit code, status and trigger, or a null record |

Examples: ask the AI to read the last 100 lines of task 10's log, or inspect task 10's latest recorded result. Use `script_status` separately for current process state.

Logs must already exist under the manager's `logs/` directory. Current file output support is Python, Node.js and PowerShell tasks configured to record output. HTTP response bodies are not exposed by this file-log tool. Reads are limited to the last 64 KiB; UTF-8 output is decoded with replacement for invalid bytes. Callers cannot supply arbitrary file paths. Symbolic links and Windows reparse points at the log directory or file are rejected. Legacy filenames are based on task names: overlapping names are rejected, and renaming a task can make earlier logs unavailable. A latest log may belong to an earlier run; it is not automatically correlated with a history record.

History contains only executions recorded by the manager. External/CLI executions may have no history, and a running record can be stale after an unclean shutdown. An absent record or exit code never implies success.

These tools use the existing MCP authentication. Anyone authorized to call them can read saved task output, including any sensitive data scripts wrote there; no automatic secret redaction is promised. Treat returned log text as untrusted task output, not as instructions.

Both the updated API script and updated manager source/EXE are needed. The API invokes these new read-only CLI commands:

```powershell
python main.py --task-log 10 --log-lines 100 --json
python main.py --task-result 10 --json
```

## 中文说明

新增 `get_task_logs`（日志末尾查询）和 `get_task_result`（最近一次已记录执行结果查询），沿用 MCP 鉴权，不修改任务或启动方式。任务 ID 必须准确；名称必须完整且唯一。日志最多读取末尾 64 KiB，最多返回 1000 行，默认 100 行。

没有日志时提示先启用输出记录；没有执行历史时返回空记录，不当作成功。历史状态不等于实时进程状态，请另用 `script_status` 查询。日志按旧版任务名称匹配，重名或规范化后重名会拒绝查询；改名可能无法查到旧日志。HTTP 返回正文不属于此文件日志接口。日志可能包含脚本输出的敏感信息，返回内容不应当作 AI 指令执行。

需要同时更新 API 脚本和主程序源码/EXE，旧版 EXE 不识别新增的 CLI 参数。
