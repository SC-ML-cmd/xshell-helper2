"""
Xshell Bridge v3
- 自动检测 shell 类型（CMD/Bash/PowerShell），使用正确命令分隔符
- 轮询检测 marker，不依赖 WaitForStrings API
"""

import json
import os
import sys

# ============================================================
# 配置
# ============================================================
IPC_DIR = os.path.join(os.environ.get("TEMP", os.path.join(os.path.expanduser("~"), "AppData", "Local", "Temp")),
                       "xshell_mcp")
REQ_FILE = os.path.join(IPC_DIR, ".request.json")
RESP_FILE = os.path.join(IPC_DIR, ".response.json")
SCREEN_COLS = 200

if len(sys.argv) > 1:
    IPC_DIR = sys.argv[1]
    REQ_FILE = os.path.join(IPC_DIR, ".request.json")
    RESP_FILE = os.path.join(IPC_DIR, ".response.json")


# ============================================================
# Shell 检测
# ============================================================

def _detect_separator():
    """检测当前 shell 类型，返回正确的命令分隔符

    CMD 不支持 ; 需要用 &
    Bash/PowerShell 用 ;
    """
    try:
        # 读屏幕最后 5 行来检测提示符特征
        end = _current_row()
        start = max(0, end - 5)
        recent = _read_screen(start, end)
        last_line = recent.split("\n")[-1] if recent else ""

        # CMD 特征：路径以盘符开头 或 提示符以 > 结尾
        if ">" in last_line[-3:] and ":\\" not in last_line[-60:]:
            # 可能是 CMD (C:\Users\xxx>) 或 PowerShell (PS C:\Users\xxx>)
            if "PS " in last_line:
                return ";"  # PowerShell 支持 ;
            return "&"   # CMD 不支持 ;
        return ";"  # 默认 Unix shell
    except Exception:
        return ";"


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
    start_row = _current_row()
    cmd = req.get("cmd", "")
    marker = req.get("marker", "")
    timeout_ms = req.get("timeout_ms", 30000)

    sep = _detect_separator()
    full_cmd = cmd + " " + sep + " echo " + marker
    xsh.Screen.Send(full_cmd + "\r")

    # 轮询等待 marker
    timed_out = True
    elapsed = 0
    while elapsed < timeout_ms:
        xsh.Session.Sleep(200)
        elapsed += 200
        end_row = _current_row()
        chk_start = max(0, end_row - 8)
        recent = _read_screen(chk_start, end_row)
        if marker in recent:
            timed_out = False
            break

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


def _handle_send_raw(req):
    start_row = _current_row()
    text = req.get("cmd", "")
    wait_for = req.get("wait_for", "")
    timeout_ms = req.get("timeout_ms", 30000)

    xsh.Screen.Send(text)

    timed_out = True
    if wait_for:
        elapsed = 0
        while elapsed < timeout_ms:
            xsh.Session.Sleep(200)
            elapsed += 200
            end_row = _current_row()
            chk_start = max(0, end_row - 8)
            recent = _read_screen(chk_start, end_row)
            if wait_for in recent:
                timed_out = False
                break
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
    xsh.Screen.Send("\x03")
    return {"success": True, "output": "", "timed_out": False,
            "start_row": 0, "end_row": 0,
            "screen_rows": _total_rows(), "screen_cols": SCREEN_COLS}


def _handle_check():
    return {"success": True, "output": "bridge v3 online",
            "timed_out": False, "start_row": 0, "end_row": 0,
            "screen_rows": _total_rows(), "screen_cols": SCREEN_COLS,
            "current_row": _current_row(), "connected": _is_connected()}


# ============================================================
# 终端辅助
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
    except Exception:
        return ""


# ============================================================
# 主循环
# ============================================================

def Main():
    try:
        xsh.Screen.Synchronous = True
    except Exception:
        pass

    if not os.path.isdir(IPC_DIR):
        os.makedirs(IPC_DIR, exist_ok=True)

    _write_resp({
        "success": True, "output": "bridge v3 started", "timed_out": False,
        "start_row": 0, "end_row": 0, "screen_rows": _total_rows(),
        "screen_cols": SCREEN_COLS, "current_row": _current_row()
    })

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
