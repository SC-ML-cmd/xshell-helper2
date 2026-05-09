"""Xshell MCP Server — 让大模型通过 Xshell 执行命令"""

import time
import logging

from mcp.server.fastmcp import FastMCP

from .config import load_config
from .bridge_client import BridgeClient
from .xshell_launcher import launch_xshell, wait_for_bridge
from .output_processor import clean_command_output, truncate_output
from .exceptions import BridgeNotReadyError, BridgeTimeoutError, BridgeConnectionError

logger = logging.getLogger("xshell_mcp")

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


# ============================================================
# 生命周期
# ============================================================

@mcp.tool()
def check_bridge() -> dict:
    """检查 Bridge 是否在线"""
    try:
        client = get_client()
        ok = client.check_bridge()
        return {"bridge_online": ok}
    except Exception as e:
        return {"bridge_online": False, "error": str(e)}


@mcp.tool()
def execute_command(command: str, timeout: int = 30) -> dict:
    """在 Xshell 当前终端中执行命令并返回输出。

    命令将在 Xshell 当前活跃的会话/终端中执行。请确保已在 Xshell 中
    手动完成登录和跳转（如需要），再使用此工具。

    Args:
        command: 要执行的 shell 命令
        timeout: 超时时间（秒），默认 30
    """
    client = get_client()
    marker = "{}{}".format(_config.marker_prefix, int(time.time() * 1000000))

    try:
        resp = client.execute(command.strip(), marker, timeout=timeout)
        output = clean_command_output(resp.output, command.strip(), marker)
        output, truncated = truncate_output(output)

        return {
            "output": output,
            "timed_out": resp.timed_out,
            "truncated": truncated,
            "command": command,
        }
    except BridgeTimeoutError:
        return {
            "output": "",
            "timed_out": True,
            "truncated": False,
            "error": "命令执行超时 ({}s)".format(timeout),
            "command": command,
        }


@mcp.tool()
def send_raw(text: str, wait_for: str = "$", timeout: int = 30) -> dict:
    """向 Xshell 终端发送原始文本，不自动追加回车。

    用于交互式场景：输入密码、回答 yes/no 提示等。

    Args:
        text: 要发送的文本
        wait_for: 等待终端出现的字符串（如 "$"、"#"、"password:"）
        timeout: 超时时间（秒），默认 30
    """
    client = get_client()

    try:
        resp = client.send_raw(text, wait_for, timeout=timeout)
        output, truncated = truncate_output(resp.output)

        return {
            "output": output,
            "timed_out": resp.timed_out,
            "truncated": truncated,
        }
    except BridgeTimeoutError:
        return {
            "output": "",
            "timed_out": True,
            "truncated": False,
            "error": "等待超时 ({}s)，等待字符串: {}".format(timeout, wait_for),
        }


@mcp.tool()
def interrupt() -> dict:
    """向终端发送 Ctrl+C，中断正在运行的命令"""
    client = get_client()

    resp = client.interrupt()
    return {"success": resp.success}


@mcp.tool()
def get_screen(lines: int = 50) -> dict:
    """读取 Xshell 终端最后 N 行内容。

    Args:
        lines: 读取的行数，默认 50
    """
    client = get_client()

    resp = client.get_screen(lines=lines)
    output, truncated = truncate_output(resp.output)

    return {
        "content": output,
        "truncated": truncated,
        "screen_rows": resp.screen_rows,
        "screen_cols": resp.screen_cols,
    }


@mcp.tool()
def get_session_info() -> dict:
    """获取当前 Xshell 终端状态信息"""
    client = get_client()

    resp = client.get_screen(lines=1)
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

    # 检查 Bridge 是否已在运行
    if client.check_bridge():
        logger.info("Bridge 已在线")
        _client = client
        return client

    # 启动 Xshell + Bridge
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
