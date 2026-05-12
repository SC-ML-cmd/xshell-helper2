"""
Xshell Bridge v6.6
- 统一异常处理：容错 3 次才退出
- 智能 Sleep：优先 xsh.Session.Sleep，异常退化为 time.sleep
- 自适应间隔：活跃 200ms → 低活跃 5s → 空闲 30s
- Session/Connected 缓存：10 分钟才真正调 COM
- 配置集中管理，方便调试
- 日志带 tid，区分不同窗口/页签
- **修复**：_handle_exec / _handle_send_raw start_row 使用 _current_row()
  （光标位置），end_row 使用 _total_rows()（屏幕总行数）
- **修复**：marker 中插入 shell 空引号 `''` 打断命令回显，
  使 WaitForString 只能在实际输出中匹配 marker，彻底解决输出丢失
"""

import json
import os
import sys
import threading
import time
import traceback

# ============================================================
# 配置
# ============================================================

class BridgeConfig:
    SCREEN_COLS = 200

    # Sleep 策略
    COM_SLEEP_MS = 500           # 活跃期
    IDLE_SLEEP_MS = 5000         # 低活跃期（3分钟无 IPC）
    DEEP_IDLE_SLEEP_MS = 30000   # 空闲期（10分钟无 exec）
    IDLE_THRESHOLD_SEC = 180
    DEEP_IDLE_THRESHOLD_SEC = 600

    # Session 缓存
    CACHE_TTL_SEC = 600          # 10 分钟

    # 容错
    MAX_ERRORS = 3

    # 日志
    HEARTBEAT_INTERVAL = 100


CFG = BridgeConfig()

# ============================================================
# IPC 路径
# ============================================================

IPC_DIR = os.path.join(os.environ.get("TEMP", os.path.join(os.path.expanduser("~"), "AppData", "Local", "Temp")),
                       "xshell_mcp")
REQ_FILE = os.path.join(IPC_DIR, ".request.json")
RESP_FILE = os.path.join(IPC_DIR, ".response.json")
LOG_FILE = os.path.join(IPC_DIR, "bridge.log")

if len(sys.argv) > 1:
    IPC_DIR = sys.argv[1]
    REQ_FILE = os.path.join(IPC_DIR, ".request.json")
    RESP_FILE = os.path.join(IPC_DIR, ".response.json")
    LOG_FILE = os.path.join(IPC_DIR, "bridge.log")


# ============================================================
# 日志
# ============================================================

def _log(msg):
    try:
        ts = time.strftime("%H:%M:%S", time.localtime()) + ".%03d" % (int(time.time() * 1000) % 1000)
        tid = threading.current_thread().ident
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("[%s] [tid=%s] %s\n" % (ts, tid, msg))
            f.flush()
    except Exception:
        pass


# ============================================================
# 安全访问工具
# ============================================================

def _safe_init():
    try:
        if 'xsh' not in globals():
            return False
        if xsh is None:
            return False
        if not hasattr(xsh, 'Session') or xsh.Session is None:
            return False
        if not hasattr(xsh, 'Screen'):
            return False
        return True
    except Exception:
        return False


def _safe_call(func, default=None):
    try:
        return func()
    except (AttributeError, TypeError, NameError, Exception):
        return default


def _is_session_valid():
    return _safe_call(lambda: xsh.Session is not None, False)


def _is_connected():
    return _safe_call(lambda: xsh.Session.Connected, False)


# ============================================================
# 智能 Sleep
# ============================================================

def _smart_sleep(ms):
    """优先 xsh.Session.Sleep，异常退化 time.sleep，耗时异常抛 RuntimeError"""
    t0 = time.time()
    try:
        xsh.Session.Sleep(ms)
    except Exception:
        _log("COM Sleep exception, fallback time.sleep(%dms)" % ms)
        time.sleep(ms / 1000.0)
        return

    elapsed = (time.time() - t0) * 1000
    if elapsed < ms * 0.5:
        raise RuntimeError(
            "COM Sleep interrupted: expected %dms, actual %.0fms" % (ms, elapsed))


# ============================================================
# Shell 检测
# ============================================================

def _detect_separator():
    end = _total_rows()
    start = max(0, end - 5)
    recent = _read_screen(start, end)
    last_line = recent.split("\n")[-1] if recent else ""
    if ">" in last_line[-3:] and ":\\" not in last_line[-60:]:
        if "PS " in last_line:
            return ";"
        return "&"
    return ";"


