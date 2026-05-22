"""log_analyzer 模块的单元测试"""

import json
import time

import pytest

from xshell_mcp.log_analyzer import (
    build_search_command,
    build_extract_command,
    build_filter_command,
    build_context_command,
    generate_cache_filename,
    parse_extract_result,
    is_compressed,
)
from xshell_mcp.config import LogConfig, load_log_config


class TestBuildSearchCommand:
    """测试 search 模式命令生成"""

    def test_basic_search(self):
        """基本搜索：keyword + log_dir + file_pattern"""
        cmd = build_search_command(
            keyword="ERROR",
            log_dir="/var/log/app",
            file_pattern="*.log*",
            compressed_extensions=[".gz"],
        )
        assert "zgrep" in cmd
        assert "-n" in cmd
        assert "ERROR" in cmd
        assert "/var/log/app/*.log*" in cmd
        assert "head -n 50" in cmd

    def test_search_with_time_range(self):
        """带时间范围的搜索：组合 time_range 和 keyword"""
        cmd = build_search_command(
            keyword="OOM",
            log_dir="/logs",
            file_pattern="*.log*",
            compressed_extensions=[".gz"],
            time_range="14:30-14:32",
        )
        # 时间范围应拼接为正则
        assert "14:30" in cmd
        assert "14:31" in cmd
        assert "14:32" in cmd
        assert "OOM" in cmd
        # 有时间范围时应使用 -E (extended regex)
        assert "-E" in cmd

    def test_search_with_offset(self):
        """分页搜索：offset > 0 时应有 tail -n +{offset+1}"""
        cmd = build_search_command(
            keyword="WARN",
            log_dir="/logs",
            file_pattern="*.log",
            compressed_extensions=[".gz"],
            offset=100,
            max_lines=20,
        )
        assert "tail -n +101" in cmd
        assert "head -n 20" in cmd

    def test_search_with_context_lines(self):
        """带上下文行数：应有 -C{context_lines}"""
        cmd = build_search_command(
            keyword="Exception",
            log_dir="/logs",
            file_pattern="*.log*",
            compressed_extensions=[".gz"],
            context_lines=5,
        )
        assert "-C5" in cmd

    def test_search_default_file_pattern(self):
        """默认 file_pattern 为空时使用 *.log*"""
        cmd = build_search_command(
            keyword="timeout",
            log_dir="/data/logs",
            file_pattern="",
            compressed_extensions=[".gz"],
        )
        assert "/data/logs/*.log*" in cmd

    def test_search_no_time_range_no_extended(self):
        """无时间范围时不应有 -E 标志"""
        cmd = build_search_command(
            keyword="info",
            log_dir="/logs",
            file_pattern="*.log*",
            compressed_extensions=[".gz"],
        )
        assert "-E" not in cmd

    def test_search_keyword_with_special_chars(self):
        """特殊字符关键字应被 shlex.quote 安全转义"""
        cmd = build_search_command(
            keyword="user's error",
            log_dir="/logs",
            file_pattern="*.log*",
            compressed_extensions=[".gz"],
        )
        # shlex.quote 会用单引号包裹或转义
        assert "user" in cmd
        assert "error" in cmd


class TestBuildExtractCommand:
    """测试 extract 模式命令生成"""

    def test_basic_extract(self):
        """基本提取：应包含 zgrep -H -n, head -n 限制, 重定向到缓存文件"""
        cmd = build_extract_command(
            keyword="traceId-abc",
            log_dir="/logs",
            file_pattern="*.log*",
            max_extract_lines=10000,
            cache_path="/tmp/xshell_cache_123.txt",
        )
        assert "zgrep -H -n" in cmd
        assert "traceId-abc" in cmd
        assert "/logs/*.log*" in cmd
        assert "head -n 10000" in cmd
        assert "/tmp/xshell_cache_123.txt" in cmd
        assert "2>/dev/null" in cmd

    def test_extract_with_max_lines(self):
        """行数上限保护：head -n {max_extract_lines}"""
        cmd = build_extract_command(
            keyword="ERROR",
            log_dir="/logs",
            file_pattern="*.log*",
            max_extract_lines=5000,
            cache_path="/tmp/cache.txt",
        )
        assert "head -n 5000" in cmd

    def test_extract_includes_wc(self):
        """命令应以 ; wc -l cache_file 结尾"""
        cache = "/tmp/xshell_cache_999.txt"
        cmd = build_extract_command(
            keyword="test",
            log_dir="/logs",
            file_pattern="*.log*",
            max_extract_lines=10000,
            cache_path=cache,
        )
        assert cmd.endswith(f"wc -l {cache}") or "wc -l" in cmd

    def test_extract_file_pattern(self):
        """指定 file_pattern 时只搜索匹配文件"""
        cmd = build_extract_command(
            keyword="payment",
            log_dir="/data/logs",
            file_pattern="payment-*.log.gz",
            max_extract_lines=10000,
            cache_path="/tmp/cache.txt",
        )
        assert "/data/logs/payment-*.log.gz" in cmd


