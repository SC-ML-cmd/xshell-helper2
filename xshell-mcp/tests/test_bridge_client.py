"""Bridge 客户端单元测试（使用模拟 Bridge）"""
import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from xshell_mcp.bridge_client import BridgeClient
from xshell_mcp.exceptions import BridgeTimeoutError


class TestBridgeClient:
    @pytest.fixture
    def tmp_ipc_dir(self):
        with tempfile.TemporaryDirectory() as d:
            yield d

    @pytest.fixture
    def client(self, tmp_ipc_dir):
        return BridgeClient(tmp_ipc_dir, timeout=3)

    def test_initialize_creates_dir(self, tmp_ipc_dir):
        c = BridgeClient(tmp_ipc_dir)
        c.initialize()
        assert Path(tmp_ipc_dir).exists()

    def test_check_bridge_returns_false_when_no_bridge(self, client):
        assert not client.check_bridge()

    def _write_response(self, ipc_dir: str, resp: dict):
        resp_file = Path(ipc_dir) / ".response.json"
        with open(resp_file, "w") as f:
            json.dump(resp, f)

    def _read_request(self, ipc_dir: str) -> dict | None:
        req_file = Path(ipc_dir) / ".request.json"
        try:
            with open(req_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def test_execute_basic_workflow(self, tmp_ipc_dir):
        """模拟：MCP 发请求 → 手动写响应（模拟 Bridge 行为）"""
        client = BridgeClient(tmp_ipc_dir, timeout=5)
        client.initialize()

        # 后台线程模拟 Bridge：等待请求出现后写入响应
        import threading

        def simulate_bridge():
            req_file = Path(tmp_ipc_dir) / ".request.json"
            resp_file = Path(tmp_ipc_dir) / ".response.json"
            deadline = time.time() + 4
            while time.time() < deadline:
                if req_file.exists():
                    with open(req_file, "r") as f:
                        req = json.load(f)
                    resp = {
                        "success": True,
                        "output": "NAME  READY\npod-a  1/1",
                        "timed_out": False,
                        "start_row": 10,
                        "end_row": 15,
                        "screen_rows": 200,
                        "screen_cols": 120,
                    }
                    with open(resp_file, "w") as f:
                        json.dump(resp, f)
                    return
                time.sleep(0.2)

        t = threading.Thread(target=simulate_bridge, daemon=True)
        t.start()

        resp = client.execute("kubectl get pods", "__XSH_1__", timeout=5)
        assert resp.success
        assert "pod-a" in resp.output
        assert not resp.timed_out

    def test_timeout(self, client):
        """没有 Bridge 响应时应超时"""
        with pytest.raises(BridgeTimeoutError):
            client.execute("ls", "__XSH_X__", timeout=1)

    def test_send_request_removes_old_response(self, tmp_ipc_dir):
        """确保旧的响应文件被清除"""
        client = BridgeClient(tmp_ipc_dir, timeout=3)
        client.initialize()

        # 写入旧响应
        self._write_response(tmp_ipc_dir, {"success": True, "output": "old",
                                           "timed_out": False, "start_row": 0,
                                           "end_row": 0, "screen_rows": 0,
                                           "screen_cols": 0, "error": ""})

        # 检查旧响应文件存在
        assert (Path(tmp_ipc_dir) / ".response.json").exists()

        # 由于没有 Bridge，这个会超时，但请求文件应该已写入
        try:
            client.execute("ls", "__XSH_X__", timeout=1)
        except BridgeTimeoutError:
            pass
