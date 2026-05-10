"""
Xshell Bridge v6.1
- exec/send_raw 用 xsh.Screen.WaitForString 等待（安全）
- 主循环混合 Sleep: time.sleep(15ms) + xsh.Session.Sleep(5ms)
  前者不持 COM 引用安全等待，后者泵消息防 UI 卡顿，崩溃窗口仅 5ms
- xsh.Session.Connected 轮询检测会话断开
- 启动跳过旧请求（last_mtime 初始化为当前文件时间戳）
- 日志带 tid，区分不同窗口/页签
"""

import json
import os
import sys
import threading
import time

# ============================================================
# 配置
# ============================================================
IPC_DIR = os.path.join(os.environ.get("TEMP", os.path.join(os.path.expanduser("~"), "AppData", "Local", "Temp")),
                       "xshell_mcp")
REQ_FILE = os.path.join(IPC_DIR, ".request.json")
RESP_FILE = os.path.join(IPC_DIR, ".response.json")
LOG_FILE = os.path.join(IPC_DIR, "bridge.log")
SCREEN_COLS = 200
POLL_INTERVAL_MS = 20
COM_SLEEP_MS = 5        # xsh.Session.Sleep 泵消息，崩溃窗口仅 5ms
PY_SLEEP_SEC = 0.015    # time.sleep 安全等待，不持有 COM 引用

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
    def _check():
        if xsh.Session is None:
            return False
        return True
    return _safe_call(_check, False)


def _is_connected():
    def _check():
        return xsh.Session.Connected
    return _safe_call(_check, False)


# ============================================================
# Shell 检测
# ============================================================

def _detect_separator():
    def _get():
        end = _current_row()
        start = max(0, end - 5)
        recent = _read_screen(start, end)
        last_line = recent.split("\n")[-1] if recent else ""

        if ">" in last_line[-3:] and ":\\" not in last_line[-60:]:
            if "PS " in last_line:
                return ";"
            return "&"
        return ";"
    return _safe_call(_get, ";")


# ============================================================
# 请求处理
# ============================================================

def process_request(req):
    t = req.get("type", "")
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
    if not _is_session_valid():
        _log("EXEC session invalid, abort")
        return {"success": False, "error": "Session closed", "output": "", "timed_out": False,
                "start_row": 0, "end_row": 0, "screen_rows": 0, "screen_cols": SCREEN_COLS}

    start_row = _current_row()
    cmd = req.get("cmd", "")
    marker = req.get("marker", "")
    timeout_ms = req.get("timeout_ms", 30000)

    _log("EXEC start: %s [marker=%s, start_row=%d, timeout=%d]" % (cmd[:120], marker, start_row, timeout_ms))

    sep = _detect_separator()
    full_cmd = cmd + " " + sep + " echo " + marker

    try:
        xsh.Screen.Send(full_cmd + "\r")
    except (AttributeError, TypeError, Exception) as e:
        _log("EXEC Screen.Send failed: %s" % e)
        return {"success": False, "error": "Session closed", "output": "", "timed_out": False,
                "start_row": start_row, "end_row": start_row,
                "screen_rows": _total_rows(), "screen_cols": SCREEN_COLS}

    _safe_call(lambda: xsh.Screen.WaitForString(marker))

    if not _is_session_valid():
        _log("EXEC session lost during WaitForString")
        return {"success": False, "error": "Session closed during execution", "output": "", "timed_out": True,
                "start_row": start_row, "end_row": _current_row(),
                "screen_rows": _total_rows(), "screen_cols": SCREEN_COLS}

    timed_out = False

    end_row = _current_row()
    output = _read_screen(start_row, end_row)

    _log("EXEC done: %s [output_len=%d]" % (cmd[:120], len(output)))

    return {
        "success": not timed_out,
        "output": output,
        "timed_out": timed_out,
        "start_row": start_row,
        "end_row": end_row,
        "screen_rows": _total_rows(),
        "screen_cols": SCREEN_COLS,
    }


def _handle_send_raw(req):
    if not _is_session_valid():
        _log("SEND_RAW session invalid")
        return {"success": False, "error": "Session closed", "output": "", "timed_out": False,
                "start_row": 0, "end_row": 0, "screen_rows": 0, "screen_cols": SCREEN_COLS}

    start_row = _current_row()
    text = req.get("cmd", "")
    wait_for = req.get("wait_for", "")
    timeout_ms = req.get("timeout_ms", 30000)

    _log("SEND_RAW: %s [wait=%s]" % (text[:120], wait_for))

    try:
        xsh.Screen.Send(text)
    except (AttributeError, TypeError, Exception) as e:
        _log("SEND_RAW Screen.Send failed: %s" % e)
        return {"success": False, "error": "Session closed", "output": "", "timed_out": False,
                "start_row": start_row, "end_row": start_row,
                "screen_rows": _total_rows(), "screen_cols": SCREEN_COLS}

    timed_out = True
    if wait_for:
        _safe_call(lambda: xsh.Screen.WaitForString(wait_for))
        if not _is_session_valid():
            _log("SEND_RAW session lost during WaitForString")
            return {"success": False, "error": "Session closed", "output": "", "timed_out": True,
                    "start_row": start_row, "end_row": _current_row(),
                    "screen_rows": _total_rows(), "screen_cols": SCREEN_COLS}
        timed_out = False
    else:
        timed_out = False

    end_row = _current_row()
    output = _read_screen(start_row, end_row)

    return {
        "success": not timed_out,
        "output": output,
        "timed_out": timed_out,
        "start_row": start_row,
        "end_row": end_row,
        "screen_rows": _total_rows(),
        "screen_cols": SCREEN_COLS,
    }


