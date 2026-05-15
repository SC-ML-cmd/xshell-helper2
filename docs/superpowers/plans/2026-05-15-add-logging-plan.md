# 文件日志系统 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为 Xshell MCP Server 添加文件日志系统，记录所有工具调用和 IPC 通信，支持链路追踪、日志轮转和敏感信息脱敏。

**架构：** 新建 `log_config.py` 封装日志配置（RotatingFileHandler + contextvars 注入 request_id）。在 `server.py` 和 `bridge_client.py` 的关键路径手动埋点。通过 `request_id` 串联 server → bridge 的调用链路。

**技术栈：** Python stdlib `logging` / `logging.handlers` / `contextvars`

---

### 任务 1：扩展 config.py — 添加日志配置字段

**文件：**
- 修改：`xshell-mcp/src/xshell_mcp/config.py`

- [ ] **步骤 1：在 XshellConfig 中添加日志字段**

```python
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class XshellConfig:
    xshell_path: str = r"D:\software\xshell8\Xshell.exe"
    bridge_script_path: str = ""
    ipc_dir: str = ""
    default_timeout: int = 30
    screen_cols: int = 200
    marker_prefix: str = "__XSH_"
    log_dir: str = ""
    log_level: str = "INFO"
    log_mask_sensitive: bool = False

    def __post_init__(self):
        if not self.bridge_script_path:
            pkg_dir = Path(__file__).resolve().parent.parent.parent
            self.bridge_script_path = str(pkg_dir / "bridge" / "xshell_bridge_v6.11.py")
        if not self.ipc_dir:
            import tempfile
            self.ipc_dir = str(Path(tempfile.gettempdir()) / "xshell_mcp")
        if not self.log_dir:
            pkg_dir = Path(__file__).resolve().parent.parent.parent
            self.log_dir = str(pkg_dir / "logs")
```

- [ ] **步骤 2：在 load_config() 中读取新环境变量**

```python
def load_config() -> XshellConfig:
    import os

    cfg = XshellConfig()
    if v := os.getenv("XSH_XSHELL_PATH"):
        cfg.xshell_path = v
    if v := os.getenv("XSH_BRIDGE_SCRIPT"):
        cfg.bridge_script_path = v
    if v := os.getenv("XSH_IPC_DIR"):
        cfg.ipc_dir = v
    if v := os.getenv("XSH_DEFAULT_TIMEOUT"):
        cfg.default_timeout = int(v)
    if v := os.getenv("XSH_SCREEN_COLS"):
        cfg.screen_cols = int(v)
    if v := os.getenv("XSH_LOG_DIR"):
        cfg.log_dir = v
    if v := os.getenv("XSH_LOG_LEVEL"):
        cfg.log_level = v
    if v := os.getenv("XSH_LOG_MASK_SENSITIVE"):
        cfg.log_mask_sensitive = v.lower() in ("1", "true", "yes")
    return cfg
```

- [ ] **步骤 3：运行现有测试确认兼容**

```bash
pytest xshell-mcp/tests/ -v
```
预期：全部通过

- [ ] **步骤 4：Commit**

---

### 任务 2：创建 log_config.py — 日志核心模块

**文件：**
- 创建：`xshell-mcp/src/xshell_mcp/log_config.py`

- [ ] **步骤 1：编写失败的测试**

创建 `xshell-mcp/tests/test_log_config.py`：

