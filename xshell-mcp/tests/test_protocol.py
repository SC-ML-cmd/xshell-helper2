"""协议层单元测试"""
from xshell_mcp.protocol import Request, Response


class TestRequest:
    def test_exec_request(self):
        req = Request(type="exec", cmd="ls", marker="__XSH_1__")
        data = req.to_json()
        assert "exec" in data
        assert "ls" in data
        assert "__XSH_1__" in data

    def test_roundtrip(self):
        req = Request(type="exec", cmd="ls -la", marker="__XSH_X__", timeout_ms=5000)
        req2 = Request.from_json(req.to_json())
        assert req2.type == req.type
        assert req2.cmd == req.cmd
        assert req2.marker == req.marker
        assert req2.timeout_ms == req.timeout_ms

    def test_auto_seq_id(self):
        req1 = Request(type="check")
        req2 = Request(type="check")
        assert req1.seq_id != req2.seq_id


class TestResponse:
    def test_success_response(self):
        resp = Response(success=True, output="hello", screen_rows=100, screen_cols=80)
        data = resp.to_json()
        assert "true" in data
        assert "hello" in data

    def test_error_response(self):
        resp = Response.error_response("something wrong")
        assert not resp.success
        assert resp.error == "something wrong"

    def test_roundtrip(self):
        resp = Response(success=True, output="test", timed_out=False,
                        start_row=5, end_row=25, screen_rows=200, screen_cols=120)
        resp2 = Response.from_json(resp.to_json())
        assert resp2.success == resp.success
        assert resp2.output == resp.output
        assert resp2.start_row == resp.start_row
        assert resp2.end_row == resp.end_row