def _handle_get_screen(req):
    if not _is_session_valid():
        return {"success": False, "error": "Session closed", "output": "", "timed_out": False,
                "start_row": 0, "end_row": 0, "screen_rows": 0, "screen_cols": SCREEN_COLS}

    lines = req.get("lines", 50)
    total_rows = _total_rows()
    start_row = max(0, total_rows - lines)
    output = _read_screen(start_row, total_rows)

    return {
        "success": True,
        "output": output,
        "timed_out": False,
        "start_row": start_row,
        "end_row": total_rows,
        "screen_rows": total_rows,
        "screen_cols": SCREEN_COLS,
    }


def _handle_interrupt():
    _log("INTERRUPT sending ^C")
    if not _is_session_valid():
        _log("INTERRUPT session invalid")
        return {"success": False, "error": "Session closed", "output": "", "timed_out": False,
                "start_row": 0, "end_row": 0, "screen_rows": 0, "screen_cols": SCREEN_COLS}

    try:
        xsh.Screen.Send("\x03")
        return {"success": True, "output": "", "timed_out": False,
                "start_row": 0, "end_row": 0,
                "screen_rows": _total_rows(), "screen_cols": SCREEN_COLS}
    except (AttributeError, TypeError, Exception) as e:
        _log("INTERRUPT Screen.Send failed: %s" % e)
        return {"success": False, "error": "Session closed", "output": "", "timed_out": False,
                "start_row": 0, "end_row": 0,
                "screen_rows": _total_rows(), "screen_cols": SCREEN_COLS}


def _handle_check():
    if not _is_session_valid() or not _is_connected():
        return {"success": False, "error": "Session closed", "output": "bridge v6.1 offline",
                "timed_out": False, "start_row": 0, "end_row": 0,
                "screen_rows": 0, "screen_cols": SCREEN_COLS,
                "current_row": 0, "connected": False}

    return {"success": True, "output": "bridge v6.1 online",
            "timed_out": False, "start_row": 0, "end_row": 0,
            "screen_rows": _total_rows(), "screen_cols": SCREEN_COLS,
            "current_row": _current_row(), "connected": True}


# ============================================================
# 终端辅助
# ============================================================

def _current_row():
    def _get():
        return xsh.Screen.CurrentRow
    return _safe_call(_get, 0)


def _total_rows():
    def _get():
        return xsh.Screen.Rows
    return _safe_call(_get, 0)


def _read_screen(start_row, end_row):
    def _get():
        if start_row >= end_row:
            return ""
        return xsh.Screen.Get(start_row, 1, end_row, SCREEN_COLS)
    return _safe_call(_get, "")


# ============================================================
# 主循环
# ============================================================

def Main():
    if not _safe_init():
        _log("STARTUP FAILED: xsh not available")
        return

    try:
        xsh.Screen.Synchronous = True
    except (AttributeError, TypeError, Exception):
        pass

    if not os.path.isdir(IPC_DIR):
        os.makedirs(IPC_DIR, exist_ok=True)

    _log("v6.1 STARTED pid=%s ipc=%s" % (os.getpid(), IPC_DIR))

    _write_resp({
        "success": True, "output": "bridge v6.1 started", "timed_out": False,
        "start_row": 0, "end_row": 0, "screen_rows": _total_rows(),
        "screen_cols": SCREEN_COLS, "current_row": _current_row()
    })

    last_mtime = os.path.getmtime(REQ_FILE) if os.path.isfile(REQ_FILE) else 0
    iteration = 0
    exit_reason = "unknown"

    while True:
        if not _is_session_valid():
            _log("MAIN session invalid")
            exit_reason = "session_invalid"
            break

        if not _is_connected():
            _log("MAIN connected=false")
            exit_reason = "connected_false"
            break

        try:
            mtime = os.path.getmtime(REQ_FILE)
            if mtime > last_mtime:
                last_mtime = mtime
                req = _read_req()
                if req:
                    _log("REQ type=%s" % req.get("type", "?"))
                    resp = process_request(req)
                    _write_resp(resp)
                    _log("RESP done type=%s success=%s" % (req.get("type", "?"), resp.get("success")))
        except OSError:
            pass

        try:
            time.sleep(PY_SLEEP_SEC)                               # 安全等待，不持 COM 引用
            _log("pre-comsleep iter=%d" % iteration)
            _safe_call(lambda: xsh.Session.Sleep(COM_SLEEP_MS))    # 泵消息，失败静默跳过
            _log("post-comsleep iter=%d" % iteration)
        except (AttributeError, TypeError, NameError, Exception) as e:
            _log("MAIN Sleep error: %s" % e)
            exit_reason = "sleep_error"
            break

        iteration += 1
        if iteration % 5000 == 0:
            _log("MAIN heartbeat iteration=%d connected=%s" % (iteration, _is_connected()))

    _log("EXIT reason=%s iteration=%d" % (exit_reason, iteration))

    _write_resp({
        "success": False, "error": "bridge exiting", "output": "bridge v6.1 stopped",
        "timed_out": False, "start_row": 0, "end_row": 0,
        "screen_rows": 0, "screen_cols": SCREEN_COLS
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
