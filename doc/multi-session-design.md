# XShell MCP 多会话支持 — 设计方案

> 版本：v2.0  
> 日期：2026-05-16  
> 状态：待确认

---

## 1. 背景与问题

### 当前架构（单会话）

```
Claude Code 窗口 ──(stdio)──→ MCP Server ──(文件 IPC)──→ Bridge 脚本 ──(COM)──→ XShell 单 tab
                                │                              │
                           全局单例 _client             单一 .request.json / .response.json
```

### 核心问题

1. **多个 Claude Code 窗口共享同一个 XShell tab**：每个 Claude Code 窗口启动独立的 MCP Server 进程（stdio 模式），但所有进程通过同一个 IPC 目录与同一个 bridge 通信，命令会串到同一个 XShell tab
2. **XShell 是单实例应用**（已验证）：无法启动多个 XShell.exe 实例
3. **用户需求**：每个 Claude Code 窗口绑定一个 XShell tab，一对一持续使用，不需要每次指定会话

### 已确认的前提

- ✅ XShell 是单实例应用，无法多开（已通过测试验证：第二次启动 Xshell.exe 时新进程立即退出）
- ✅ XShell 一个页签（tab）= 一个会话（session）
- ✅ 每个会话可以运行一个脚本，脚本绑定到该 tab 的 `xsh.Session` / `xsh.Screen`
- ✅ 每个 Claude Code 窗口启动一个独立的 MCP Server 进程

---

## 2. 方案概述：Bridge 注册制 + 一次性绑定

### 核心思路

1. **Bridge 注册**：用户在 XShell 每个 tab 中运行 bridge 脚本，bridge 启动时自动注册自己（写入注册文件）
2. **一次性绑定**：Claude Code 窗口打开后，调用 `connect_session` 选择一个 XShell tab 绑定
3. **持续使用**：绑定后，该窗口的所有命令自动路由到绑定的 session，无需再传 session_id
4. **断联释放**：任一端断开（Claude Code 关闭 / XShell tab 关闭 / SSH 断联），绑定自动解除，session 释放给其他窗口使用

### 架构图

```
┌─────────────────────────┐  ┌─────────────────────────┐
│  Claude Code 窗口 A     │  │  Claude Code 窗口 B     │
│  (绑定 session_92292)   │  │  (绑定 session_78431)   │
└──────────┬──────────────┘  └──────────┬──────────────┘
           │ stdio                      │ stdio
           ▼                            ▼
┌─────────────────────────┐  ┌─────────────────────────┐
│  MCP Server 进程 1      │  │  MCP Server 进程 2      │
│  _bound_session_id =    │  │  _bound_session_id =    │
│    "session_92292"      │  │    "session_78431"      │
│  _bound_client =        │  │  _bound_client =        │
│    BridgeClient(...)    │  │    BridgeClient(...)    │
└──────────┬──────────────┘  └──────────┬──────────────┘
           │                            │
           │  xshell-mcp/ipc/sessions/  │
           │  ├── session_92292/        │
           │  │   ├── .request.json     │
           │  │   └── .response.json    │
           │  └── session_78431/        │
           │      ├── .request.json     │
           │      └── .response.json    │
           │                            │
           ▼                            ▼
┌──────────────────────────────────────────────────────┐
│  XShell.exe（单实例，多个 tab）                        │
│                                                       │
│  Tab1 ←→ Bridge PID 92292（IPC: session_92292/）      │
│  Tab2 ←→ Bridge PID 78431（IPC: session_78431/）      │
│  Tab3 ←→ Bridge PID 45016（IPC: session_45016/）未绑定 │
└──────────────────────────────────────────────────────┘
```

### 注册目录

```
xshell-mcp/ipc/registry/
├── session_92292.json     ← Tab1 的 bridge 注册信息（已绑定）
├── session_78431.json     ← Tab2 的 bridge 注册信息（已绑定）
└── session_45016.json     ← Tab3 的 bridge 注册信息（空闲）
```

