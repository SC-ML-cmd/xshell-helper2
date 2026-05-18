# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Xshell MCP — 一个 MCP (Model Context Protocol) 服务器，让大模型通过 Xshell 终端执行命令。通过文件 IPC 与 Xshell 内运行的 Bridge 脚本通信。

**v0.2.0 起支持多会话并发**：多个 Claude Code 窗口可分别绑定到不同的 XShell 页签并发执行命令；XShell 页签关闭 / SSH 断联后自动释放占用。

## 命令

```bash
# 安装
pip install -e xshell-mcp

# 运行 MCP Server（stdio 模式）
python -m xshell_mcp

# 运行 log_config 测试
pytest xshell-mcp/tests/test_log_config.py -v

# 运行所有测试
pytest xshell-mcp/tests/

# 运行单个测试文件
pytest xshell-mcp/tests/test_protocol.py

# 运行特定测试类
pytest xshell-mcp/tests/test_output_processor.py::TestCleanCommandOutput -v
```

## 架构

四层通信模型（多会话）：

```
LLM (Claude) ←→ MCP Server (server.py)
                     │
                     ├── SessionManager (session_manager.py) ── 注册扫描 + CAS 绑定
                     │
                     └── Bridge Client (bridge_client.py) ←→ 文件 IPC ←→ Bridge 脚本 (xshell_bridge_v7.py, 在 XShell 内多页签并发运行)
```

每个 XShell 页签内独立运行 Bridge 脚本，启动时按 `session_<PID>` 写注册文件并将自身页签名也修改为 `session_<PID>`，便于辨认；MCP Server 通过 `connect_session()` 一次性 CAS 绑定到某个会话，之后该 Server 实例只会读写该会话的 IPC 目录。

### 核心模块

| 模块 | 职责 |
|------|------|
| `server.py` | FastMCP 服务器，定义 10 个工具函数：原 6 个执行类（`execute_command` / `send_raw` / `read_screen` 等）+ 新增 4 个会话管理类（`list_sessions` / `connect_session` / `disconnect_session` / `get_bridge_info`），未绑定时执行类工具抛 `SessionNotBoundError` |
| `session_manager.py` | 扫描 `ipc/registry/*.json` 发现 Bridge；`bind()` 采用 CAS（读→检查 `bound_by==0`→写→sleep 50ms→再读校验）防并发抢占；`check_stale_bindings()` 通过 Windows ctypes `kernel32.OpenProcess` 检测 PID 存活回收幽灵占用 |
| `bridge_client.py` | 通过 `.request.json` / `.response.json` 与某个会话 Bridge 通信，`_send_request` 受 `threading.Lock` 保护避免并发 IPC 错乱，接收 `request_id` 做链路追踪 |
| `bridge/xshell_bridge_v7.py` | 在 XShell 页签内运行，启动时 `SESSION_ID = "session_" + str(os.getpid())`，写入 `ipc/registry/<session>.json`，把 `xsh.Session.TabText` 改为 SESSION_ID，定期心跳更新 `last_heartbeat`，退出时清理注册；保留 v6.11 的命令执行 + marker 轮询逻辑 |
| `protocol.py` | IPC 的 `Request` / `Response` 数据类 |
| `output_processor.py` | 清理 ANSI 转义序列、命令回显、marker 行和提示符，从原始终端输出中提取命令结果 |
| `xshell_launcher.py` | 通过 `Xshell.exe -script <bridge>` 启动 XShell；`get_bridge_guidance()` 返回引导用户启动 Bridge + 调用 `connect_session()` 的提示文本 |
| `config.py` | 基于环境变量的配置；新增 `ipc_base` 字段（默认 `<pkg>/ipc`），`bridge_script_path` 默认改为 `xshell_bridge_v7.py` |
| `log_config.py` | 文件日志：`RotatingFileHandler`、`contextvars` 注入 `request_id` + `session_id`、日志脱敏；日志格式 `[asctime] [session_id] [request_id] ...` |
| `exceptions.py` | 多会话异常：`SessionNotFoundError` / `SessionOccupiedError` / `SessionNotBoundError` |

### 数据流

#### 会话绑定流程（启动后一次性）

