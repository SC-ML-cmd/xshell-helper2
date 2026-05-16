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

三层通信模型：

```
LLM (Claude) ←→ MCP Server (server.py) ←→ Bridge Client (bridge_client.py) ←→ 文件 IPC ←→ Bridge 脚本 (xshell_bridge_v6.11.py, 在 Xshell 内运行)
```

### 核心模块

| 模块 | 职责 |
|------|------|
| `server.py` | FastMCP 服务器，定义 6 个工具函数作为外部 API，每个调用生成 `request_id` 并记录入口/出口日志 |
| `bridge_client.py` | 通过 `.request.json` / `.response.json` 文件与 Bridge 进行 IPC 通信，接收 `request_id` 传入 `_send_request` 做链路追踪 |
| `bridge/xshell_bridge_v6.11.py` | 在 Xshell 内部运行，使用 `xsh.Screen.Send()` 执行命令并轮询 marker 检测输出完成 |
| `protocol.py` | IPC 的 `Request` / `Response` 数据类 |
| `output_processor.py` | 清理 ANSI 转义序列、命令回显、marker 行和提示符，从原始终端输出中提取命令结果 |
| `xshell_launcher.py` | 通过 `Xshell.exe -script <bridge>` 启动 Xshell |
| `config.py` | 基于环境变量的配置（路径、超时、日志等） |
| `log_config.py` | 文件日志配置：`RotatingFileHandler`（500KB/5 文件）、`contextvars` 注入 `request_id`、日志脱敏 |

### 数据流（命令执行）

1. LLM 调用 MCP 工具 `execute_command("ls -la")`
2. `server.py` 生成 `request_id`（格式 `ttttt-nnnnn`），注入 `contextvars`，记录入口日志（cmd、timeout）
3. `server.py` 将 `request_id` 传入 `bridge_client.execute()`，创建带唯一 marker 的 `Request`
4. `bridge_client.py` 将请求写入 `%TEMP%\xshell_mcp\.request.json`，记录 IPC 请求日志
5. Bridge 脚本每 200ms 轮询该文件，检测到变化后读取请求
6. Bridge 检测 shell 类型（CMD → `&`，Bash/PowerShell → `;`），发送 `cmd ; echo MARKER`
7. Bridge 轮询终端直到出现 marker，读取屏幕行作为原始输出
8. Bridge 将 `Response` 写入 `.response.json`
9. `bridge_client.py` 读取响应，记录 IPC 响应日志（success、output_len、elapsed）
10. `server.py` 的 `output_processor.py` 清理输出，记录出口日志（elapsed、output_len、timed_out）

### IPC 协议

- 文件 IPC 目录：`%TEMP%\xshell_mcp\`（可通过 `XSH_IPC_DIR` 覆盖）
- 请求文件：`.request.json`，响应文件：`.response.json`
- Bridge 客户端在写入新请求前删除旧响应文件，使用 `.tmp` + 原子 rename 保证写入完整性
- 轮询间隔：Bridge 端 200ms，客户端端 100ms

### 日志系统

- 日志文件：`logs/xshell_mcp.log`（通过 `XSH_LOG_DIR` 可配置）
- 格式：`时间(ms) 级别 [request_id] 文件:行号 函数名() | 消息`
- `request_id` 通过 `contextvars` 注入，自动串联 server → bridge_client 的同一调用链路
- `RotatingFileHandler`：单文件 500KB，保留 5 个历史文件
- 输出内容只记录长度（`output_len`），不记录原文
- `send_raw` 的 `text` 参数可通过 `XSH_LOG_MASK_SENSITIVE=true` 脱敏

### 配置（环境变量）

- `XSH_XSHELL_PATH` — Xshell.exe 路径
- `XSH_BRIDGE_SCRIPT` — Bridge 脚本路径（默认为 `bridge/xshell_bridge_v6.11.py`）
- `XSH_IPC_DIR` — IPC 目录（默认为 `%TEMP%\xshell_mcp`）
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