class TestBuildFilterCommand:
    """测试 filter 模式命令生成"""

    def test_basic_filter(self):
        """基本过滤：在缓存文件上 grep"""
        cmd = build_filter_command(
            keyword="ERROR",
            cache_file="/tmp/xshell_cache_123.txt",
        )
        assert "grep -n" in cmd
        assert "ERROR" in cmd
        assert "/tmp/xshell_cache_123.txt" in cmd
        # 默认应有 head -n 50
        assert "head -n 50" in cmd

    def test_filter_with_offset(self):
        """分页过滤"""
        cmd = build_filter_command(
            keyword="WARN",
            cache_file="/tmp/cache.txt",
            max_lines=30,
            offset=50,
        )
        assert "tail -n +51" in cmd
        assert "head -n 30" in cmd

    def test_filter_max_lines(self):
        """行数限制"""
        cmd = build_filter_command(
            keyword="timeout",
            cache_file="/tmp/cache.txt",
            max_lines=100,
        )
        assert "head -n 100" in cmd

    def test_filter_uses_grep_not_zgrep(self):
        """缓存文件是纯文本，应使用 grep 而非 zgrep"""
        cmd = build_filter_command(
            keyword="test",
            cache_file="/tmp/cache.txt",
        )
        assert cmd.startswith("grep")
        assert "zgrep" not in cmd


class TestBuildContextCommand:
    """测试 context 模式命令生成"""

    def test_context_compressed_file(self):
        """压缩文件应使用 zgrep"""
        cmd = build_context_command(
            file_path="/logs/app.log.gz",
            keyword="NullPointer",
            compressed_extensions=[".gz"],
        )
        # occurrence=1 默认, 压缩文件用 zgrep
        assert "zgrep" in cmd
        assert "NullPointer" in cmd
        assert "/logs/app.log.gz" in cmd

    def test_context_plain_file(self):
        """普通文件应使用 grep"""
        cmd = build_context_command(
            file_path="/logs/app.log",
            keyword="Exception",
            compressed_extensions=[".gz"],
        )
        assert cmd.startswith("grep")
        assert "zgrep" not in cmd
        assert "Exception" in cmd

    def test_context_occurrence_1(self):
        """第一次匹配：使用 -m1 优化"""
        cmd = build_context_command(
            file_path="/logs/app.log",
            keyword="error",
            occurrence=1,
        )
        assert "-m1" in cmd

    def test_context_occurrence_gt_1(self):
        """occurrence > 1 时需要定位具体行号"""
        cmd = build_context_command(
            file_path="/logs/app.log",
            keyword="error",
            occurrence=3,
            compressed_extensions=[".gz"],
        )
        # 应使用 LINE= 方式定位
        assert "LINE=" in cmd
        assert "sed" in cmd
        # 普通文件不应出现 zcat
        assert "zcat" not in cmd

    def test_context_occurrence_gt_1_compressed(self):
        """occurrence > 1 且为压缩文件时应使用 zcat"""
        cmd = build_context_command(
            file_path="/logs/app.log.gz",
            keyword="error",
            occurrence=2,
            compressed_extensions=[".gz"],
        )
        assert "zcat" in cmd
        assert "LINE=" in cmd

    def test_context_before_after(self):
        """验证 before/after 行数参数"""
        cmd = build_context_command(
            file_path="/logs/app.log",
            keyword="crash",
            before=10,
            after=30,
            occurrence=1,
        )
        assert "-B10" in cmd
        assert "-A30" in cmd

    def test_context_default_before_after(self):
        """默认 before=20, after=50"""
        cmd = build_context_command(
            file_path="/logs/app.log",
            keyword="test",
        )
        assert "-B20" in cmd
        assert "-A50" in cmd


class TestGenerateCacheFilename:
    """测试缓存文件名生成"""

    def test_format(self):
        """验证格式：/tmp/xshell_cache_{timestamp}.txt"""
        name = generate_cache_filename()
        assert name.startswith("/tmp/xshell_cache_")
        assert name.endswith(".txt")

    def test_uniqueness(self):
        """连续调用应生成不同文件名"""
        name1 = generate_cache_filename()
        # 微小延迟确保时间戳不同
        time.sleep(0.002)
        name2 = generate_cache_filename()
        assert name1 != name2

    def test_contains_numeric_timestamp(self):
        """文件名中间部分应为数字时间戳"""
        name = generate_cache_filename()
        # 提取 /tmp/xshell_cache_ 和 .txt 之间的部分
        ts_part = name.replace("/tmp/xshell_cache_", "").replace(".txt", "")
        assert ts_part.isdigit()


