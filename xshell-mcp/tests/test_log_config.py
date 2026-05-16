"""log_config 模块单元测试"""
import logging
import tempfile
from pathlib import Path

from xshell_mcp.log_config import (
    setup_logging,
    get_logger,
    generate_request_id,
    set_request_id,
)


def _cleanup_logging():
    """关闭 root logger 与 xshell_mcp logger 的 handler，释放文件锁"""
    for logger_name in ("xshell_mcp", ""):
        lg = logging.getLogger(logger_name)
        for h in lg.handlers[:]:
            h.close()
            lg.removeHandler(h)


class TestGenerateRequestId:
    def test_format(self):
        rid = generate_request_id()
        parts = rid.split("-")
        assert len(parts) == 2
        assert len(parts[0]) == 5
        assert len(parts[1]) == 5
        assert parts[0].isdigit()
        assert parts[1].isdigit()

    def test_uniqueness(self):
        ids = {generate_request_id() for _ in range(100)}
        assert len(ids) == 100


class TestSetupLogging:
    def test_creates_log_file(self):
        with tempfile.TemporaryDirectory() as d:
            try:
                setup_logging(d, "INFO")
                logger = get_logger()
                logger.info("test message")

                log_files = list(Path(d).glob("xshell_mcp.log*"))
                assert len(log_files) >= 1
                content = (Path(d) / "xshell_mcp.log").read_text()
                assert "test message" in content
            finally:
                _cleanup_logging()

    def test_format_includes_request_id(self):
        with tempfile.TemporaryDirectory() as d:
            try:
                setup_logging(d, "INFO")
                set_request_id("12345-00042")
                logger = get_logger()
                logger.info("hello")

                content = (Path(d) / "xshell_mcp.log").read_text()
                assert "[12345-00042]" in content
            finally:
                _cleanup_logging()

    def test_empty_request_id_when_not_set(self):
        with tempfile.TemporaryDirectory() as d:
            try:
                set_request_id("")  # 显式清空，避免其他测试的 contextvar 泄漏
                setup_logging(d, "INFO")
                logger = get_logger()
                logger.info("no rid")

                content = (Path(d) / "xshell_mcp.log").read_text()
                assert "[]" in content
            finally:
                _cleanup_logging()

    def test_rotating_handler_configured(self):
        with tempfile.TemporaryDirectory() as d:
            try:
                setup_logging(d, "INFO")
                root = logging.getLogger()
                assert len(root.handlers) >= 1
                handler = root.handlers[0]
                assert handler.maxBytes == 500 * 1024
                assert handler.backupCount == 5
            finally:
                _cleanup_logging()
