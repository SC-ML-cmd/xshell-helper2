"""Bridge 客户端 — MCP Server 通过文件 IPC 与 Xshell Bridge 通信"""

import json
import os
import threading
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
        self._lock = threading.Lock()

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
        with self._lock:
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