```python
"""log_config 模块单元测试"""
import logging
import tempfile
from pathlib import Path

from xshell_mcp.log_config import (
    setup_logging,
    get_logger,
    generate_request_id,
    set_request_id,
)


class TestGenerateRequestId:
    def test_format(self):
        rid = generate_request_id()
        parts = rid.split("-")
        assert len(parts) == 2
        assert 5 <= len(parts[0]) <= 5
        assert len(parts[1]) == 5
        assert parts[0].isdigit()
        assert parts[1].isdigit()

    def test_uniqueness(self):
        ids = {generate_request_id() for _ in range(100)}
        assert len(ids) == 100


class TestSetupLogging:
    def test_creates_log_file(self):
        with tempfile.TemporaryDirectory() as d:
            setup_logging(d, "INFO")
            logger = get_logger()
            logger.info("test message")

            log_files = list(Path(d).glob("xshell_mcp.log*"))
            assert len(log_files) >= 1
            content = Path(d / "xshell_mcp.log").read_text()
            assert "test message" in content

    def test_format_includes_request_id(self):
        with tempfile.TemporaryDirectory() as d:
            setup_logging(d, "INFO")
            set_request_id("12345-00042")
            logger = get_logger()
            logger.info("hello")

            content = Path(d / "xshell_mcp.log").read_text()
            assert "[12345-00042]" in content

    def test_empty_request_id_when_not_set(self):
        with tempfile.TemporaryDirectory() as d:
            setup_logging(d, "INFO")
            logger = get_logger()
            logger.info("no rid")

            content = Path(d / "xshell_mcp.log").read_text()
            assert "[]" in content

    def test_rotating_handler_configured(self):
        with tempfile.TemporaryDirectory() as d:
            setup_logging(d, "INFO")
            logger = get_logger()
            root = logging.getLogger("xshell_mcp")
            assert len(root.handlers) == 1
            handler = root.handlers[0]
            assert handler.maxBytes == 500 * 1024
            assert handler.backupCount == 5
```

- [ ] **步骤 2：运行测试确认失败**

```bash
pytest xshell-mcp/tests/test_log_config.py -v
```
预期：ImportError，`log_config` 模块不存在

- [ ] **步骤 3：实现 log_config.py**

```python
"""Xshell MCP 文件日志配置 — RotatingFileHandler + request_id 链路追踪"""
import contextvars
import logging
import logging.handlers
import threading
import time
from pathlib import Path

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)
_counter = 0
_lock = threading.Lock()


def generate_request_id() -> str:
    """生成 8 位短 ID，格式 ttttt-nnnnn（时间戳后 5 位 + 5 位序号）"""
    global _counter
    with _lock:
        _counter = (_counter + 1) % 100000
        return "{:05d}-{:05d}".format(int(time.time()) % 100000, _counter)


def set_request_id(rid: str) -> None:
    """设置当前上下文的 request_id"""
    _request_id_var.set(rid)


class _RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = _request_id_var.get()
        return True


def setup_logging(log_dir: str, level: str = "INFO") -> None:
    """配置文件日志：RotatingFileHandler，500KB 轮转，保留 5 个文件"""
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        str(path / "xshell_mcp.log"),
        maxBytes=500 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s.%(msecs)03d %(levelname)-5s [%(request_id)s] "
            "%(filename)s:%(lineno)d %(funcName)s() | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root = logging.getLogger("xshell_mcp")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()
    root.addHandler(handler)
    root.addFilter(_RequestIdFilter())
    root.propagate = False


def get_logger(name: str = "xshell_mcp") -> logging.Logger:
    """获取 xshell_mcp 命名空间下的 logger"""
    return logging.getLogger(name)
```

- [ ] **步骤 4：运行测试确认通过**

```bash
pytest xshell-mcp/tests/test_log_config.py -v
```
预期：全部 PASS

- [ ] **步骤 5：Commit**

---

### 任务 3：改造 __main__.py — 启动时初始化文件日志

**文件：**
- 修改：`xshell-mcp/src/xshell_mcp/__main__.py`

- [ ] **步骤 1：替换 basicConfig 为 setup_logging**

```python
"""Xshell MCP Server 入口"""

import logging
import sys


def main():
    from .config import load_config

    cfg = load_config()

    from .log_config import setup_logging

    setup_logging(cfg.log_dir, cfg.log_level)

    logger = logging.getLogger("xshell_mcp")
    logger.info("Xshell MCP Server 启动中...")

    from .server import init_bridge, mcp

    try:
        init_bridge()
    except Exception as e:
        logger.warning("Bridge 初始化失败: %s", e)
        logger.warning("将继续启动 MCP Server，但命令执行需要 Bridge 在线")
        logger.warning("请手动在 Xshell 中运行 bridge/xshell_bridge.py 脚本")

    logger.info("MCP Server 就绪")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

- [ ] **步骤 2：运行现有测试确认兼容**

```bash
pytest xshell-mcp/tests/ -v
```
预期：全部通过（日志文件在项目 `logs/` 目录下生成是预期行为）

- [ ] **步骤 3：Commit**

---

### 任务 4：bridge_client.py — 添加 request_id 参数链 + IPC 日志

**文件：**
- 修改：`xshell-mcp/src/xshell_mcp/bridge_client.py`

- [ ] **步骤 1：添加 logger 和 request_id 参数（所有公开方法 + _send_request）**

修改 `bridge_client.py`，完整内容：

```python
"""Bridge 客户端 — MCP Server 通过文件 IPC 与 Xshell Bridge 通信"""

