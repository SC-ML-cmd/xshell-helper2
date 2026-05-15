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
    """在 Xshell 当前终端中执行命令并返回输出。

    命令将在 Xshell 当前活跃的会话/终端中执行。请确保已在 Xshell 中
    手动完成登录和跳转（如需要），再使用此工具。

    Args:
        command: 要执行的 shell 命令
        timeout: 超时时间（秒），默认 30
    """
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
    """向 Xshell 终端发送原始文本，自动追加回车。

    用于交互式场景：输入密码、回答 yes/no 提示、菜单选择等。

    Args:
        text: 要发送的文本（自动追加回车）
        wait_for: 等待终端出现的字符串（如 "$"、"#"、"password:"）
        timeout: 超时时间（秒），默认 30
    """
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
    """读取 Xshell 终端最后 N 行内容。

    Args:
        lines: 读取的行数，默认 50
    """
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