1. 用户在每个 XShell 页签内运行 `xshell_bridge_v7.py`，Bridge 写注册文件 `ipc/registry/session_<PID>.json` 并把页签名设为 `session_<PID>`
2. MCP Server 启动 → `_init_session_manager()` 扫描 registry → 若仅 1 个空闲会话则自动 CAS 绑定，否则等待 LLM 调用 `list_sessions()` 选择并 `connect_session(session_id)`
3. CAS 绑定：读取注册文件 → 检查 `bound_by == 0` → 写入 `bound_by = <PID>` → sleep 50ms → 再读验证；冲突则抛 `SessionOccupiedError`
4. 绑定成功后 `_bound_session_id` / `_bound_client` 被设置，后续所有命令工具复用此 client

#### 命令执行流程

1. LLM 调用 MCP 工具 `execute_command("ls -la")`
2. `server.py` 生成 `request_id`，注入 `contextvars`（含 `session_id`），记录入口日志
3. 通过 `_bound_client` 写入 `ipc/sessions/session_<PID>/.request.json`
4. Bridge 每 200ms 轮询该文件，发送 `cmd ; echo MARKER`，轮询终端直到出现 marker
5. Bridge 写 `.response.json`，client 读取并清理输出，回工具返回值
6. 命令期间若 `BridgeTimeoutError`，server 二次检查 Bridge 存活，若注册文件缺失或 PID 死亡则解绑

#### 退出与清理

- Bridge 进程退出（页签关闭 / SSH 断联）：`atexit` 删注册文件
- MCP Server 进程退出：`atexit` 调用 `_cleanup_on_exit()` 清除 `bound_by` 标记
- 异常崩溃：下个 Server 启动扫描时通过 PID 存活检测自动回收

### IPC 协议（v0.2.0）

```
<XSH_IPC_BASE>/                 默认 <pkg>/ipc
├── registry/                   会话注册文件目录
│   └── session_<PID>.json      含 bridge_pid / bound_by / last_heartbeat / session_name / tab_text 等字段
└── sessions/
    └── session_<PID>/          每个会话独立的 IPC 目录
        ├── .request.json
        └── .response.json
```

- 注册文件 / 请求 / 响应文件均以 `.tmp` + `os.replace()` 原子写
- 心跳超过 5 分钟视为失联
- 轮询间隔：Bridge 端 200ms，client 端 100ms
- **Legacy 模式**：当 `XSH_IPC_DIR` 已设置且 `XSH_IPC_BASE` 未设置时，回退单会话直连模式（Bridge v7 也会跳过注册流程），确保旧用法仍可用

### 日志系统

- 日志文件：`logs/xshell_mcp.log`（通过 `XSH_LOG_DIR` 可配置）
- 格式：`时间(ms) 级别 [session_id] [request_id] 文件:行号 函数名() | 消息`
- `request_id` 与 `session_id` 通过 `contextvars` 注入，自动串联 server → bridge_client 的同一调用链路
- `RotatingFileHandler`：单文件 500KB，保留 5 个历史文件
- 输出内容只记录长度（`output_len`），不记录原文
- `send_raw` 的 `text` 参数可通过 `XSH_LOG_MASK_SENSITIVE=true` 脱敏

### 配置（环境变量）

- `XSH_XSHELL_PATH` — Xshell.exe 路径
- `XSH_BRIDGE_SCRIPT` — Bridge 脚本路径（默认 `bridge/xshell_bridge_v7.py`）
- `XSH_IPC_BASE` — 多会话 IPC 根目录（默认 `<pkg>/ipc`），下含 `registry/` 和 `sessions/`
- `XSH_IPC_DIR` — Legacy 单会话 IPC 目录；与 `XSH_IPC_BASE` 互斥，仅设此项时进入 legacy 模式
- `XSH_DEFAULT_TIMEOUT` — 命令超时秒数（默认 30）
- `XSH_SCREEN_COLS` — 屏幕列宽（默认 200）
- `XSH_LOG_DIR` — 日志目录（默认为项目根 `logs/`）
- `XSH_LOG_LEVEL` — 日志级别（默认 `INFO`，可选 `DEBUG`/`WARNING`/`ERROR`）
- `XSH_LOG_MASK_SENSITIVE` — 是否脱敏 `send_raw` 内容（默认 `false`，设为 `true` 时 `text` 参数显示为 `***`）

<!-- superpowers-zh:begin (do not edit between these markers) -->
# Superpowers-ZH 中文增强版