import json
import os
import time
from pathlib import Path

from .exceptions import BridgeNotReadyError, BridgeTimeoutError, BridgeConnectionError
from .protocol import Request, Response
from .log_config import get_logger, set_request_id

logger = get_logger("xshell_mcp.bridge")


class BridgeClient:
    def __init__(self, ipc_dir: str, timeout: int = 30):
        self._ipc_dir = Path(ipc_dir)
        self._req_file = self._ipc_dir / ".request.json"
        self._resp_file = self._ipc_dir / ".response.json"
        self._default_timeout = timeout

    def initialize(self):
        self._ipc_dir.mkdir(parents=True, exist_ok=True)

    def check_bridge(self, request_id: str = "") -> bool:
        set_request_id(request_id)
        req = Request(type="check")
        logger.info("发送 check")
        try:
            resp = self._send_request(req, timeout=10, request_id=request_id)
            logger.info("check 结果 online=%s", resp.success)
            return resp.success
        except Exception:
            logger.info("check 结果 online=False")
            return False

    def execute(self, cmd: str, marker: str, timeout: int = 0, request_id: str = "") -> Response:
        if timeout <= 0:
            timeout = self._default_timeout
        set_request_id(request_id)
        req = Request(
            type="exec",
            cmd=cmd,
            marker=marker,
            timeout_ms=timeout * 1000,
        )
        return self._send_request(req, timeout, request_id)

    def send_raw(self, text: str, wait_for: str, timeout: int = 0, request_id: str = "") -> Response:
        if timeout <= 0:
            timeout = self._default_timeout
        set_request_id(request_id)
        req = Request(
            type="send_raw",
            cmd=text,
            wait_for=wait_for,
            timeout_ms=timeout * 1000,
        )
        return self._send_request(req, timeout, request_id)

    def get_screen(self, lines: int = 50, timeout: int = 5, request_id: str = "") -> Response:
        set_request_id(request_id)
        req = Request(type="get_screen", lines=lines)
        logger.info("读取屏幕 lines=%d", lines)
        resp = self._send_request(req, timeout=timeout, request_id=request_id)
        logger.info("屏幕读取完成 output_len=%d rows=%d cols=%d",
                     len(resp.output), resp.screen_rows, resp.screen_cols)
        return resp

    def interrupt(self, timeout: int = 3, request_id: str = "") -> Response:
        set_request_id(request_id)
        req = Request(type="interrupt")
        logger.info("发送中断")
        return self._send_request(req, timeout=timeout, request_id=request_id)

    def _send_request(self, req: Request, timeout: int, request_id: str = "") -> Response:
        if timeout <= 0:
            timeout = self._default_timeout
        set_request_id(request_id)

        self._ipc_dir.mkdir(parents=True, exist_ok=True)
        _remove_if_exists(self._resp_file)

        logger.info("写入请求 type=%s cmd=%.80s", req.type, req.cmd[:80])

        _write_json(self._req_file, req)

        t0 = time.time()
        deadline = time.time() + timeout + 2
        while time.time() < deadline:
            resp_data = _read_json(self._resp_file)
            if resp_data is not None:
                elapsed = time.time() - t0
                resp = Response(**{k: v for k, v in resp_data.items() if k in Response._FIELDS})
                logger.info("收到响应 success=%s output_len=%d elapsed=%.2fs",
                            resp.success, len(resp.output), elapsed)
                return resp
            time.sleep(0.1)

        elapsed = time.time() - t0
        logger.warning("请求超时 type=%s timeout=%ds elapsed=%.2fs", req.type, timeout, elapsed)
        raise BridgeTimeoutError(
            "Bridge 命令执行超时 ({}s): {}".format(timeout, req.cmd[:80])
        )


def _write_json(path, obj):
    data = obj if isinstance(obj, dict) else obj.to_json() if hasattr(obj, 'to_json') else json.dumps(obj)
    if isinstance(data, str):
        data = data.encode("utf-8")
    elif not isinstance(data, bytes):
        data = json.dumps(data).encode("utf-8")

    tmp = str(path) + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, str(path))