# ============================================================
# 终端辅助
# ============================================================

def _current_row():
    return _safe_call(lambda: xsh.Screen.CurrentRow, 0)


def _total_rows():
    return _safe_call(lambda: xsh.Screen.Rows, 0)


def _read_screen(start_row, end_row):
    if start_row >= end_row:
        return ""
    return _safe_call(lambda: xsh.Screen.Get(start_row, 1, end_row, CFG.SCREEN_COLS), "")


# ============================================================
# Session 缓存
# ============================================================

_session_cache = {"valid": True, "connected": True, "ts": 0.0}


def _check_session():
    """带缓存的 session 检查，10 分钟才真正调 COM"""
    now = time.time()
    if now - _session_cache["ts"] < CFG.CACHE_TTL_SEC:
        if not _session_cache["connected"]:
            raise RuntimeError("Session disconnected (cached)")
        return

    valid = _is_session_valid()
    _smart_sleep(200)
    connected = _is_connected()

    _session_cache["valid"] = valid
    _session_cache["connected"] = connected
    _session_cache["ts"] = now

    if not connected:
        _log("SESSION check: connected=false, will raise")
    if not valid or not connected:
        raise RuntimeError("Session lost")


# ============================================================
# 自适应间隔
# ============================================================

_last_request_time = time.time()
_last_command_time = time.time()


def _get_sleep_ms():
    now = time.time()
    if now - _last_command_time > CFG.DEEP_IDLE_THRESHOLD_SEC:
        return CFG.DEEP_IDLE_SLEEP_MS
    elif now - _last_request_time > CFG.IDLE_THRESHOLD_SEC:
        return CFG.IDLE_SLEEP_MS
    return CFG.COM_SLEEP_MS


# ============================================================
# 请求处理
# ============================================================

def process_request(req):
    global _last_request_time, _last_command_time
    _last_request_time = time.time()
    t = req.get("type", "")
    if t == "exec" or t == "send_raw":
        _last_command_time = time.time()

    if t == "exec":
        return _handle_exec(req)
    elif t == "send_raw":
        return _handle_send_raw(req)
    elif t == "get_screen":
        return _handle_get_screen(req)
    elif t == "interrupt":
        return _handle_interrupt()
    elif t == "check":
        return _handle_check()
    else:
        return {"success": False, "error": "Unknown type: " + t, "output": ""}


def _handle_exec(req):
    cmd = req.get("cmd", "")
    marker = req.get("marker", "")
    timeout_ms = req.get("timeout_ms", 30000)
    start_row = _current_row()

    _log("EXEC start: %s [marker=%s]" % (cmd[:120], marker))

    sep = _detect_separator()
    half = len(marker) // 2
    escaped_marker = marker[:half] + "''" + marker[half:]
    full_cmd = cmd + " " + sep + " echo " + escaped_marker
    xsh.Screen.Send(full_cmd + "\r")
    xsh.Screen.WaitForString(marker)

    end_row = _total_rows()
    output = _read_screen(start_row, end_row)
    _log("EXEC done: %s [start=%d end=%d output_len=%d]" % (cmd[:120], start_row, end_row, len(output)))

    return {
        "success": True, "output": output, "timed_out": False,
        "start_row": start_row, "end_row": end_row,
        "screen_rows": _total_rows(), "screen_cols": CFG.SCREEN_COLS,
    }


def _handle_send_raw(req):
    text = req.get("cmd", "")
    wait_for = req.get("wait_for", "")
    start_row = _current_row()

    _log("SEND_RAW: %s [wait=%s]" % (text[:120], wait_for))
    xsh.Screen.Send(text)

    timed_out = True
    if wait_for:
        xsh.Screen.WaitForString(wait_for)
        timed_out = False
    else:
        timed_out = False

    end_row = _total_rows()
    output = _read_screen(start_row, end_row)

    return {
        "success": not timed_out, "output": output, "timed_out": timed_out,
        "start_row": start_row, "end_row": end_row,
        "screen_rows": _total_rows(), "screen_cols": CFG.SCREEN_COLS,
    }


