"""log_analyzer 模块的单元测试"""

import time

from xshell_mcp.config import LogConfig, load_log_config
from xshell_mcp.log_analyzer import (
    build_context_command,
    build_estimate_command,
    build_extract_command,
    build_extract_context_command,
    build_filter_command,
    build_search_command,
    generate_cache_filename,
    is_compressed,
    parse_estimate_result,
    parse_extract_result,
)


class TestBuildSearchCommand:
    """测试 search 模式命令生成"""

    def test_basic_search(self):
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
        assert "2>/dev/null" in cmd

    def test_search_with_time_range_uses_extended_regex(self):
        cmd = build_search_command(
            keyword="OOM",
            log_dir="/logs",
            file_pattern="*.log*",
            compressed_extensions=[".gz"],
            time_range="14:30-14:32",
        )
        assert "14:30" in cmd
        assert "14:31" in cmd
        assert "14:32" in cmd
        assert "OOM" in cmd
        assert "-E" in cmd

    def test_search_with_offset(self):
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
        cmd = build_search_command(
            keyword="Exception",
            log_dir="/logs",
            file_pattern="*.log*",
            compressed_extensions=[".gz"],
            context_lines=5,
        )
        assert "-C5" in cmd

    def test_search_fixed_string(self):
        cmd = build_search_command(
            keyword="traceId-abc.123",
            log_dir="/logs",
            file_pattern="*.log*",
            compressed_extensions=[".gz"],
            fixed_string=True,
        )
        assert "-F" in cmd
        assert "-E" not in cmd

    def test_search_fixed_string_with_time_range_fallback_to_regex(self):
        cmd = build_search_command(
            keyword="traceId-abc",
            log_dir="/logs",
            file_pattern="*.log*",
            compressed_extensions=[".gz"],
            time_range="14:30-14:31",
            fixed_string=True,
        )
        # 与时间范围组合时固定字符串会回落到正则模式
        assert "-F" not in cmd
        assert "-E" in cmd

    def test_search_case_insensitive(self):
        cmd = build_search_command(
            keyword="error",
            log_dir="/logs",
            file_pattern="*.log*",
            compressed_extensions=[".gz"],
            case_sensitive=False,
        )
        assert "-i" in cmd


class TestBuildEstimateCommand:
    """测试 estimate 模式命令生成"""

    def test_estimate_includes_cache_and_summary_markers(self):
        cmd = build_estimate_command(
            keyword="traceId-abc",
            log_dir="/logs",
            file_pattern="*.log*",
            max_extract_lines=5000,
            cache_path="/tmp/xshell_cache_123.txt",
        )
        assert "head -n 5000 > /tmp/xshell_cache_123.txt" in cmd
        assert "__XSH_ESTIMATE_TOTAL__" in cmd
        assert "__XSH_ESTIMATE_FILES__" in cmd
        assert "__XSH_ESTIMATE_TOP_BEGIN__" in cmd
        assert "__XSH_ESTIMATE_TOP_END__" in cmd


class TestBuildExtractCommand:
    """测试 extract 模式命令生成"""

    def test_basic_extract(self):
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
        cmd = build_extract_command(
            keyword="ERROR",
            log_dir="/logs",
            file_pattern="*.log*",
            max_extract_lines=5000,
            cache_path="/tmp/cache.txt",
        )
        assert "head -n 5000" in cmd

    def test_extract_includes_wc(self):
        cache = "/tmp/xshell_cache_999.txt"
        cmd = build_extract_command(
            keyword="test",
            log_dir="/logs",
            file_pattern="*.log*",
            max_extract_lines=10000,
            cache_path=cache,
        )
        assert cmd.endswith(f"wc -l {cache}") or "wc -l" in cmd


class TestBuildExtractContextCommand:
    """测试 extract_context 模式命令生成"""

    def test_basic_extract_context(self):
        cmd = build_extract_context_command(
            keyword="NullPointerException",
            log_dir="/logs",
            file_pattern="*.log*",
            max_extract_lines=8000,
            cache_path="/tmp/xshell_cache_888.txt",
            before=20,
            after=80,
        )
        assert "-B20" in cmd
        assert "-A80" in cmd
        assert "head -n 8000" in cmd
        assert "wc -l /tmp/xshell_cache_888.txt" in cmd