def _read_json(path):
    try:
        with open(str(path), "r", encoding="utf-8") as f:
            return json.loads(f.read())
    except (json.JSONDecodeError, OSError):
        return None


def _remove_if_exists(path):
    try:
        os.remove(str(path))
    except OSError:
        pass
```

- [ ] **步骤 2：运行现有测试确认兼容（新参数有默认值，不破坏已有调用）**

```bash
pytest xshell-mcp/tests/ -v
```
预期：全部通过

- [ ] **步骤 3：Commit**

---

### 任务 5：server.py — 为 6 个工具函数添加日志

**文件：**
- 修改：`xshell-mcp/src/xshell_mcp/server.py`

- [ ] **步骤 1：添加日志调用**

修改 `server.py`，完整内容：

```python
"""Xshell MCP Server — 让大模型通过 Xshell 执行命令"""

import time
import logging

from mcp.server.fastmcp import FastMCP

from .config import load_config
from .bridge_client import BridgeClient
from .xshell_launcher import launch_xshell, wait_for_bridge
from .output_processor import clean_command_output, truncate_output
from .exceptions import BridgeNotReadyError, BridgeTimeoutError, BridgeConnectionError
from .log_config import get_logger, generate_request_id, set_request_id

logger = get_logger("xshell_mcp")

# ============================================================
# Server 初始化
# ============================================================

mcp = FastMCP("xshell-mcp")

_config = load_config()
_client: BridgeClient | None = None


def get_client() -> BridgeClient:
    global _client
    if _client is None:
        raise BridgeNotReadyError("Bridge 未初始化，请先启动 MCP Server")
    return _client


def _mask_if_needed(text: str) -> str:
    """根据配置决定是否脱敏"""
    return "***" if _config.log_mask_sensitive else text


# ============================================================
# 生命周期
# ============================================================

@mcp.tool()
def check_bridge() -> dict:
    """检查 Bridge 是否在线"""
    rid = generate_request_id()
    set_request_id(rid)

    try:
        client = get_client()
        ok = client.check_bridge(request_id=rid)
        logger.info("bridge_online=%s", ok)
        return {"bridge_online": ok}
    except Exception as e:
        logger.info("bridge_online=False error=%s", e)
        return {"bridge_online": False, "error": str(e)}


@mcp.tool()
def execute_command(command: str, timeout: int = 30) -> dict:
    """在 Xshell 当前终端中执行命令并返回输出。"""
    rid = generate_request_id()
    set_request_id(rid)

    client = get_client()
    marker = "{}{}".format(_config.marker_prefix, int(time.time() * 1000000))

    cmd = command.strip()
    logger.info("cmd=%.80s timeout=%d", cmd, timeout)

    try:
        t0 = time.time()
        resp = client.execute(cmd, marker, timeout=timeout, request_id=rid)
        elapsed = time.time() - t0
        output = clean_command_output(resp.output, cmd, marker)
        output, truncated = truncate_output(output)

        logger.info("完成 elapsed=%.2fs output_len=%d timed_out=%s",
                     elapsed, len(output), resp.timed_out)

        return {
            "output": output,
            "timed_out": resp.timed_out,
            "truncated": truncated,
            "command": command,
        }
    except BridgeTimeoutError:
        logger.warning("超时 timeout=%ds", timeout)
        return {
            "output": "",
            "timed_out": True,
            "truncated": False,
            "error": "命令执行超时 ({}s)".format(timeout),
            "command": command,
        }


@mcp.tool()
def send_raw(text: str, wait_for: str = "$", timeout: int = 30) -> dict:
    """向 Xshell 终端发送原始文本，自动追加回车。"""
    rid = generate_request_id()
    set_request_id(rid)

    client = get_client()

    logger.info("text=%s wait_for=%s timeout=%d",
                _mask_if_needed(text), wait_for, timeout)

    try:
        t0 = time.time()
        resp = client.send_raw(text, wait_for, timeout=timeout, request_id=rid)
        elapsed = time.time() - t0
        output, truncated = truncate_output(resp.output)

        logger.info("完成 elapsed=%.2fs output_len=%d", elapsed, len(output))

        return {
            "output": output,
            "timed_out": resp.timed_out,
            "truncated": truncated,
        }
    except BridgeTimeoutError:
        logger.warning("超时 timeout=%ds wait_for=%s", timeout, wait_for)
        return {
            "output": "",
            "timed_out": True,
            "truncated": False,
            "error": "等待超时 ({}s)，等待字符串: {}".format(timeout, wait_for),
        }