def _handle_get_screen(req):
    lines = req.get("lines", 50)
    total_rows = _total_rows()
    start_row = max(0, total_rows - lines)
    output = _read_screen(start_row, total_rows)

    return {
        "success": True, "output": output, "timed_out": False,
        "start_row": start_row, "end_row": total_rows,
        "screen_rows": total_rows, "screen_cols": CFG.SCREEN_COLS,
    }


def _handle_interrupt():
    _log("INTERRUPT sending ^C")
    xsh.Screen.Send("\x03")
    return {"success": True, "output": "", "timed_out": False,
            "start_row": 0, "end_row": 0,
            "screen_rows": _total_rows(), "screen_cols": CFG.SCREEN_COLS}


def _handle_check():
    return {"success": True, "output": "bridge v6.6 online",
            "timed_out": False, "start_row": 0, "end_row": 0,
            "screen_rows": _total_rows(), "screen_cols": CFG.SCREEN_COLS,
            "current_row": _current_row(), "connected": _is_connected()}


# ============================================================
# 主循环 — 统一异常处理 + 容错
# ============================================================

def Main():
    global _last_request_time, _last_command_time

    if not _safe_init():
        _log("STARTUP FAILED: xsh not available")
        return

    try:
        xsh.Screen.Synchronous = True
    except Exception:
        pass

    if not os.path.isdir(IPC_DIR):
        os.makedirs(IPC_DIR, exist_ok=True)

    _log("v6.6 STARTED pid=%s ipc=%s cfg=%s" % (
        os.getpid(), IPC_DIR,
        {k: v for k, v in vars(CFG).items() if not k.startswith("_")}
    ))

    _write_resp({
        "success": True, "output": "bridge v6.6 started", "timed_out": False,
        "start_row": 0, "end_row": 0,
        "screen_rows": _total_rows(), "screen_cols": CFG.SCREEN_COLS,
        "current_row": _current_row()
    })

    last_mtime = os.path.getmtime(REQ_FILE) if os.path.isfile(REQ_FILE) else 0
    iteration = 0
    error_count = 0
    exit_reason = "unknown"

    while True:
        try:
            # 步骤1: 会话检查（缓存，10分钟1次COM）
            _check_session()

            # 步骤2: IPC 请求处理
            try:
                mtime = os.path.getmtime(REQ_FILE)
                if mtime > last_mtime:
                    last_mtime = mtime
                    req = _read_req()
                    if req:
                        req_type = req.get("type", "?")
                        _log("REQ type=%s" % req_type)
                        resp = process_request(req)
                        _write_resp(resp)
                        _log("RESP done type=%s success=%s" % (req_type, resp.get("success")))
            except OSError:
                pass

            # 步骤3: 自适应 Sleep
            sleep_ms = _get_sleep_ms()
            _smart_sleep(sleep_ms)

            # 成功完成一轮，重置错误计数
            error_count = 0

        except RuntimeError as e:
            # 预期的 session 丢失
            _log("SESSION LOST: %s" % e)
            exit_reason = "session_lost"
            break

        except Exception as e:
            error_count += 1
            _log("LOOP error #%d/%d: %s\n%s" % (error_count, CFG.MAX_ERRORS, e, traceback.format_exc()))
            if error_count >= CFG.MAX_ERRORS:
                _log("FATAL: too many errors, exiting")
                exit_reason = "too_many_errors"
                break
            _smart_sleep(1000)

        iteration += 1
        if iteration % CFG.HEARTBEAT_INTERVAL == 0:
            _log("MAIN heartbeat iter=%d errors=%d sleep=%dms connected=%s" % (
                iteration, error_count, _get_sleep_ms(), _session_cache["connected"]))

    _log("EXIT reason=%s iteration=%d errors=%d" % (exit_reason, iteration, error_count))

    _write_resp({
        "success": False, "error": "bridge exiting", "output": "bridge v6.6 stopped",
        "timed_out": False, "start_row": 0, "end_row": 0,
        "screen_rows": 0, "screen_cols": CFG.SCREEN_COLS
    })


# ============================================================
# 文件操作
# ============================================================

def _read_req():
    try:
        with open(REQ_FILE, "r") as f:
            return json.loads(f.read())
    except Exception:
        return None


def _write_resp(resp):
    try:
        with open(RESP_FILE, "w") as f:
            f.write(json.dumps(resp))
    except Exception:
        pass
