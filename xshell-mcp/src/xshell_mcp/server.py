"""Xshell MCP Server — 让大模型通过 Xshell 执行命令"""

import atexit
import os
import time
import logging

from mcp.server.fastmcp import FastMCP

from .config import load_config
from .bridge_client import BridgeClient
from .session_manager import SessionManager
from .output_processor import clean_command_output, truncate_output
from .exceptions import (
    BridgeNotReadyError,
    BridgeTimeoutError,
    BridgeConnectionError,
    SessionNotBoundError,
    SessionNotFoundError,
    SessionOccupiedError,
)
from .log_config import get_logger, generate_request_id, set_request_id, set_session_id

logger = get_logger("xshell_mcp")

# ============================================================
# Server 初始化
# ============================================================

mcp = FastMCP("xshell-mcp")

_config = load_config()
_bound_session_id: str | None = None
_bound_client: BridgeClient | None = None
_session_manager: SessionManager | None = None


def get_client() -> BridgeClient:
    global _bound_client
    if _bound_client is None:
        raise SessionNotBoundError(
            "尚未绑定 XShell 会话，请先调用 list_sessions() 查看可用会话，"
            "再调用 connect_session(session_id) 绑定"
        )
    return _bound_client


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
    global _bound_session_id, _bound_client

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
        # 二次检查 bridge 是否还活着
        if _bound_session_id and _session_manager:
            if not _session_manager._is_bridge_alive(_bound_session_id):
                _bound_session_id = None
                _bound_client = None
                set_session_id("")
                return {
                    "output": "",
                    "error": "XShell 会话已断开（tab 可能已关闭），请调用 list_sessions() 重新选择",
                    "timed_out": True,
                    "session_lost": True,
                }
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
    global _bound_session_id, _bound_client

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
        # 二次检查 bridge 是否还活着
        if _bound_session_id and _session_manager:
            if not _session_manager._is_bridge_alive(_bound_session_id):
                _bound_session_id = None
                _bound_client = None
                set_session_id("")
                return {
                    "output": "",
                    "error": "XShell 会话已断开（tab 可能已关闭），请调用 list_sessions() 重新选择",
                    "timed_out": True,
                    "session_lost": True,
                }
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
# 会话管理工具
# ============================================================

@mcp.tool()
def list_sessions() -> dict:
    """列出所有 XShell 会话（含 PID、远程地址、占用状态）。

    返回所有已注册且存活的 XShell Bridge 会话列表，每个会话包含：
    - session_id: 会话标识
    - remote_address: 远程主机地址
    - session_name: 会话名称
    - status: 占用状态（空闲/已占用）
    """
    rid = generate_request_id()
    set_request_id(rid)

    if _session_manager is None:
        return {"error": "Session Manager 未初始化", "sessions": []}

    _session_manager.check_stale_bindings()
    sessions = _session_manager.discover()

    # 简化输出
    result = []
    for s in sessions:
        result.append({
            "session_id": s.get("session_id", ""),
            "remote_address": s.get("remote_address", ""),
            "remote_port": s.get("remote_port", 0),
            "session_name": s.get("session_name", ""),
            "tab_text": s.get("tab_text", ""),
            "user_name": s.get("user_name", ""),
            "status": s.get("status", "未知"),
        })

    logger.info("list_sessions: 发现 %d 个会话", len(result))
    return {"sessions": result, "count": len(result)}


