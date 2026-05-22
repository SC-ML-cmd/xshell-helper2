"""Xshell MCP Server — 让大模型通过 Xshell 执行命令"""

import atexit
import os
import time
import threading
import logging

from mcp.server.fastmcp import FastMCP

from .config import load_config, load_log_config, LogConfig
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
from .log_analyzer import (
    build_search_command,
    build_extract_command,
    build_filter_command,
    build_context_command,
    generate_cache_filename,
    parse_extract_result,
)

logger = get_logger("xshell_mcp")

# ============================================================
# Server 初始化
# ============================================================

mcp = FastMCP("xshell-mcp")

_config = load_config()
_log_config: LogConfig | None = load_log_config()
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
def get_terminal_size() -> dict:
    """获取当前 Xshell 终端的行数和列数（screen_rows / screen_cols）。"""
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
# 日志分析工具
# ============================================================

@mcp.tool()
def search_logs(
    keyword: str,
    mode: str = "search",
    file_pattern: str = "",
    time_range: str = "",
    max_lines: int = 50,
    context_lines: int = 0,
    offset: int = 0,
    before: int = 20,
    after: int = 50,
    file_path: str = "",
    occurrence: int = 1,
    cache_file: str = "",
    timeout: int = 60,
) -> dict:
    """在远程 POD 日志中搜索关键字（支持 .gz 压缩文件）。

    四种模式：
    - mode="search":  直接搜索原始日志文件（适合无明确关键字时探索）
    - mode="extract": 原子化提取所有匹配行到临时缓存文件（解决日志轮转竞态）
    - mode="filter":  在缓存文件上二次过滤（如从 traceId 结果中筛 ERROR）
    - mode="context": 获取指定匹配的上下文（查看完整堆栈）

    双路径工作流：
    - 有明确关键字(traceId等): extract → filter → context
    - 无明确关键字(探索性): search(时间+级别) → 发现线索后 extract

    日志目录和文件格式从项目配置 .xshell-log.json 自动读取。

    Args:
        keyword: 搜索关键字（traceId、异常类名、ERROR、日志内容等）
        mode: 工作模式 - "search" / "extract" / "filter" / "context"
        file_pattern: 文件过滤（如 ossres-dws.log.4*.gz），为空则搜索配置中所有日志
        time_range: 时间范围过滤（如 "14:30-14:35"），配合 timestamp_format 使用
        max_lines: [search/filter] 最大返回行数（默认50）
        context_lines: [search] 每个匹配的上下文行数
        offset: [search/filter] 跳过前 N 行（分页用）
        before: [context] 匹配前显示行数（默认20）
        after: [context] 匹配后显示行数（默认50，堆栈通常在后面）
        file_path: [context] 指定文件路径
        occurrence: [context] 第几次匹配（默认第1次）
        cache_file: [filter/context] 指定缓存文件路径（extract返回的路径）
        timeout: 超时秒数（压缩文件搜索建议60-120）
    """
    rid = generate_request_id()
    set_request_id(rid)

    client = get_client()
    marker = "{}{}".format(_config.marker_prefix, int(time.time() * 1000000))

    # 获取日志配置
    log_dir = ""
    compressed_ext = [".gz"]
    max_extract = 10000

    if _log_config:
        log_dir = _log_config.log_dir
        compressed_ext = _log_config.compressed_extensions
        max_extract = _log_config.max_extract_lines
        if not file_pattern:
            file_pattern = _log_config.file_pattern

    # 无配置且无 file_pattern 时报错
    if not log_dir and mode in ("search", "extract"):
        return {"error": "未找到日志配置(.xshell-log.json)且未指定 file_pattern，无法确定搜索范围"}

    logger.info("search_logs mode=%s keyword=%.40s file_pattern=%s", mode, keyword, file_pattern)

    try:
        t0 = time.time()
        if mode == "search":
            cmd = build_search_command(
                keyword=keyword,
                log_dir=log_dir,
                file_pattern=file_pattern,
                compressed_extensions=compressed_ext,
                time_range=time_range,
                max_lines=max_lines,
                offset=offset,
                context_lines=context_lines,
            )
            resp = client.execute(cmd, marker, timeout=timeout, request_id=rid)
            output = clean_command_output(resp.output, cmd, marker)

            lines = output.strip().split("\n") if output.strip() else []
            elapsed = time.time() - t0
            logger.info("search_logs 完成 mode=search elapsed=%.2fs lines=%d", elapsed, len(lines))
            return {
                "output": output,
                "lines_returned": len(lines),
                "has_more": len(lines) >= max_lines,
                "timed_out": resp.timed_out,
                "command": cmd,
            }

        elif mode == "extract":
            cache_path = generate_cache_filename()
            cmd = build_extract_command(
                keyword=keyword,
                log_dir=log_dir,
                file_pattern=file_pattern,
                max_extract_lines=max_extract,
                cache_path=cache_path,
            )
            resp = client.execute(cmd, marker, timeout=timeout, request_id=rid)
            output = clean_command_output(resp.output, cmd, marker)

            result = parse_extract_result(output, cache_path)
            result["timed_out"] = resp.timed_out
            result["command"] = cmd
            elapsed = time.time() - t0
            logger.info("search_logs 完成 mode=extract elapsed=%.2fs total_lines=%d cache=%s",
                        elapsed, result.get("total_lines", 0), cache_path)
            return result

        elif mode == "filter":
            if not cache_file:
                return {"error": "filter 模式需要指定 cache_file（由 extract 模式返回）"}
            cmd = build_filter_command(
                keyword=keyword,
                cache_file=cache_file,
                max_lines=max_lines,
                offset=offset,
            )
            resp = client.execute(cmd, marker, timeout=timeout, request_id=rid)
            output = clean_command_output(resp.output, cmd, marker)

            lines = output.strip().split("\n") if output.strip() else []
            elapsed = time.time() - t0
            logger.info("search_logs 完成 mode=filter elapsed=%.2fs lines=%d", elapsed, len(lines))
            return {
                "output": output,
                "lines_returned": len(lines),
                "has_more": len(lines) >= max_lines,
                "timed_out": resp.timed_out,
                "command": cmd,
            }

        elif mode == "context":
            target_file = file_path or cache_file
            if not target_file:
                return {"error": "context 模式需要指定 file_path 或 cache_file"}
            cmd = build_context_command(
                file_path=target_file,
                keyword=keyword,
                before=before,
                after=after,
                occurrence=occurrence,
                compressed_extensions=compressed_ext,
            )
            resp = client.execute(cmd, marker, timeout=timeout, request_id=rid)
            output = clean_command_output(resp.output, cmd, marker)

            elapsed = time.time() - t0
            logger.info("search_logs 完成 mode=context elapsed=%.2fs file=%s", elapsed, target_file)
            return {
                "output": output,
                "file": target_file,
                "timed_out": resp.timed_out,
                "command": cmd,
            }
        else:
            return {"error": f"未知模式: {mode}，支持的模式: search/extract/filter/context"}

    except BridgeTimeoutError:
        logger.warning("search_logs 超时 mode=%s timeout=%ds", mode, timeout)
        return {
            "output": "",
            "timed_out": True,
            "error": f"日志搜索超时 ({timeout}s)，压缩文件较多时请增加 timeout 参数",
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
        if _bound_session_id == "legacy":
            return {
                "sessions": [{
                    "session_id": "legacy",
                    "remote_address": "",
                    "remote_port": 0,
                    "session_name": "legacy (XSH_IPC_DIR)",
                    "tab_text": "",
                    "user_name": "",
                    "status": "已绑定 (legacy 单会话模式)",
                }],
                "count": 1,
                "mode": "legacy"
            }
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

    - session_id 为空时：自动选择 started_at 最早的会话（多个时逐个尝试，处理并发抢占）
    - session_id 非空时：绑定指定的会话

    Args:
        session_id: 要绑定的会话 ID（如 "session_92292"），为空则自动选择
    """
    global _bound_session_id, _bound_client

    rid = generate_request_id()
    set_request_id(rid)

    if _session_manager is None:
        if _bound_session_id == "legacy":
            return {
                "success": True,
                "session_id": "legacy",
                "message": "Legacy 单会话模式已自动绑定，无需手动连接",
                "mode": "legacy"
            }
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

        # 按 started_at 升序排列（最早启动的优先），缺失字段排最后
        available.sort(key=lambda s: s.get("started_at", "9999"))

        logger.info("自动选择: %d 个空闲会话，按 started_at 排序", len(available))
        for s in available:
            logger.info("  - %s started_at=%s", s.get("session_id", ""), s.get("started_at", "?"))

        # 逐个尝试绑定，处理并发抢占
        for s in available:
            sid = s["session_id"]
            try:
                client = _session_manager.bind(sid, mcp_pid)
                _bound_session_id = sid
                _bound_client = client
                set_session_id(sid)

                info = _session_manager.get_session_info(sid) or {}
                logger.info("自动绑定成功 session=%s remote=%s", sid,
                           info.get("remote_address", ""))
                return {
                    "success": True,
                    "session_id": sid,
                    "remote_address": info.get("remote_address", ""),
                    "remote_port": info.get("remote_port", 0),
                    "session_name": info.get("session_name", ""),
                    "tab_text": info.get("tab_text", ""),
                    "user_name": info.get("user_name", ""),
                }
            except SessionOccupiedError:
                logger.warning("会话 %s 已被抢占，尝试下一个...", sid)
                continue
            except SessionNotFoundError:
                logger.warning("会话 %s 已消失，尝试下一个...", sid)
                continue

        return {
            "success": False,
            "error": "所有空闲会话绑定失败（可能被抢占），请重试"
        }

    # session_id 非空：绑定指定会话
    try:
        client = _session_manager.bind(session_id, mcp_pid)
        _bound_session_id = session_id
        _bound_client = client
        set_session_id(session_id)

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
    若检测到 Bridge 已断连则自动解绑。
    """
    global _bound_session_id, _bound_client

    rid = generate_request_id()
    set_request_id(rid)

    if not _bound_session_id:
        return {"bound": False, "message": "当前没有绑定的会话"}

    info = {}
    if _session_manager:
        info = _session_manager.get_session_info(_bound_session_id) or {}
    elif _bound_session_id == "legacy":
        info = {
            "session_id": "legacy",
            "remote_address": "",
            "session_name": "legacy (XSH_IPC_DIR)",
            "ipc_dir": _config.ipc_dir,
            "mode": "legacy",
        }

    # 检测到 Bridge 已死则自动清理僵尸绑定
    if not info:
        old_session = _bound_session_id
        logger.warning("绑定会话 %s 已断连（注册文件不存在），自动解绑", old_session)
        _bound_session_id = None
        _bound_client = None
        set_session_id("")
        return {"bound": False, "message": f"会话 {old_session} 已断连，已自动解绑"}

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
    global _session_manager, _bound_session_id, _bound_client

    logger.debug("XSH_IPC_DIR=%r XSH_IPC_BASE=%r ipc_base=%r ipc_dir=%r",
                 os.getenv("XSH_IPC_DIR"), os.getenv("XSH_IPC_BASE"),
                 _config.ipc_base, _config.ipc_dir)

    # Legacy 模式：XSH_IPC_DIR 已设置且 XSH_IPC_BASE 未设置
    # 退回旧的单会话模式，直接连接到指定 IPC 目录
    if os.getenv("XSH_IPC_DIR") and not os.getenv("XSH_IPC_BASE"):
        logger.info("检测到 legacy 模式 (XSH_IPC_DIR=%s)，使用单会话直连", _config.ipc_dir)
        client = BridgeClient(_config.ipc_dir, timeout=_config.default_timeout)
        client.initialize()
        _bound_client = client
        _bound_session_id = "legacy"
        set_session_id("legacy")
        _session_manager = None  # 不使用 SessionManager

        # 尝试检查 bridge 是否在线
        try:
            if client.check_bridge():
                logger.info("Legacy 模式: Bridge 已在线")
            else:
                logger.warning("Legacy 模式: Bridge 未在线，命令执行需要 Bridge 先启动")
        except Exception:
            logger.warning("Legacy 模式: Bridge 检测失败，命令执行需要 Bridge 先启动")
        return

    # 正常多会话路径
    _session_manager = SessionManager(_config.ipc_base, timeout=_config.default_timeout)

    # 清理僵尸注册文件 + 已退出 session 目录
    _session_manager.check_stale_bindings()
    _session_manager.cleanup_stale_session_dirs()

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
        logger.info("未发现已注册的 bridge，将在后台轮询等待...")


def _auto_bind_loop():
    """后台线程：轮询等待空闲会话并自动绑定"""
    global _bound_session_id, _bound_client

    deadline = time.time() + _config.auto_bind_timeout
    mcp_pid = os.getpid()

    while time.time() < deadline:
        _session_manager.check_stale_bindings()
        available = _session_manager.list_available()

        if not available:
            logger.info("自动绑定: 未发现空闲会话，%ds 后重试... (剩余 %ds)",
                       _config.auto_bind_poll_interval,
                       int(deadline - time.time()))
            time.sleep(_config.auto_bind_poll_interval)
            continue

        # 按 started_at 升序排列（最早启动的优先），缺失字段排最后
        available.sort(key=lambda s: s.get("started_at", "9999"))

        logger.info("自动绑定: 发现 %d 个空闲会话，按启动时间排序后尝试绑定", len(available))
        for s in available:
            logger.info("  - %s started_at=%s remote=%s",
                       s.get("session_id", ""),
                       s.get("started_at", "?"),
                       s.get("remote_address", ""))

        # 逐个尝试 CAS 绑定，被抢占则 fallback 到下一个
        for s in available:
            sid = s["session_id"]
            try:
                client = _session_manager.bind(sid, mcp_pid)
                _bound_session_id = sid
                _bound_client = client
                set_session_id(sid)
                logger.info("自动绑定成功: %s (started_at=%s)",
                           sid, s.get("started_at", ""))
                return
            except SessionOccupiedError:
                logger.warning("自动绑定: 会话 %s 已被抢占，尝试下一个...", sid)
                continue
            except SessionNotFoundError:
                logger.warning("自动绑定: 会话 %s 已消失，尝试下一个...", sid)
                continue

        # 所有会话绑定失败，等待后下一轮重新扫描
        logger.info("自动绑定: 所有空闲会话绑定失败（可能被抢占），%ds 后重试...",
                   _config.auto_bind_poll_interval)
        time.sleep(_config.auto_bind_poll_interval)

    logger.warning("自动绑定超时（%ds），MCP Server 将以未绑定状态运行，"
                  "请稍后调用 list_sessions() 和 connect_session() 手动绑定",
                  _config.auto_bind_timeout)


def start_auto_bind():
    """启动后台自动绑定线程（非阻塞）"""
    if _session_manager is None:
        return  # legacy 模式无需后台绑定

    t = threading.Thread(target=_auto_bind_loop, daemon=True, name="auto-bind")
    t.start()
    logger.info("后台自动绑定线程已启动（timeout=%ds interval=%ds）",
               _config.auto_bind_timeout, _config.auto_bind_poll_interval)


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