class TestBuildFilterCommand:
    """测试 filter 模式命令生成"""

    def test_basic_filter(self):
        cmd = build_filter_command(
            keyword="ERROR",
            cache_file="/tmp/xshell_cache_123.txt",
        )
        assert "grep -n" in cmd
        assert "ERROR" in cmd
        assert "/tmp/xshell_cache_123.txt" in cmd
        assert "head -n 50" in cmd

    def test_filter_fixed_string_case_insensitive(self):
        cmd = build_filter_command(
            keyword="traceId-ABC",
            cache_file="/tmp/cache.txt",
            fixed_string=True,
            case_sensitive=False,
        )
        assert "-F" in cmd
        assert "-i" in cmd

    def test_filter_with_offset(self):
        cmd = build_filter_command(
            keyword="WARN",
            cache_file="/tmp/cache.txt",
            max_lines=30,
            offset=50,
        )
        assert "tail -n +51" in cmd
        assert "head -n 30" in cmd


class TestBuildContextCommand:
    """测试 context 模式命令生成"""

    def test_context_compressed_file(self):
        cmd = build_context_command(
            file_path="/logs/app.log.gz",
            keyword="NullPointer",
            compressed_extensions=[".gz"],
        )
        assert "zgrep" in cmd
        assert "NullPointer" in cmd
        assert "/logs/app.log.gz" in cmd

    def test_context_plain_file(self):
        cmd = build_context_command(
            file_path="/logs/app.log",
            keyword="Exception",
            compressed_extensions=[".gz"],
        )
        assert cmd.startswith("grep")
        assert "zgrep" not in cmd
        assert "Exception" in cmd

    def test_context_occurrence_1(self):
        cmd = build_context_command(
            file_path="/logs/app.log",
            keyword="error",
            occurrence=1,
        )
        assert "-m1" in cmd

    def test_context_occurrence_gt_1(self):
        cmd = build_context_command(
            file_path="/logs/app.log",
            keyword="error",
            occurrence=3,
            compressed_extensions=[".gz"],
            fixed_string=True,
            case_sensitive=False,
        )
        assert "LINE=" in cmd
        assert "sed" in cmd
        assert "-F" in cmd
        assert "-i" in cmd
        assert "zcat" not in cmd


class TestGenerateCacheFilename:
    """测试缓存文件名生成"""

    def test_format(self):
        name = generate_cache_filename()
        assert name.startswith("/tmp/xshell_cache_")
        assert name.endswith(".txt")

    def test_uniqueness(self):
        name1 = generate_cache_filename()
        time.sleep(0.002)
        name2 = generate_cache_filename()
        assert name1 != name2

    def test_contains_numeric_timestamp(self):
        name = generate_cache_filename()
        ts_part = name.replace("/tmp/xshell_cache_", "").replace(".txt", "")
        assert ts_part.isdigit()


class TestParseExtractResult:
    """测试 extract / extract_context 结果解析"""

    def test_parse_wc_output(self):
        raw = "  8500 /tmp/xshell_cache_123.txt"
        result = parse_extract_result(raw, "/tmp/xshell_cache_123.txt")
        assert result["cache_file"] == "/tmp/xshell_cache_123.txt"
        assert result["total_lines"] == 8500
        assert result["truncated"] is False

    def test_parse_with_truncated_true(self):
        raw = "10000 /tmp/cache.txt"
        result = parse_extract_result(raw, "/tmp/cache.txt", max_extract_lines=10000)
        assert result["total_lines"] == 10000
        assert result["truncated"] is True

    def test_parse_with_noise(self):
        raw = "some noise\nwarning: something\n  42 /tmp/cache.txt\n"
        result = parse_extract_result(raw, "/tmp/cache.txt")
        assert result["total_lines"] == 42


