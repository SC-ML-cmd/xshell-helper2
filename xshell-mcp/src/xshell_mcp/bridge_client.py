"""Bridge 客户端 — MCP Server 通过文件 IPC 与 Xshell Bridge 通信"""

import json
import os
import time
from pathlib import Path

from .exceptions import BridgeNotReadyError, BridgeTimeoutError, BridgeConnectionError
from .protocol import Request, Response


class BridgeClient:
    def __init__(self, ipc_dir: str, timeout: int = 30):
        self._ipc_dir = Path(ipc_dir)
        self._req_file = self._ipc_dir / ".request.json"
        self._resp_file = self._ipc_dir / ".response.json"
        self._default_timeout = timeout

    def initialize(self):
        self._ipc_dir.mkdir(parents=True, exist_ok=True)

    def check_bridge(self) -> bool:
        req = Request(type="check")
        try:
            resp = self._send_request(req, timeout=10)
            return resp.success
        except Exception:
            return False

    def execute(self, cmd: str, marker: str, timeout: int = 0) -> Response:
        if timeout <= 0:
            timeout = self._default_timeout
        req = Request(
            type="exec",
            cmd=cmd,
            marker=marker,
            timeout_ms=timeout * 1000,
        )
        return self._send_request(req, timeout)

    def send_raw(self, text: str, wait_for: str, timeout: int = 0) -> Response:
        if timeout <= 0:
            timeout = self._default_timeout
        req = Request(
            type="send_raw",
            cmd=text,
            wait_for=wait_for,
            timeout_ms=timeout * 1000,
        )
        return self._send_request(req, timeout)

    def get_screen(self, lines: int = 50, timeout: int = 5) -> Response:
        req = Request(type="get_screen", lines=lines)
        return self._send_request(req, timeout=timeout)

    def interrupt(self, timeout: int = 3) -> Response:
        req = Request(type="interrupt")
        return self._send_request(req, timeout=timeout)

    def _send_request(self, req: Request, timeout: int) -> Response:
        if timeout <= 0:
            timeout = self._default_timeout

        # 确保目录存在
        self._ipc_dir.mkdir(parents=True, exist_ok=True)

        # 删除旧的响应文件（避免读到上一次结果）
        _remove_if_exists(self._resp_file)

        # 写入请求文件
        _write_json(self._req_file, req)

        # 轮询等待响应
        deadline = time.time() + timeout + 2  # 额外 2 秒容差
        while time.time() < deadline:
            resp_data = _read_json(self._resp_file)
            if resp_data is not None:
                resp = Response(**{k: v for k, v in resp_data.items() if k in Response._FIELDS})
                return resp
            time.sleep(0.1)

        raise BridgeTimeoutError(
            "Bridge 命令执行超时 ({}s): {}".format(timeout, req.cmd[:80])
        )


def _write_json(path, obj):
    data = obj if isinstance(obj, dict) else obj.to_json() if hasattr(obj, 'to_json') else json.dumps(obj)
    if isinstance(data, str):
        data = data.encode("utf-8")
    elif not isinstance(data, bytes):
        data = json.dumps(data).encode("utf-8")

    # 先写临时文件再 rename，保证原子性
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