---

## 3. 详细设计

### 3.1 目录结构

```
xshell-mcp/ipc/
├── registry\                          ← 注册目录
│   ├── session_92292.json             ← 每个 bridge 的注册信息
│   ├── session_78431.json
│   └── session_45016.json
├── sessions\                          ← IPC 目录
│   ├── session_92292\
│   │   ├── .request.json
│   │   ├── .response.json
│   │   └── bridge.log
│   ├── session_78431\
│   │   ├── .request.json
│   │   ├── .response.json
│   │   └── bridge.log
│   └── session_45016\
│       ├── .request.json
│       ├── .response.json
│       └── bridge.log
```

### 3.2 注册文件格式

路径：`xshell-mcp/ipc/registry/session_<pid>.json`

```json
{
  "session_id": "session_92292",
  "remote_address": "192.168.1.1",
  "remote_port": 22,
  "local_address": "10.0.0.5",
  "session_path": "C:/Users/.../ali_ecs.xsh",
  "session_name": "ali_ecs",
  "tab_text": "ali_ecs - root@192.168.1.1",
  "user_name": "root",
  "connected": true,
  "ipc_dir": "d:/dev/.../xshell-mcp/ipc/sessions/session_92292",
  "pid": 92292,
  "started_at": "2026-05-16T21:17:04",
  "last_heartbeat": "2026-05-16T21:22:13",

  "bound_by": 12345,
  "bound_at": "2026-05-16T21:30:00"
}
```

字段说明：

| 字段 | 说明 |
|------|------|
| `session_id` | Bridge 的唯一标识，格式 `session_<PID>` |
| `remote_address` | XShell 连接的远程地址（来自 `xsh.Session.RemoteAddress`） |
| `remote_port` | 远程端口（来自 `xsh.Session.RemotePort`） |
| `local_address` | 本地地址（来自 `xsh.Session.LocalAddress`） |
| `session_path` | XShell session 文件路径（来自 `xsh.Session.Path`），可为空 |
| `session_name` | XShell 会话名称（来自 `xsh.Session.SessionName`） |
| `tab_text` | XShell 页签显示文本（来自 `xsh.Session.TabText`） |
| `user_name` | 登录用户名（来自 `xsh.Session.UserName`） |
| `connected` | 连接状态（来自 `xsh.Session.Connected`） |
| `ipc_dir` | 该 bridge 的 IPC 目录绝对路径 |
| `pid` | Bridge 脚本进程 PID |
| `started_at` | Bridge 启动时间 |
| `last_heartbeat` | 最后心跳时间（每 100 轮主循环更新一次） |
| `bound_by` | 占用者的 MCP Server 进程 PID，0 表示未占用 |
| `bound_at` | 绑定时间 |

### 3.3 session_id 生成规则

**以 PID 为主**，因为：
- 同一台 XShell 中多个 tab 连同一台虚拟机很常见，用 session 文件名或 IP 地址容易冲突
- PID 天然唯一，每个 bridge 脚本运行在不同的进程空间

```
session_id = "session_" + str(os.getpid())
```

注册文件中的 `remote_address` / `session_path` 信息供用户辨识，不参与 ID 生成。

### 3.4 Bridge 端改动

#### 3.4.1 启动流程（新增）

```
Main() 启动后：
  1. session_id = "session_" + str(os.getpid())
  2. 创建 IPC 目录  xshell-mcp/ipc/sessions/<session_id>/
  3. 收集 session 信息（remote_address, remote_port, session_path 等）
  4. 写注册文件     xshell-mcp/ipc/registry/<session_id>.json
  5. 进入主循环
```

#### 3.4.2 心跳更新（新增）

Bridge 在主循环的 heartbeat 逻辑中（每 100 轮迭代），更新注册文件的 `last_heartbeat` 字段：