class TestParseEstimateResult:
    """测试 estimate 结果解析"""

    def test_parse_estimate_markers(self):
        raw = """__XSH_ESTIMATE_TOTAL__:120
__XSH_ESTIMATE_FILES__:3
__XSH_ESTIMATE_TOP_BEGIN__
  80 /logs/a.log
  30 /logs/b.log.gz
  10 /logs/c.log.gz
__XSH_ESTIMATE_TOP_END__
"""
        result = parse_estimate_result(raw, "/tmp/xshell_cache_1.txt", max_extract_lines=5000)
        assert result["cache_file"] == "/tmp/xshell_cache_1.txt"
        assert result["total_lines"] == 120
        assert result["matched_files"] == 3
        assert len(result["top_files"]) == 3
        assert result["top_files"][0]["file"] == "/logs/a.log"
        assert result["top_files"][0]["count"] == 80
        assert result["truncated"] is False

    def test_parse_estimate_truncated(self):
        raw = """__XSH_ESTIMATE_TOTAL__:10000
__XSH_ESTIMATE_FILES__:2
__XSH_ESTIMATE_TOP_BEGIN__
__XSH_ESTIMATE_TOP_END__
"""
        result = parse_estimate_result(raw, "/tmp/xshell_cache_2.txt", max_extract_lines=10000)
        assert result["truncated"] is True


class TestIsCompressed:
    """测试压缩文件判断"""

    def test_gz_file(self):
        assert is_compressed("app.log.gz", [".gz"]) is True

    def test_plain_log(self):
        assert is_compressed("app.log", [".gz"]) is False

    def test_multiple_extensions(self):
        assert is_compressed("app.log.zip", [".gz", ".zip"]) is True

    def test_empty_filename(self):
        assert is_compressed("", [".gz"]) is False

    def test_empty_extensions(self):
        assert is_compressed("app.log.gz", []) is False


class TestLoadLogConfig:
    """测试环境变量配置加载"""

    def test_load_default_config(self, monkeypatch):
        monkeypatch.delenv("XSH_SEARCH_LOG_DIR", raising=False)
        monkeypatch.delenv("XSH_SEARCH_FILE_PATTERN", raising=False)
        monkeypatch.delenv("XSH_SEARCH_COMPRESSED_EXTENSIONS", raising=False)
        monkeypatch.delenv("XSH_SEARCH_MAX_EXTRACT_LINES", raising=False)

        cfg = load_log_config()
        assert isinstance(cfg, LogConfig)
        assert cfg.log_dir == "/logs"
        assert cfg.file_pattern == "*.log*"
        assert cfg.compressed_extensions == [".gz"]
        assert cfg.max_extract_lines == 10000

    def test_load_from_env(self, monkeypatch):
        monkeypatch.setenv("XSH_SEARCH_LOG_DIR", "/app/logs")
        monkeypatch.setenv("XSH_SEARCH_FILE_PATTERN", "service-*.log*")
        monkeypatch.setenv("XSH_SEARCH_COMPRESSED_EXTENSIONS", ".gz,.zip")
        monkeypatch.setenv("XSH_SEARCH_LOG_FORMAT", "json")
        monkeypatch.setenv("XSH_SEARCH_TIMESTAMP_FORMAT", "ISO8601")
        monkeypatch.setenv("XSH_SEARCH_FILE_NAMING", "service-{date}.log")
        monkeypatch.setenv("XSH_SEARCH_MAX_EXTRACT_LINES", "5000")
        monkeypatch.setenv("XSH_SEARCH_LOG_DESCRIPTION", "测试配置")

        cfg = load_log_config()
        assert cfg.log_dir == "/app/logs"
        assert cfg.file_pattern == "service-*.log*"
        assert cfg.compressed_extensions == [".gz", ".zip"]
        assert cfg.log_format == "json"
        assert cfg.timestamp_format == "ISO8601"
        assert cfg.file_naming == "service-{date}.log"
        assert cfg.max_extract_lines == 5000
        assert cfg.description == "测试配置"