@mcp.tool()
def connect_session(session_id: str = "") -> dict:
    """绑定一个 XShell 会话（CAS 并发安全）。

    绑定后，该窗口的所有命令（execute_command、send_raw 等）自动路由到此 session。

    - session_id 为空时：如果只有 1 个空闲会话则自动绑定，多个则列出供选择
    - session_id 非空时：绑定指定的会话

    Args:
        session_id: 要绑定的会话 ID（如 "session_92292"），为空则自动选择
    """
    global _bound_session_id, _bound_client

    rid = generate_request_id()
    set_request_id(rid)

    if _session_manager is None:
        return {"success": False, "error": "Session Manager 未初始化"}

    # 如果已绑定，先断开
    if _bound_session_id:
        logger.info("已有绑定 session=%s，先断开", _bound_session_id)
        _session_manager.unbind(_bound_session_id)
        _bound_session_id = None
        _bound_client = None
        set_session_id("")

    mcp_pid = os.getpid()

    # 自动选择逻辑
    if not session_id:
        _session_manager.check_stale_bindings()
        available = _session_manager.list_available()
        if len(available) == 0:
            return {
                "success": False,
                "error": "没有可用的空闲会话，请在 XShell 中运行 bridge 脚本"
            }
        elif len(available) == 1:
            session_id = available[0]["session_id"]
            logger.info("自动选择唯一空闲会话: %s", session_id)
        else:
            # 多个空闲会话，返回列表让用户选择
            sessions = []
            for s in available:
                sessions.append({
                    "session_id": s.get("session_id", ""),
                    "remote_address": s.get("remote_address", ""),
                    "session_name": s.get("session_name", ""),
                    "tab_text": s.get("tab_text", ""),
                })
            return {
                "success": False,
                "error": "有多个空闲会话，请指定 session_id",
                "available_sessions": sessions
            }

    try:
        client = _session_manager.bind(session_id, mcp_pid)
        _bound_session_id = session_id
        _bound_client = client
        set_session_id(session_id)

        # 注册 atexit 清理
        info = _session_manager.get_session_info(session_id) or {}

        logger.info("绑定成功 session=%s remote=%s", session_id,
                    info.get("remote_address", ""))
        return {
            "success": True,
            "session_id": session_id,
            "remote_address": info.get("remote_address", ""),
            "remote_port": info.get("remote_port", 0),
            "session_name": info.get("session_name", ""),
            "tab_text": info.get("tab_text", ""),
            "user_name": info.get("user_name", ""),
        }
    except (SessionNotFoundError, SessionOccupiedError) as e:
        logger.warning("绑定失败: %s", e)
        return {"success": False, "error": str(e)}


@mcp.tool()
def disconnect_session() -> dict:
    """断开当前绑定的 XShell 会话，释放占用标记。

    断开后需要重新调用 connect_session() 绑定才能执行命令。
    """
    global _bound_session_id, _bound_client

    rid = generate_request_id()
    set_request_id(rid)

    if not _bound_session_id:
        return {"success": True, "message": "当前没有绑定的会话"}

    old_session = _bound_session_id
    if _session_manager:
        _session_manager.unbind(_bound_session_id)

    _bound_session_id = None
    _bound_client = None
    set_session_id("")

    logger.info("已断开 session=%s", old_session)
    return {"success": True, "session_id": old_session}


@mcp.tool()
def get_bridge_info() -> dict:
    """返回当前绑定的 XShell 会话信息。

    包括 session_id、bridge PID、远程地址等，用于确认当前 Claude Code 窗口对应哪个 XShell 页签。
    """
    rid = generate_request_id()
    set_request_id(rid)

    if not _bound_session_id:
        return {"bound": False, "message": "当前没有绑定的会话"}

    info = {}
    if _session_manager:
        info = _session_manager.get_session_info(_bound_session_id) or {}

    return {
        "bound": True,
        "session_id": _bound_session_id,
        "bridge_pid": info.get("pid", 0),
        "remote_address": info.get("remote_address", ""),
        "remote_port": info.get("remote_port", 0),
        "session_name": info.get("session_name", ""),
        "tab_text": info.get("tab_text", ""),
        "user_name": info.get("user_name", ""),
        "connected": info.get("connected", False),
    }


# ============================================================
# 启动逻辑
# ============================================================

def _init_session_manager():
    """初始化 Session Manager，发现已注册的 bridge"""
    global _session_manager

    _session_manager = SessionManager(_config.ipc_base, timeout=_config.default_timeout)

    sessions = _session_manager.discover()
    if sessions:
        logger.info("发现 %d 个已注册的 bridge", len(sessions))
        for s in sessions:
            logger.info("  - %s (%s:%s) [%s]",
                       s.get("session_id", ""),
                       s.get("remote_address", ""),
                       s.get("remote_port", ""),
                       s.get("status", ""))
    else:
        logger.info("未发现已注册的 bridge，请在 XShell 中运行 xshell_bridge_v7.py 脚本")


# ============================================================
# atexit 清理
# ============================================================

def _cleanup_on_exit():
    """MCP Server 退出时自动清除占用标记"""
    global _bound_session_id, _bound_client
    if _bound_session_id and _session_manager:
        try:
            _session_manager.unbind(_bound_session_id)
            logger.info("atexit: 已清除占用标记 session=%s", _bound_session_id)
        except Exception:
            pass
    _bound_session_id = None
    _bound_client = None

atexit.register(_cleanup_on_exit)