```python
if iteration % CFG.HEARTBEAT_INTERVAL == 0:
    _update_registry_heartbeat()   # 新增
    _log("MAIN heartbeat ...")
```

#### 3.4.3 退出清理（新增）

Bridge 退出时删除自己的注册文件：

```python
# Main() 退出前
_remove_registry()
```

#### 3.4.4 IPC_DIR 变更

当前：
```python
IPC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ipc")
# 可被 XSH_IPC_DIR 环境变量或命令行参数覆盖
```

改为：
```python
# 1. 确定 session_id
SESSION_ID = "session_" + str(os.getpid())

# 2. 确定 IPC 根目录（与 config.py 中的逻辑一致）
IPC_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ipc")

# 3. 如果有环境变量覆盖，使用环境变量
if os.environ.get("XSH_IPC_BASE"):
    IPC_BASE = os.environ["XSH_IPC_BASE"]

# 4. 基于 session_id 创建子目录
IPC_DIR = os.path.join(IPC_BASE, "sessions", SESSION_ID)
REGISTRY_DIR = os.path.join(IPC_BASE, "registry")
REQ_FILE = os.path.join(IPC_DIR, ".request.json")
RESP_FILE = os.path.join(IPC_DIR, ".response.json")
LOG_FILE = os.path.join(IPC_DIR, "bridge.log")
```

#### 3.4.5 向后兼容

如果 `XSH_IPC_DIR` 环境变量存在（旧方式），直接使用该目录，不创建 sessions 子目录，不走注册机制。这样旧用户升级后不受影响。

### 3.5 MCP Server 端改动

#### 3.5.1 绑定状态（核心变更）

```python
# 旧：全局单例
_client: BridgeClient | None = None

# 新：绑定模式
_bound_session_id: str | None = None        # 当前绑定的 session_id
_bound_client: BridgeClient | None = None   # 对应的 BridgeClient
_session_manager: SessionManager             # 管理 session 发现/注册/占用
```

#### 3.5.2 Session 管理器（新增 `session_manager.py`）

```python
class SessionManager:
    """管理 XShell session 的发现、注册、绑定"""

    def __init__(self, ipc_base: str, timeout: int):
        self._ipc_base = ipc_base          # xshell-mcp/ipc/
        self._timeout = timeout

    def discover(self) -> list[dict]:
        """扫描 registry 目录，返回所有已注册 bridge 的信息列表"""

    def list_available(self) -> list[dict]:
        """返回所有可绑定（未被占用）的 session 列表"""

    def is_available(self, session_id: str) -> bool:
        """检查指定 session 是否可绑定（注册文件存在 + 未被占用 + bridge 存活）"""

    def bind(self, session_id: str, mcp_pid: int) -> BridgeClient:
        """绑定指定 session：写入占用标记、创建 BridgeClient"""

    def unbind(self, session_id: str):
        """解除绑定：清除占用标记"""

    def check_stale_bindings(self):
        """清理幽灵占用：检查占用的 MCP Server 进程是否还活着，不活着则释放"""

    def _is_bridge_alive(self, session_id: str) -> bool:
        """检查 bridge 是否存活（注册文件存在 + heartbeat 未过期 + check_bridge 通过）"""

    @staticmethod
    def _is_process_alive(pid: int) -> bool:
        """检查指定 PID 的进程是否还在运行（用于清理幽灵占用）"""
```

#### 3.5.3 工具变更

**新增工具**：

| 工具 | 说明 |
|------|------|
| `connect_session(session_id: str)` | 绑定一个 XShell 会话（CAS 并发安全）。session_id 为空时自动绑定唯一空闲会话。绑定后该窗口所有命令自动路由到此 session |
| `disconnect_session()` | 断开当前绑定，释放占用标记 |
| `list_sessions()` | 列出所有 XShell 会话（含 PID、远程地址、占用状态） |
| `get_bridge_info()` | 返回当前绑定的 XShell 会话信息（session_id、bridge PID、远程地址等），用于了解当前 Claude Code 窗口对应哪个 XShell 页签 |

