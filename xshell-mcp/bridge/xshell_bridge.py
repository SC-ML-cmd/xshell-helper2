"""
Xshell Bridge 脚本 — 在 Xshell 内部运行

通过文件 IPC 与外部 MCP Server 通信：
- 轮询 .request.json（mtime 变化检测）
- 执行命令 → 捕获输出
- 写入 .response.json

使用 Xshell 脚本 API：xsh.Session / xsh.Screen / xsh.Dialog
"""

import json
import os
import sys
import time

# ============================================================
# 配置区域（MCP Server 启动时会覆盖 IPC_DIR）
# ============================================================
IPC_DIR = os.path.join(os.environ.get("TEMP", os.path.join(os.path.expanduser("~"), "AppData", "Local", "Temp")),
                       "xshell_mcp")
REQ_FILE = os.path.join(IPC_DIR, ".request.json")
RESP_FILE = os.path.join(IPC_DIR, ".response.json")
SCREEN_COLS = 200

# 允许通过命令行参数或环境变量覆盖
if len(sys.argv) > 1:
    IPC_DIR = sys.argv[1]
    REQ_FILE = os.path.join(IPC_DIR, ".request.json")
    RESP_FILE = os.path.join(IPC_DIR, ".response.json")


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
        return {"success": False, "error": "Unknown request type: " + t}


def _handle_exec(req):
    start_row = _current_row()
    cmd = req.get("cmd", "")
    marker = req.get("marker", "")
    timeout_ms = req.get("timeout_ms", 30000)

    # 拼接完整命令，追加 marker echo
    full_cmd = cmd + " ; echo " + marker
    xsh.Screen.Send(full_cmd + "\r")

    # 等待 marker 出现（用 WaitForStrings，支持超时）
    timed_out = False
    try:
        xsh.Screen.WaitForStrings([marker], timeout_ms)
    except Exception:
        timed_out = True

    end_row = _current_row()
    total_rows = _total_rows()
    output = _read_screen(start_row, end_row)

    return {
        "success": not timed_out,
        "output": output,
        "timed_out": timed_out,
        "start_row": start_row,
        "end_row": end_row,
        "screen_rows": total_rows,
        "screen_cols": SCREEN_COLS,
    }


def _handle_send_raw(req):
    start_row = _current_row()
    text = req.get("cmd", "")  # send_raw 也用 cmd 字段
    wait_for = req.get("wait_for", "")
    timeout_ms = req.get("timeout_ms", 30000)

    xsh.Screen.Send(text)

    timed_out = False
    if wait_for:
        try:
            xsh.Screen.WaitForStrings([wait_for], timeout_ms)
        except Exception:
            timed_out = True

    end_row = _current_row()
    total_rows = _total_rows()
    output = _read_screen(start_row, end_row)

    return {
        "success": not timed_out,
        "output": output,
        "timed_out": timed_out,
        "start_row": start_row,
        "end_row": end_row,
        "screen_rows": total_rows,
        "screen_cols": SCREEN_COLS,
    }


def _handle_get_screen(req):
    lines = req.get("lines", 50)
    total_rows = _total_rows()
    start_row = max(0, total_rows - lines)
    end_row = total_rows
    output = _read_screen(start_row, end_row)

    return {
        "success": True,
        "output": output,
        "timed_out": False,
        "start_row": start_row,
        "end_row": end_row,
        "screen_rows": total_rows,
        "screen_cols": SCREEN_COLS,
    }


def _handle_interrupt():
    xsh.Screen.Send("\x03")
    return {"success": True, "output": "", "timed_out": False,
            "start_row": 0, "end_row": 0, "screen_rows": _total_rows(), "screen_cols": SCREEN_COLS}


def _handle_check():
    return {"success": True, "output": "bridge online",
            "timed_out": False, "start_row": 0, "end_row": 0,
            "screen_rows": _total_rows(), "screen_cols": SCREEN_COLS,
            "current_row": _current_row(), "connected": _is_connected()}


# ============================================================
# 终端操作辅助
# ============================================================

def _current_row():
    try:
        return xsh.Screen.CurrentRow
    except Exception:
        return 0


def _total_rows():
    try:
        return xsh.Screen.Rows
    except Exception:
        return 0


def _is_connected():
    try:
        return xsh.Session.Connected
    except Exception:
        return False


def _read_screen(start_row, end_row):
    if start_row >= end_row:
        return ""
    try:
        return xsh.Screen.Get(start_row, 1, end_row, SCREEN_COLS)
    except Exception as e:
        return "[Bridge read error: {}]".format(str(e))


# ============================================================
# 主循环 — 文件轮询 IPC
# ============================================================

def Main():
    _setup()

    # 确保 IPC 目录存在
    if not os.path.isdir(IPC_DIR):
        os.makedirs(IPC_DIR, exist_ok=True)

    # 写入初始就绪信号
    _write_resp({"success": True, "output": "bridge started",
                 "timed_out": False, "start_row": 0, "end_row": 0,
                 "screen_rows": _total_rows(), "screen_cols": SCREEN_COLS,
                 "current_row": _current_row()})

    last_mtime = 0
    while True:
        try:
            mtime = os.path.getmtime(REQ_FILE)
            if mtime > last_mtime:
                last_mtime = mtime
                req = _read_req()
                if req:
                    resp = process_request(req)
                    _write_resp(resp)
        except OSError:
            pass

        xsh.Session.Sleep(200)


def _setup():
    try:
        xsh.Screen.Synchronous = True
    except Exception:
        pass


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
