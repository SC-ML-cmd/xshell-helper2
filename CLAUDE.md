# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Xshell MCP — 一个 MCP (Model Context Protocol) 服务器，让大模型通过 Xshell 终端执行命令。通过文件 IPC 与 Xshell 内运行的 Bridge 脚本通信。

## 命令

```bash
# 安装
pip install -e xshell-mcp

# 运行 MCP Server（stdio 模式）
python -m xshell_mcp

# 运行所有测试
pytest xshell-mcp/tests/

# 运行单个测试文件
pytest xshell-mcp/tests/test_protocol.py

# 运行特定测试类
pytest xshell-mcp/tests/test_output_processor.py::TestCleanCommandOutput -v
```

## 架构

三层通信模型：

```
LLM (Claude) ←→ MCP Server (server.py) ←→ Bridge Client (bridge_client.py) ←→ 文件 IPC ←→ Bridge 脚本 (xshell_bridge_v3.py, 在 Xshell 内运行)
```

### 核心模块

| 模块 | 职责 |
|------|------|
| `server.py` | FastMCP 服务器，定义 6 个工具函数作为外部 API |
| `bridge_client.py` | 通过 `.request.json` / `.response.json` 文件与 Bridge 进行 IPC 通信 |
| `bridge/xshell_bridge_v3.py` | 在 Xshell 内部运行，使用 `xsh.Screen.Send()` 执行命令并轮询 marker 检测输出完成 |
| `protocol.py` | IPC 的 `Request` / `Response` 数据类 |
| `output_processor.py` | 清理 ANSI 转义序列、命令回显、marker 行和提示符，从原始终端输出中提取命令结果 |
| `xshell_launcher.py` | 通过 `Xshell.exe -script <bridge>` 启动 Xshell |
| `config.py` | 基于环境变量的配置（`XSH_XSHELL_PATH`、`XSH_DEFAULT_TIMEOUT` 等） |

### 数据流（命令执行）

1. LLM 调用 MCP 工具 `execute_command("ls -la")`
2. `server.py` 创建带唯一 marker 的 `Request`
3. `bridge_client.py` 将请求写入 `%TEMP%\xshell_mcp\.request.json`
4. Bridge 脚本每 200ms 轮询该文件，检测到变化后读取请求
5. Bridge 检测 shell 类型（CMD → `&`，Bash/PowerShell → `;`），发送 `cmd ; echo MARKER`
6. Bridge 轮询终端直到出现 marker，读取屏幕行作为原始输出
7. Bridge 将 `Response` 写入 `.response.json`
8. `bridge_client.py` 读取响应，`output_processor.py` 清理输出并返回

### IPC 协议

- 文件 IPC 目录：`%TEMP%\xshell_mcp\`（可通过 `XSH_IPC_DIR` 覆盖）
- 请求文件：`.request.json`，响应文件：`.response.json`
- Bridge 客户端在写入新请求前删除旧响应文件，使用 `.tmp` + 原子 rename 保证写入完整性
- 轮询间隔：Bridge 端 200ms，客户端端 100ms

### 配置（环境变量）

- `XSH_XSHELL_PATH` — Xshell.exe 路径
- `XSH_BRIDGE_SCRIPT` — Bridge 脚本路径（默认为 `bridge/xshell_bridge_v3.py`）
- `XSH_IPC_DIR` — IPC 目录（默认为 `%TEMP%\xshell_mcp`）
- `XSH_DEFAULT_TIMEOUT` — 命令超时秒数（默认 30）
- `XSH_SCREEN_COLS` — 屏幕列宽（默认 200）