**现有工具不变**：

| 工具 | 说明 |
|------|------|
| `execute_command` | 无需 session_id 参数，自动路由到绑定的 session |
| `send_raw` | 同上 |
| `interrupt` | 同上 |
| `get_screen` | 同上 |
| `get_session_info` | 同上 |
| `check_bridge` | 检查当前绑定的 bridge 是否在线 |

#### 3.5.4 绑定流程

```
Claude Code 窗口打开 → MCP Server 进程启动

1. 自动扫描 registry，发现已注册的 bridge

2. 未绑定时，任何命令工具调用返回：
   "尚未绑定 XShell 会话，请先调用 list_sessions() 查看可用会话，再调用 connect_session(session_id) 绑定"

2.5 自动绑定（MCP Server 启动后首次调用 connect_session() 时）：
   - 扫描 registry，过滤空闲 session
   - 只有 1 个空闲 session → 自动绑定（CAS 保护，失败则提示）
   - 多个空闲 session → 列出供用户选择
   - 0 个空闲 session → 返回 "所有会话已被占用或无可用会话"

3. 用户选择后调用 connect_session("session_92292"):
   a. 检查 session 是否可绑定
      - 注册文件不存在 → 报错 "会话不存在"
      - 已被占用（bound_by != 0 且该进程还活着）→ 报错 "会话已被占用"
      - bridge 不存活 → 报错 "会话已断开"
   b. CAS 写入占用标记：
      i.   读取注册文件，确认 bound_by == 0
      ii.  写入 bound_by=mcp_pid, bound_at=now
      iii. 短暂等待（50ms）
      iv.  再次读取注册文件，验证 bound_by == mcp_pid
      v.   如果不匹配（被其他 MCP Server 抢占）→ 报错 "会话绑定冲突，请重试"
   c. 创建 BridgeClient，指向该 session 的 IPC 目录
   d. 设置 _bound_session_id 和 _bound_client
   e. 返回绑定成功信息

4. 绑定后，所有命令自动路由：
   execute_command("ls") → _bound_client.execute(...) → session_92292/.request.json
```

#### 3.5.5 断联处理

MCP Server 每次工具调用时：

```
1. 检查 _bound_client 是否存在
   - 不存在 → 返回 "尚未绑定 XShell 会话，请先调用 connect_session()"

2. 执行命令
   - 成功 → 返回结果
   - BridgeTimeoutError → 二次检查 bridge 存活
     - 不存活 → 清除绑定，返回 "XShell 会话已断开（tab 可能已关闭），请调用 list_sessions() 重新选择"
     - 存活 → 正常超时返回
```

#### 3.5.6 占用标记的生命周期

```
┌──────────────────────────────────────────────────────────────────┐
│  注册文件中的占用标记                                             │
│                                                                  │
│  bound_by: 0 (未占用)                                            │
│      │                                                           │
│      ▼ connect_session()                                         │
│  bound_by: 12345 (被 MCP Server PID 12345 占用)                  │
│      │                                                           │
│      ├── disconnect_session() → bound_by: 0                      │
│      │                                                           │
│      ├── MCP Server 进程退出 → atexit 清理 → bound_by: 0         │
│      │                                                           │
│      └── MCP Server 进程崩溃（未清理）→ 幽灵占用                   │
│            │                                                     │
│            ▼ check_stale_bindings() 检测到 PID 12345 不存在       │
│            → 自动清除 bound_by → 0                               │
└──────────────────────────────────────────────────────────────────┘
```

**幽灵占用清理**：每次 `list_sessions()` 和 `connect_session()` 时，检查所有注册文件中的 `bound_by` PID 是否还活着，不活着则自动释放。

#### 3.5.7 启动逻辑变更

`__main__.py` 中：

