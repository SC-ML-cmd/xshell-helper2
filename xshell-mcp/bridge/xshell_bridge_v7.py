"""
Xshell Bridge v7.0
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
- **新增**：命令追加 `2>&1` 将 stderr 重定向到 stdout，
  确保错误信息也能被捕获
- **修复**：_handle_exec / _handle_send_raw 用轮询替代 WaitForString，
  每 200ms 检测屏幕末尾，超时自动 Ctrl+C，解决网络波动导致永久卡死
- **新增**：多会话注册机制，支持 session registry 自动发现
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

    # 心跳间隔（秒），时间驱动，不受 sleep 策略影响
    HEARTBEAT_INTERVAL_SEC = 60


CFG = BridgeConfig()

# ============================================================
# IPC 路径（多会话支持）
# ============================================================

# --- Session ID 和 IPC 路径 ---
SESSION_ID = "session_" + str(os.getpid())
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IPC_BASE = os.path.join(_SCRIPT_DIR, "..", "ipc")

# 环境变量覆盖 IPC_BASE
if os.environ.get("XSH_IPC_BASE"):
    IPC_BASE = os.environ["XSH_IPC_BASE"]

# 向后兼容：XSH_IPC_DIR 存在时直接使用，不走注册机制
_LEGACY_MODE = False
if os.environ.get("XSH_IPC_DIR"):
    IPC_DIR = os.environ["XSH_IPC_DIR"]
    REGISTRY_DIR = None
    _LEGACY_MODE = True
elif len(sys.argv) > 1:
    IPC_DIR = sys.argv[1]
    REGISTRY_DIR = None
    _LEGACY_MODE = True
else:
    IPC_DIR = os.path.join(IPC_BASE, "sessions", SESSION_ID)
    REGISTRY_DIR = os.path.join(IPC_BASE, "registry")

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
# 注册机制
# ============================================================

def _write_json_file(path, data):
    """原子写 JSON 文件（写到 .tmp 再 rename）"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _write_registry():
    """启动时写注册文件到 registry 目录"""
    if REGISTRY_DIR is None:
        return
    os.makedirs(REGISTRY_DIR, exist_ok=True)
    reg_file = os.path.join(REGISTRY_DIR, SESSION_ID + ".json")
    reg_data = {
        "session_id": SESSION_ID,
        "remote_address": _safe_call(lambda: xsh.Session.RemoteAddress, ""),
        "remote_port": _safe_call(lambda: xsh.Session.RemotePort, 0),
        "local_address": _safe_call(lambda: xsh.Session.LocalAddress, ""),
        "session_path": _safe_call(lambda: xsh.Session.Path, ""),
        "session_name": _safe_call(lambda: xsh.Session.SessionName, ""),
        "tab_text": _safe_call(lambda: xsh.Session.TabText, ""),
        "user_name": _safe_call(lambda: xsh.Session.UserName, ""),
        "connected": _safe_call(lambda: xsh.Session.Connected, False),
        "ipc_dir": os.path.abspath(IPC_DIR),
        "pid": os.getpid(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "last_heartbeat": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "bound_by": 0,
        "bound_at": ""
    }
    _write_json_file(reg_file, reg_data)
    _log("REGISTRY written: %s" % reg_file)

    # 修改 XShell 页签名为 SESSION_ID [未绑定]，便于用户辨认绑定状态
    try:
        xsh.Session.TabText = SESSION_ID + " [未绑定]"
        _log("TABTITLE set to: %s [未绑定]" % SESSION_ID)
    except Exception as e:
        _log("TABTITLE set failed (ignored): %s" % e)


def _update_registry_heartbeat():
    """心跳时更新注册文件的 last_heartbeat 和页签绑定状态"""
    if REGISTRY_DIR is None:
        return
    reg_file = os.path.join(REGISTRY_DIR, SESSION_ID + ".json")
    try:
        with open(reg_file, "r", encoding="utf-8") as f:
            reg_data = json.loads(f.read())
        reg_data["last_heartbeat"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        reg_data["connected"] = _safe_call(lambda: xsh.Session.Connected, False)
        _write_json_file(reg_file, reg_data)

        # 根据 bound_by 动态更新 XShell 页签标题
        bound_by = reg_data.get("bound_by", 0)
        if bound_by == 0:
            new_title = SESSION_ID + " [未绑定]"
        else:
            new_title = SESSION_ID + " [已绑定:" + str(bound_by) + "]"

        current_title = _safe_call(lambda: xsh.Session.TabText, "")
        if current_title != new_title:
            try:
                xsh.Session.TabText = new_title
                _log("TABTITLE updated: %s" % new_title)
            except Exception as e:
                _log("TABTITLE update failed (ignored): %s" % e)
    except Exception as e:
        _log("REGISTRY heartbeat update failed: %s" % e)


def _remove_registry():
    """退出时删除注册文件和 IPC 目录"""
    if REGISTRY_DIR is None:
        return
    reg_file = os.path.join(REGISTRY_DIR, SESSION_ID + ".json")
    try:
        if os.path.isfile(reg_file):
            os.remove(reg_file)
            _log("REGISTRY removed: %s" % reg_file)
    except OSError as e:
        _log("REGISTRY remove failed: %s" % e)


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
    full_cmd = cmd + " 2>&1 " + sep + " echo " + escaped_marker
    xsh.Screen.Send(full_cmd + "\r")
    deadline = time.time() + timeout_ms / 1000.0
    marker_found = False
    while time.time() < deadline:
        _smart_sleep(200)
        end = _total_rows()
        recent = _read_screen(start_row, end)
        if marker in recent:
            marker_found = True
            break
    timed_out = not marker_found
    if timed_out:
        _log("EXEC timeout: %s [sending Ctrl+C]" % (cmd[:120]))
        xsh.Screen.Send("\x03")

    end_row = _total_rows()
    output = _read_screen(start_row, end_row)
    _log("EXEC done: %s [start=%d end=%d output_len=%d timed_out=%s]" % (cmd[:120], start_row, end_row, len(output), timed_out))

    return {
        "success": True, "output": output, "timed_out": timed_out,
        "start_row": start_row, "end_row": end_row,
        "screen_rows": _total_rows(), "screen_cols": CFG.SCREEN_COLS,
    }


def _handle_send_raw(req):
    text = req.get("cmd", "")
    wait_for = req.get("wait_for", "")
    start_row = _current_row()

    _log("SEND_RAW: %s [wait=%s]" % (text[:120], wait_for))
    xsh.Screen.Send(text + "\r")

    timeout_ms = req.get("timeout_ms", 30000)
    timed_out = False
    if wait_for:
        deadline = time.time() + timeout_ms / 1000.0
        marker_found = False
        while time.time() < deadline:
            _smart_sleep(200)
            end = _total_rows()
            recent = _read_screen(start_row, end)
            if wait_for in recent:
                marker_found = True
                break
        timed_out = not marker_found

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
    return {"success": True, "output": "bridge v7.0 online",
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

    # 注册到 registry
    _write_registry()

    _log("v7.0 STARTED pid=%s ipc=%s cfg=%s" % (
        os.getpid(), IPC_DIR,
        {k: v for k, v in vars(CFG).items() if not k.startswith("_")}
    ))

    _write_resp({
        "success": True, "output": "bridge v7.0 started", "timed_out": False,
        "start_row": 0, "end_row": 0,
        "screen_rows": _total_rows(), "screen_cols": CFG.SCREEN_COLS,
        "current_row": _current_row()
    })

    last_mtime = os.path.getmtime(REQ_FILE) if os.path.isfile(REQ_FILE) else 0
    last_hb_time = time.time()
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
        now = time.time()
        if now - last_hb_time >= CFG.HEARTBEAT_INTERVAL_SEC:
            _log("MAIN heartbeat iter=%d errors=%d sleep=%dms connected=%s" % (
                iteration, error_count, _get_sleep_ms(), _session_cache["connected"]))
            _update_registry_heartbeat()
            last_hb_time = now

    _log("EXIT reason=%s iteration=%d errors=%d" % (exit_reason, iteration, error_count))

    _remove_registry()

    _write_resp({
        "success": False, "error": "bridge exiting", "output": "bridge v7.0 stopped",
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
