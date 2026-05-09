"""Bridge ↔ MCP Server 通信协议定义"""

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Optional


class RequestType:
    EXEC = "exec"
    SEND_RAW = "send_raw"
    GET_SCREEN = "get_screen"
    INTERRUPT = "interrupt"
    CHECK = "check"


@dataclass
class Request:
    type: str
    seq_id: str = ""
    cmd: str = ""
    marker: str = ""
    wait_for: str = ""
    timeout_ms: int = 30000
    lines: int = 50

    def __post_init__(self):
        if not self.seq_id:
            self.seq_id = str(int(time.time() * 1000000))

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, s: str) -> "Request":
        d = json.loads(s)
        return cls(**{k: d.get(k, "") if k in ("type", "seq_id", "cmd", "marker", "wait_for") else d.get(k, 0) for k in d})


@dataclass
class Response:
    success: bool = True
    output: str = ""
    timed_out: bool = False
    start_row: int = 0
    end_row: int = 0
    screen_rows: int = 0
    screen_cols: int = 0
    error: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    _FIELDS = {"success", "output", "timed_out", "start_row", "end_row", "screen_rows", "screen_cols", "error"}

    @classmethod
    def from_json(cls, s: str) -> "Response":
        d = json.loads(s)
        return cls(**{k: v for k, v in d.items() if k in cls._FIELDS})

    @classmethod
    def error_response(cls, msg: str) -> "Response":
        return cls(success=False, error=msg)