```python
def main():
    # 旧: init_bridge() → 尝试启动 XShell + 全局 _client
    # 新: session_manager.discover() → 发现已注册的 bridge
    #     不再自动启动 XShell（单实例，启动了也没用）
    #     如果没有发现 bridge → 提示用户手动在 XShell 中运行脚本
    #     MCP Server 继续运行，等待用户通过 connect_session() 绑定
```

不再自动启动 XShell。改为：
1. 扫描 registry，发现已注册的 bridge
2. 如果有 bridge → 等待 Claude Code 调用 `connect_session()` 绑定
3. 如果没有 → 打印引导信息，MCP Server 继续运行

### 3.6 Config 变更

```python
@dataclass
class XshellConfig:
    # ... 现有字段不变 ...
    ipc_base: str = ""    # 新增：注册和 sessions 的根目录

    def __post_init__(self):
        # ipc_dir 保留，用于向后兼容（XSH_IPC_DIR 环境变量指定的旧方式）
        if not self.ipc_base:
            pkg_dir = Path(__file__).resolve().parent.parent.parent
            self.ipc_base = str(pkg_dir / "ipc")
```

### 3.7 Protocol 层不变

`Request` / `Response` 数据类不需要增加 `session_id` 字段。路由在 MCP Server 层完成。

### 3.8 Output Processor 不变

输出处理与 session 无关，不需要改动。

---

## 4. 用户操作流程

### 4.1 XShell 端操作

用户在 XShell 中手动操作：

```
1. 打开 XShell
2. 打开/新建 SSH 连接 → 出现 Tab1
3. 菜单 → 工具 → 脚本 → 运行 → 选择 xshell_bridge_v7.py
   → bridge 自动注册为 session_92292
4. 重复步骤 2-3 打开更多 tab，每个 tab 运行一次 bridge 脚本
```

### 4.2 Claude Code 端操作

```
# 1. 打开 Claude Code 窗口 → MCP Server 自动启动

# 2. 查看可用的 XShell 会话
> list_sessions()
  [
    {session_id: "session_92292", remote: "192.168.1.1:22", status: "空闲"},
    {session_id: "session_78431", remote: "192.168.1.1:22", status: "空闲"},
    {session_id: "session_45016", remote: "10.0.0.1:22", status: "空闲"}
  ]

# 3. 选择并绑定一个会话（之后所有命令自动路由到此 session）
> connect_session("session_92292")
  {success: true, session_id: "session_92292", remote: "192.168.1.1:22"}

# 4. 正常使用，无需再传 session_id
> execute_command("ls -la")
  {output: "...", ...}

# 5. 查看当前绑定信息（确认哪个 XShell 页签在用）
> get_bridge_info()
  {session_id: "session_92292", bridge_pid: 92292, remote: "192.168.1.1:22"}

# 6. 断开绑定（可选，关闭窗口也会自动断开）
> disconnect_session()
  {success: true}
```

### 4.3 断联场景

```
# XShell tab 关闭 → bridge 退出 → 注册文件被清理
> execute_command("ls")
  {error: "XShell 会话已断开（tab 可能已关闭），请调用 list_sessions() 重新选择"}

# 重新选择
> list_sessions()
  [
    {session_id: "session_78431", remote: "192.168.1.1:22", status: "空闲"},
    {session_id: "session_45016", remote: "10.0.0.1:22", status: "空闲"}
  ]
  ← session_92292 已消失

> connect_session("session_78431")
  {success: true, ...}
```

### 4.4 多窗口场景

```
Claude Code 窗口 A 绑定 session_92292
Claude Code 窗口 B 绑定 session_78431
→ 两个窗口各自独立操作，互不干扰

Claude Code 窗口 C 打开：
> list_sessions()
  [
    {session_id: "session_92292", status: "已占用"},
    {session_id: "session_78431", status: "已占用"},
    {session_id: "session_45016", status: "空闲"}
  ]
> connect_session("session_45016")    ← 只能绑定空闲的

Claude Code 窗口 A 关闭：
→ session_92292 的占用标记自动清除
→ 其他窗口可以绑定 session_92292
```