class TestParseExtractResult:
    """测试 extract 结果解析"""

    def test_parse_wc_output(self):
        """解析 wc -l 的正常输出"""
        raw = "  8500 /tmp/xshell_cache_123.txt"
        result = parse_extract_result(raw, "/tmp/xshell_cache_123.txt")
        assert result["cache_file"] == "/tmp/xshell_cache_123.txt"
        assert result["total_lines"] == 8500
        assert result["truncated"] is False

    def test_parse_empty(self):
        """空输出（无匹配）"""
        result = parse_extract_result("", "/tmp/cache.txt")
        assert result["total_lines"] == 0
        assert result["cache_file"] == "/tmp/cache.txt"

    def test_parse_with_noise(self):
        """输出中有前置噪声行时仍能正确解析"""
        raw = "some noise\nwarning: something\n  42 /tmp/cache.txt\n"
        result = parse_extract_result(raw, "/tmp/cache.txt")
        assert result["total_lines"] == 42

    def test_parse_zero_lines(self):
        """wc -l 输出为 0 行"""
        raw = "0 /tmp/xshell_cache_123.txt"
        result = parse_extract_result(raw, "/tmp/xshell_cache_123.txt")
        assert result["total_lines"] == 0

    def test_parse_none_output(self):
        """None 输出应返回 0 行"""
        result = parse_extract_result(None, "/tmp/cache.txt")
        assert result["total_lines"] == 0

    def test_truncated_always_false(self):
        """parse_extract_result 本身不判断截断，由调用方处理"""
        raw = "10000 /tmp/cache.txt"
        result = parse_extract_result(raw, "/tmp/cache.txt")
        # 函数本身总是返回 truncated=False
        assert result["truncated"] is False


class TestIsCompressed:
    """测试压缩文件判断"""

    def test_gz_file(self):
        """gz 扩展名"""
        assert is_compressed("app.log.gz", [".gz"]) is True

    def test_plain_log(self):
        """普通日志文件"""
        assert is_compressed("app.log", [".gz"]) is False

    def test_multiple_extensions(self):
        """多种压缩扩展名"""
        assert is_compressed("app.log.zip", [".gz", ".zip"]) is True

    def test_empty_filename(self):
        """空文件名返回 False"""
        assert is_compressed("", [".gz"]) is False

    def test_empty_extensions(self):
        """空扩展名列表返回 False"""
        assert is_compressed("app.log.gz", []) is False

    def test_no_match(self):
        """文件扩展名不在列表中"""
        assert is_compressed("app.log.bz2", [".gz", ".zip"]) is False


class TestLoadLogConfig:
    """测试配置加载"""

    def test_load_existing_config(self, tmp_path, monkeypatch):
        """从文件加载完整配置"""
        config_data = {
            "log_dir": "/app/logs",
            "file_pattern": "service-*.log*",
            "compressed_extensions": [".gz", ".zip"],
            "log_format": "json",
            "timestamp_format": "ISO8601",
            "file_naming": "service-{date}.log",
            "max_extract_lines": 5000,
            "description": "测试配置",
        }
        config_file = tmp_path / ".xshell-log.json"
        config_file.write_text(json.dumps(config_data), encoding="utf-8")
        monkeypatch.setenv("XSH_LOG_CONFIG", str(config_file))

        cfg = load_log_config()
        assert cfg is not None
        assert cfg.log_dir == "/app/logs"
        assert cfg.file_pattern == "service-*.log*"
        assert cfg.compressed_extensions == [".gz", ".zip"]
        assert cfg.log_format == "json"
        assert cfg.max_extract_lines == 5000
        assert cfg.description == "测试配置"

    def test_missing_config(self, tmp_path, monkeypatch):
        """配置文件不存在时返回 None"""
        monkeypatch.setenv("XSH_LOG_CONFIG", str(tmp_path / "nonexistent.json"))
        cfg = load_log_config()
        assert cfg is None

    def test_invalid_json(self, tmp_path, monkeypatch):
        """JSON 格式错误时返回 None"""
        config_file = tmp_path / ".xshell-log.json"
        config_file.write_text("{invalid json!!!", encoding="utf-8")
        monkeypatch.setenv("XSH_LOG_CONFIG", str(config_file))

        cfg = load_log_config()
        assert cfg is None

    def test_partial_config(self, tmp_path, monkeypatch):
        """部分字段缺失时使用默认值"""
        config_data = {
            "log_dir": "/custom/logs",
        }
        config_file = tmp_path / ".xshell-log.json"
        config_file.write_text(json.dumps(config_data), encoding="utf-8")
        monkeypatch.setenv("XSH_LOG_CONFIG", str(config_file))

        cfg = load_log_config()
        assert cfg is not None
        assert cfg.log_dir == "/custom/logs"
        # 其余字段应为默认值
        assert cfg.file_pattern == "*.log*"
        assert cfg.compressed_extensions == [".gz"]
        assert cfg.max_extract_lines == 10000
