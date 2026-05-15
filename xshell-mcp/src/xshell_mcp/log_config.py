"""Xshell MCP 文件日志配置 — RotatingFileHandler + request_id 链路追踪"""
import contextvars
import logging
import logging.handlers
import threading
import time
from pathlib import Path

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default=""
)
_counter = 0
_lock = threading.Lock()


def generate_request_id() -> str:
    """生成 8 位短 ID，格式 ttttt-nnnnn（时间戳后 5 位 + 5 位序号）"""
    global _counter
    with _lock:
        _counter = (_counter + 1) % 100000
        return "{:05d}-{:05d}".format(int(time.time()) % 100000, _counter)


def set_request_id(rid: str) -> None:
    """设置当前上下文的 request_id"""
    _request_id_var.set(rid)


class _RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = _request_id_var.get()
        return True


def setup_logging(log_dir: str, level: str = "INFO") -> None:
    """配置文件日志：RotatingFileHandler，500KB 轮转，保留 5 个文件"""
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        str(path / "xshell_mcp.log"),
        maxBytes=500 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s.%(msecs)03d %(levelname)-5s [%(request_id)s] "
            "%(filename)s:%(lineno)d %(funcName)s() | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root = logging.getLogger("xshell_mcp")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()
    root.addHandler(handler)
    root.addFilter(_RequestIdFilter())
    root.propagate = False


def get_logger(name: str = "xshell_mcp") -> logging.Logger:
    """获取 xshell_mcp 命名空间下的 logger"""
    return logging.getLogger(name)