@mcp.tool()
def interrupt() -> dict:
    """向终端发送 Ctrl+C，中断正在运行的命令"""
    rid = generate_request_id()
    set_request_id(rid)

    client = get_client()
    resp = client.interrupt(request_id=rid)
    logger.info("success=%s", resp.success)
    return {"success": resp.success}


@mcp.tool()
def get_screen(lines: int = 50) -> dict:
    """读取 Xshell 终端最后 N 行内容。"""
    rid = generate_request_id()
    set_request_id(rid)

    client = get_client()

    logger.info("lines=%d", lines)

    resp = client.get_screen(lines=lines, request_id=rid)
    output, truncated = truncate_output(resp.output)

    logger.info("完成 output_len=%d truncated=%s screen_rows=%d screen_cols=%d",
                len(output), truncated, resp.screen_rows, resp.screen_cols)

    return {
        "content": output,
        "truncated": truncated,
        "screen_rows": resp.screen_rows,
        "screen_cols": resp.screen_cols,
    }


@mcp.tool()
def get_session_info() -> dict:
    """获取当前 Xshell 终端状态信息"""
    rid = generate_request_id()
    set_request_id(rid)

    client = get_client()

    resp = client.get_screen(lines=1)
    logger.info("screen_rows=%d screen_cols=%d",
                resp.screen_rows, resp.screen_cols)

    return {
        "screen_rows": resp.screen_rows,
        "screen_cols": resp.screen_cols,
    }


# ============================================================
# 启动逻辑
# ============================================================

def init_bridge() -> BridgeClient:
    global _client

    client = BridgeClient(_config.ipc_dir, timeout=_config.default_timeout)
    client.initialize()

    if client.check_bridge():
        logger.info("Bridge 已在线")
        _client = client
        return client

    logger.info("启动 Xshell 并加载 Bridge 脚本...")
    launch_xshell(_config)

    logger.info("等待 Bridge 就绪...")
    if not wait_for_bridge(client):
        raise BridgeNotReadyError(
            "Bridge 启动超时。请确认:\n"
            "1. Xshell 已安装且路径正确\n"
            "2. Xshell 的脚本功能可用\n"
            "3. 手动打开 Xshell → 工具 → 脚本 → 运行 → 选择 bridge/xshell_bridge.py"
        )

    logger.info("Bridge 就绪")
    _client = client
    return client
```

- [ ] **步骤 2：运行全部测试**

```bash
pytest xshell-mcp/tests/ -v
```
预期：全部通过

- [ ] **步骤 3：Commit**

---

### 任务 6：端到端验证

**文件：**
- 验证：`xshell-mcp/tests/test_log_config.py`（已存在）

- [ ] **步骤 1：运行完整测试套件**

```bash
pytest xshell-mcp/tests/ -v
```
预期：全部 PASS

- [ ] **步骤 2：验证日志文件生成**

```bash
python -c "from xshell_mcp.log_config import setup_logging, get_logger, generate_request_id, set_request_id; import tempfile; import os; d = tempfile.mkdtemp(); setup_logging(d); rid = generate_request_id(); set_request_id(rid); logger = get_logger(); logger.info('test'); logger.warning('warn'); print('LOG DIR:', d); [print(f, open(f).read()) for f in __import__('pathlib').Path(d).glob('*.log')]"
```
预期：输出日志内容，格式正确，包含 request_id

- [ ] **步骤 3：验证日志轮转配置**

```bash
python -c "from xshell_mcp.log_config import setup_logging; import logging; import tempfile; d = __import__('tempfile').mkdtemp(); setup_logging(d); root = logging.getLogger('xshell_mcp'); h = root.handlers[0]; print(f'maxBytes={h.maxBytes}, backupCount={h.backupCount}')"
```
预期：`maxBytes=512000, backupCount=5`

- [ ] **步骤 4：Commit**

---