---

## 5. 占用标记机制

### 写入时机

| 时机 | 操作 |
|------|------|
| `connect_session()` | 写入 `bound_by=mcp_pid`, `bound_at=now` |
| `disconnect_session()` | 写入 `bound_by=0`, `bound_at=""` |
| MCP Server 进程退出 | `atexit` 注册清理函数，写入 `bound_by=0` |
| 幽灵占用检测 | 检测到 `bound_by` PID 不存在，自动写入 `bound_by=0` |

### CAS（Compare-And-Swap）并发安全

多个 MCP Server 进程可能同时尝试绑定同一个 session。为避免竞争条件，`connect_session()` 使用 CAS 模式：

1. **Read**：读取注册文件，检查 `bound_by == 0`
2. **Write**：写入 `bound_by = mcp_pid, bound_at = now`
3. **Wait**：等待 50ms（让可能的并发写入完成）
4. **Verify**：再次读取注册文件，确认 `bound_by == mcp_pid`
5. **Rollback**：如果验证失败（被抢占），不做清理（另一个进程已写入），返回错误

这种方式利用文件系统的原子 rename 操作（`.tmp` + `os.replace()`），在绝大多数情况下能正确处理并发绑定。极端情况下（两个进程在 50ms 内先后写入），后写入者会覆盖先写入者的标记，但 verify 步骤会让先写入者检测到失败并报错。

### 清理策略

| 场景 | 清理方式 |
|------|----------|
| 正常断开（`disconnect_session()`） | 即时清除 |
| Claude Code 窗口关闭 | `atexit` 回调清除 |
| MCP Server 进程崩溃 | 下次 `list_sessions()` 或 `connect_session()` 时检测到 PID 不存在，自动清除 |
| XShell tab 关闭 | Bridge 退出时删除注册文件（含占用标记一并删除） |

### 进程存活检测

```python
import psutil

@staticmethod
def _is_process_alive(pid: int) -> bool:
    """检查指定 PID 的进程是否还在运行"""
    try:
        return psutil.pid_exists(pid) and psutil.Process(pid).is_running()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
```

> 注意：如果不想引入 `psutil` 依赖，可以用 Windows 原生方式：
> ```python
> import ctypes
> def _is_process_alive(pid: int) -> bool:
>     kernel32 = ctypes.windll.kernel32
>     PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
>     handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
>     if handle:
>         kernel32.CloseHandle(handle)
>         return True
>     return False
> ```

---

## 6. Bridge 存活检测

### 检测机制

| 信号 | 含义 |
|------|------|
| 注册文件不存在 | Bridge 已退出（正常清理） |
| `last_heartbeat` 超过 5 分钟 | Bridge 可能已崩溃（未正常清理注册文件） |
| `check_bridge()` 返回 false | Bridge 不响应 IPC 请求 |

### 处理策略

1. `list_sessions()` 时过滤掉不存活的 session
2. `connect_session()` 前验证 bridge 存活
3. 工具调用时如果 bridge 不响应 → 清除绑定，返回错误信息
4. 可选：`list_sessions()` 时清理过期注册文件（`last_heartbeat` 超 5 分钟且 `check_bridge()` 失败）

---

## 7. 并发安全

### 不同 session 天然隔离

每个 session 有独立的 IPC 目录（`sessions/session_<pid>/`），不同 Claude Code 窗口操作不同 session 时，文件 IPC 完全隔离，不会互相干扰。

### 同一 session 内的并发

理论上不会发生（因为占用标记阻止了同一 session 被多个 MCP Server 绑定），但作为防御性编程，在 `BridgeClient._send_request()` 中加进程内锁：