本项目已安装 superpowers-zh 技能框架（20 个 skills）。

## 核心规则

1. **收到任务时，先检查是否有匹配的 skill** — 哪怕只有 1% 的可能性也要检查
2. **设计先于编码** — 收到功能需求时，先用 brainstorming skill 做需求分析
3. **测试先于实现** — 写代码前先写测试（TDD）
4. **验证先于完成** — 声称完成前必须运行验证命令

## 可用 Skills

Skills 位于 `.claude/skills/` 目录，每个 skill 有独立的 `SKILL.md` 文件。

- **brainstorming**: 在任何创造性工作之前必须使用此技能——创建功能、构建组件、添加功能或修改行为。在实现之前先探索用户意图、需求和设计。
- **chinese-code-review**: 中文 review 沟通参考——话术模板、分级标注（必须修复/建议修改/仅供参考）、国内团队常见反模式应对。仅在用户显式 /chinese-code-review 时调用，不要根据上下文自动触发。
- **chinese-commit-conventions**: 中文 commit 与 changelog 配置参考——Conventional Commits 中文适配、commitlint/husky/commitizen 中文模板、conventional-changelog 中文配置。仅在用户显式 /chinese-commit-conventions 时调用，不要根据上下文自动触发。
- **chinese-documentation**: 中文文档排版参考——中英文空格、全半角标点、术语保留、链接格式、中文文案排版指北约定。仅在用户显式 /chinese-documentation 时调用，不要根据上下文自动触发。
- **chinese-git-workflow**: 国内 Git 平台配置参考——Gitee、Coding.net、极狐 GitLab、CNB 的 SSH/HTTPS/凭据/CI 接入差异与镜像同步配置。仅在用户显式 /chinese-git-workflow 时调用，不要根据上下文自动触发。
- **dispatching-parallel-agents**: 当面对 2 个以上可以独立进行、无共享状态或顺序依赖的任务时使用
- **executing-plans**: 当你有一份书面实现计划需要在单独的会话中执行，并设有审查检查点时使用
- **finishing-a-development-branch**: 当实现完成、所有测试通过、需要决定如何集成工作时使用——通过提供合并、PR 或清理等结构化选项来引导开发工作的收尾
- **mcp-builder**: MCP 服务器构建方法论 — 系统化构建生产级 MCP 工具，让 AI 助手连接外部能力
- **receiving-code-review**: 收到代码审查反馈后、实施建议之前使用，尤其当反馈不明确或技术上有疑问时——需要技术严谨性和验证，而非敷衍附和或盲目执行
- **requesting-code-review**: 完成任务、实现重要功能或合并前使用，用于验证工作成果是否符合要求
- **subagent-driven-development**: 当在当前会话中执行包含独立任务的实现计划时使用
- **systematic-debugging**: 遇到任何 bug、测试失败或异常行为时使用，在提出修复方案之前执行
- **test-driven-development**: 在实现任何功能或修复 bug 时使用，在编写实现代码之前
- **using-git-worktrees**: 当需要开始与当前工作区隔离的功能开发或执行实现计划之前使用——创建具有智能目录选择和安全验证的隔离 git 工作树
- **using-superpowers**: 在开始任何对话时使用——确立如何查找和使用技能，要求在任何响应（包括澄清性问题）之前调用 Skill 工具
- **verification-before-completion**: 在宣称工作完成、已修复或测试通过之前使用，在提交或创建 PR 之前——必须运行验证命令并确认输出后才能声称成功；始终用证据支撑断言
- **workflow-runner**: 在 Claude Code / OpenClaw / Cursor 中直接运行 agency-orchestrator YAML 工作流——无需 API key，使用当前会话的 LLM 作为执行引擎。当用户提供 .yaml 工作流文件或要求多角色协作完成任务时触发。
- **writing-plans**: 当你有规格说明或需求用于多步骤任务时使用，在动手写代码之前
- **writing-skills**: 当创建新技能、编辑现有技能或在部署前验证技能是否有效时使用

## 如何使用

当任务匹配某个 skill 时，使用 `Skill` 工具加载对应 skill 并严格遵循其流程。绝不要用 Read 工具读取 SKILL.md 文件。

如果你认为哪怕只有 1% 的可能性某个 skill 适用于你正在做的事情，你必须调用该 skill 检查。
<!-- superpowers-zh:end -->
