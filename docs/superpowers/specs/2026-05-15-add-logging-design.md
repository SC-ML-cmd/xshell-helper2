# Xshell MCP 文件日志系统 — 设计规格

## 动机

当前 MCP Server 只有启动时的 7 条 console 日志，6 个工具函数和 Bridge IPC 通信完全没有日志。无法追踪调用者行为、排查问题、分析性能。

## 方案

方案 C（集中式日志模块 + request_id 链路追踪）：新建 `log_config.py`，封装 RotatingFileHandler；在 `server.py` 和 `bridge_client.py` 关键路径手动埋点；每条日志带 `request_id` 串联单次调用链路。

## 日志格式

纯文本，每行一条：

```
2026-05-15 10:30:01.123 INFO  [34567-00042] server.py:49 execute_command() | cmd="ls -la" timeout=30
2026-05-15 10:30:01.234 INFO  [34567-00042] bridge_client.py:71 _send_request() | 写入请求 exec marker=__XSH_1747295401123456
2026-05-15 10:30:01.567 INFO  [34567-00042] bridge_client.py:79 _send_request() | 收到响应 success=True output_len=2048 elapsed=0.45s
2026-05-15 10:30:01.568 INFO  [34567-00042] server.py:68 execute_command() | 完成 elapsed=0.45s output_len=2048 timed_out=False
```

格式：`时间(ms) 级别 [request_id] 文件:行号 函数名() | 消息`

- 输出内容只记录 `output_len`，不记录原文（避免日志膨胀）
- 参数原样记录，`send_raw` 的 `text` 在脱敏模式打开时替换为 `***`

## 新增文件

### `log_config.py`

- `setup_logging(log_dir, level)` — 配置 RotatingFileHandler，返回 root logger
- `get_logger(name)` — 获取具名 logger
- `generate_request_id()` — 生成 8 位短 ID（格式 `ttttt-nnnnn`，如 `34567-00042`），线程安全

## 修改文件

### `server.py`

每个工具函数添加入口/出口日志：

| 函数 | 入口日志 | 出口日志 |
|------|---------|---------|
| `check_bridge` | — | `bridge_online=<bool>` |
| `execute_command` | `cmd`、`timeout` | `elapsed`、`output_len`、`timed_out` |
| `send_raw` | `text`(可脱敏)、`wait_for` | `elapsed`、`output_len` |
| `interrupt` | — | `success` |
| `get_screen` | `lines` | `timed_out`、`output_len`、`screen_rows/cols` |
| `get_session_info` | — | `screen_rows/cols` |

异常路径（超时、异常）同样记录。

### `bridge_client.py`

`request_id` 传递链：`server.py` 工具函数生成 id → 传给 `bridge_client` 公开方法 → 传给 `_send_request`。

- `execute`、`send_raw`、`get_screen`、`interrupt`、`check_bridge` 方法签名均新增 `request_id: str = ""` 参数
- `_send_request` 方法签名新增 `request_id: str = ""` 参数
- 各公开方法在调用 `_send_request` 时传入 `request_id`
- 日志点：写入请求文件 → 收到响应（或超时）

`_send_request` 日志示例：
```
[rid] bridge_client.py:71 _send_request() | 写入请求 exec cmd[:80]
[rid] bridge_client.py:79 _send_request() | 收到响应 success=True output_len=2048
[rid] bridge_client.py:79 _send_request() | 超时 timeout=30s
```

### `config.py`

新增配置字段 `log_dir`、`log_level`、`log_mask_sensitive`：

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `XSH_LOG_DIR` | `<项目根>/logs/` | 日志目录 |
| `XSH_LOG_LEVEL` | `INFO` | 日志级别 |
| `XSH_LOG_MASK_SENSITIVE` | `false` | 为 `true` 时 `send_raw` 的 `text` 脱敏为 `***` |

### `__main__.py`

启动时调用 `setup_logging()` 替代现有 `logging.basicConfig`，移除 console 日志（全部进文件）。

## 轮转策略

- 使用 `RotatingFileHandler`
- 单文件最大 500KB（`maxBytes=500*1024`）
- 保留最近 5 个文件（`backupCount=5`）
- 文件名：`xshell_mcp.log`、`xshell_mcp.log.1` ... `xshell_mcp.log.5`

## 脱敏逻辑

仅作用于 `send_raw` 的 `text` 参数。`log_mask_sensitive=True` 时日志中 `text=***`。

## 不做的

- 不记录 response 原文内容（只记长度），避免日志膨胀
- 不引入第三方日志库（只用 stdlib `logging`）
- 不修改 Bridge 脚本端日志（`xshell_bridge_v6.11.py`），那是 Xshell 内部运行的脚本，文件写入权限受限
- 不影响现有 MCP 工具函数的返回值结构

## 测试

- 现有 `pytest` 测试套件必须全部通过
- 新增或扩展测试验证日志文件写入、轮转、脱敏行为（可选，不强制）