```python
class BridgeClient:
    def __init__(self, ...):
        self._lock = threading.Lock()

    def _send_request(self, req, timeout, request_id=""):
        with self._lock:
            # 原有逻辑不变
```

---

## 8. 向后兼容

| 场景 | 处理 |
|------|------|
| 用户只有 1 个 XShell tab，不关心多会话 | 正常流程：`list_sessions()` → `connect_session()`，多了一步绑定而已 |
| 旧版 bridge（无注册机制） | MCP Server 检测到 registry 目录为空时，回退到旧逻辑：直接扫描 `ipc_dir` 下的 `.request.json`，尝试 `check_bridge()`，自动绑定 |
| `XSH_IPC_DIR` 环境变量 | 仍然生效，直接使用指定目录，不走注册机制（兼容旧部署） |
| 现有测试 | 需要新增 session_manager 测试；现有 bridge_client 和 protocol 测试保持不变 |

---

## 9. 改动清单

| 文件 | 改动量 | 说明 |
|------|--------|------|
| `bridge/xshell_bridge_v6.11.py` → `xshell_bridge_v7.py` | **大改** | session_id 用 PID、IPC_DIR 动态化、注册机制、心跳更新、退出清理 |
| `src/xshell_mcp/server.py` | **大改** | `_client` 单例 → 绑定模式、新增 connect/disconnect/list/get_bridge_info 工具、断联处理 |
| `src/xshell_mcp/session_manager.py` | **新增** | Session 发现、注册、绑定/解绑、占用标记、幽灵清理、存活检测 |
| `src/xshell_mcp/config.py` | **小改** | 新增 `ipc_base` 字段 |
| `src/xshell_mcp/bridge_client.py` | **小改** | 新增 `threading.Lock` 防并发 |
| `src/xshell_mcp/log_config.py` | **小改** | 新增 `session_id` contextvars 变量，日志格式增加 `[session_id]` 字段 |
| `src/xshell_mcp/xshell_launcher.py` | **中改** | 不再自动启动 XShell，改为引导提示 |
| `src/xshell_mcp/__main__.py` | **小改** | `init_bridge()` → `session_manager.discover()` + 等待绑定 |
| `src/xshell_mcp/protocol.py` | **不改** | |
| `src/xshell_mcp/output_processor.py` | **不改** | |
| `src/xshell_mcp/exceptions.py` | **小改** | 新增 `SessionNotFoundError`, `SessionOccupiedError` |
| 测试文件 | **新增** | `test_session_manager.py` |

---

## 10. 风险与待验证项

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| XShell 同一进程内运行多个脚本时，`xsh` 对象是否真的隔离 | 如果不隔离，多个 bridge 会操作同一个 tab | 需要在 XShell 中实际测试 |
| `xsh.Session.RemoteAddress` 等属性能否正确返回 | 影响用户辨识不同 session | 需要实际测试；如果不可用则注册文件中仅保留 PID |
| 注册文件残留（bridge 崩溃未清理） | MCP Server 认为已死的 bridge 还活着 | 心跳超时检测 + `check_bridge()` 二次验证 |
| 幽灵占用（MCP Server 崩溃未清理占用标记） | session 看似被占用但实际空闲 | PID 存活检测 + 自动释放 |
| XShell 脚本运行方式：是全局运行还是绑定到特定 tab | 决定方案可行性 | 需要验证 |

### 建议验证步骤

1. 打开 XShell → 打开 2 个 SSH 连接 tab
2. 在 tab 1 中运行 bridge 脚本（菜单 → 工具 → 脚本 → 运行）
3. 观察 bridge 日志中 `xsh.Session.RemoteAddress` 是否是 tab 1 的地址
4. 通过 IPC 发送 `get_screen` 请求，确认输出是 tab 1 的内容
5. 在 tab 2 中再运行 bridge 脚本（使用不同 IPC 目录）
6. 确认两个 bridge 各自操作自己的 tab
