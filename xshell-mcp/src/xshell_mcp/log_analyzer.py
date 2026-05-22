"""日志分析核心逻辑：命令构建与输出解析

针对 Linux 远程主机上的日志文件，封装 search/extract/filter/context
四类 shell 命令的生成逻辑，以及 extract 结果的输出解析。

设计原则：
1. 统一使用 zgrep 处理混合的 .gz 与普通文件（zgrep 兼容两种）；
2. 使用 shlex.quote 对用户输入做 shell 转义，确保安全；
3. 分页通过 `tail -n +{offset+1} | head -n {max_lines}` 组合实现；
4. extract 模式必须设置行数上限（max_extract_lines），避免高基数关键字
   导致输出爆炸（参见三阶段工作流规范）。
"""

import shlex
import time

from .config import LogConfig  # re-export 便于上层模块统一导入

__all__ = [
    "LogConfig",
    "build_search_command",
    "build_extract_command",
    "build_filter_command",
    "build_context_command",
    "build_file_glob",
    "generate_cache_filename",
    "is_compressed",
    "parse_extract_result",
]


# ---------------------------------------------------------------------------
# 内部工具函数
# ---------------------------------------------------------------------------

def _build_time_pattern(time_range: str) -> str:
    """将 "HH:MM-HH:MM" 形式的时间范围解析为正则匹配片段。

    生成形如 "(14:30|14:31|14:32)" 的正则，用于与 keyword 拼接。
    解析失败或范围非法时返回空字符串。
    """
    if not time_range or "-" not in time_range:
        return ""

    try:
        start_str, end_str = time_range.split("-", 1)
        start_h, start_m = (int(x) for x in start_str.strip().split(":", 1))
        end_h, end_m = (int(x) for x in end_str.strip().split(":", 1))
    except (ValueError, AttributeError):
        return ""

    if not (0 <= start_h <= 23 and 0 <= end_h <= 23):
        return ""
    if not (0 <= start_m <= 59 and 0 <= end_m <= 59):
        return ""

    start_total = start_h * 60 + start_m
    end_total = end_h * 60 + end_m
    if end_total < start_total:
        return ""

    # 限制最大枚举数量，避免正则过长（最多 240 分钟 = 4 小时）
    if end_total - start_total > 240:
        return ""

    minutes = []
    for total in range(start_total, end_total + 1):
        h, m = divmod(total, 60)
        minutes.append(f"{h:02d}:{m:02d}")

    if len(minutes) == 1:
        return minutes[0]
    return "(" + "|".join(minutes) + ")"


def _combine_pattern(time_pattern: str, keyword: str) -> str:
    """将时间正则与 keyword 组合为 grep 正则。

    若 time_pattern 为空，仅返回 keyword 本身；否则返回
    "{time_pattern}.*{keyword}"。
    """
    if not time_pattern:
        return keyword
    return f"{time_pattern}.*{keyword}"


def _pagination_suffix(offset: int, max_lines: int) -> str:
    """生成 `| tail -n +{offset+1} | head -n {max_lines}` 分页后缀。"""
    offset = max(0, int(offset))
    max_lines = max(1, int(max_lines))
    return f" | tail -n +{offset + 1} | head -n {max_lines}"


# ---------------------------------------------------------------------------
# 公共 API
# ---------------------------------------------------------------------------

def is_compressed(filename: str, compressed_extensions: list[str]) -> bool:
    """判断文件是否为压缩文件（按扩展名匹配）。"""
    if not filename or not compressed_extensions:
        return False
    return any(filename.endswith(ext) for ext in compressed_extensions)


def generate_cache_filename() -> str:
    """生成唯一缓存文件名。

    格式：/tmp/xshell_cache_{timestamp_ms}.txt
    使用毫秒级时间戳确保同一秒内多次调用不冲突。
    """
    return f"/tmp/xshell_cache_{int(time.time() * 1000)}.txt"


def build_file_glob(log_dir: str, file_pattern: str) -> str:
    """构建文件 glob 表达式。

    log_dir 与 file_pattern 拼接为 "{log_dir}/{file_pattern}"。
    若 file_pattern 为空则使用默认 "*.log*"。末尾的 "/" 会被规范化。
    """
    pattern = file_pattern or "*.log*"
    base = (log_dir or "").rstrip("/")
    if not base:
        return pattern
    return f"{base}/{pattern}"


def build_search_command(
    keyword: str,
    log_dir: str,
    file_pattern: str,
    compressed_extensions: list[str],
    time_range: str = "",
    max_lines: int = 50,
    offset: int = 0,
    context_lines: int = 0,
) -> str:
    """生成直接搜索命令（search 模式）。

    在原始日志文件上搜索：由于目录中可能混合 .gz 与普通文件，
    统一使用 zgrep（zgrep 对两种文件均可处理）。

    Args:
        keyword: 搜索关键字（支持正则）
        log_dir: 日志目录
        file_pattern: glob 文件模式
        compressed_extensions: 压缩扩展名列表（保留参数，便于将来按需切换）
        time_range: 时间范围 "HH:MM-HH:MM"，非空时与 keyword 组合为正则
        max_lines: 单页最大返回行数
        offset: 分页偏移行数
        context_lines: 每个匹配前后的上下文行数（grep -C）

    Returns:
        可在远端 shell 直接执行的单行命令字符串
    """
    # 关键字与时间范围组合
    time_pattern = _build_time_pattern(time_range)
    pattern = _combine_pattern(time_pattern, keyword)

    # extended-regex 在拼接了 (a|b|c) 时是必需的
    use_extended = bool(time_pattern)
    extended_flag = " -E" if use_extended else ""

    context_flag = f" -C{int(context_lines)}" if context_lines and context_lines > 0 else ""

    file_glob = build_file_glob(log_dir, file_pattern)
    quoted_pattern = shlex.quote(pattern)

    # 注：file_glob 中含有 *，需让 shell 自行展开，因此不引号
    cmd = f"zgrep -n{extended_flag}{context_flag} {quoted_pattern} {file_glob}"
    cmd += _pagination_suffix(offset, max_lines)
    return cmd


def build_extract_command(
    keyword: str,
    log_dir: str,
    file_pattern: str,
    max_extract_lines: int,
    cache_path: str,
) -> str:
    """生成提取缓存命令（extract 模式）。

    原子化搜索全部文件并写入临时缓存。使用 -H -n 同时保留文件名和
    行号，便于后续 context 模式精确定位。通过 head -n {max_extract_lines}
    强制截断，避免 traceId 等高基数关键字造成输出爆炸。

    示例：
        zgrep -H -n "trace123" /logs/*.log* 2>/dev/null \
            | head -n 10000 > /tmp/xshell_cache_xxx.txt; \
            wc -l /tmp/xshell_cache_xxx.txt
    """
    file_glob = build_file_glob(log_dir, file_pattern)
    quoted_pattern = shlex.quote(keyword)
    quoted_cache = shlex.quote(cache_path)
    limit = max(1, int(max_extract_lines))

    extract = (
        f"zgrep -H -n {quoted_pattern} {file_glob} 2>/dev/null"
        f" | head -n {limit} > {quoted_cache}"
    )
    # 用分号串联：第一段写缓存，第二段返回缓存行数（供解析判断是否截断）
    return f"{extract}; wc -l {quoted_cache}"


def build_filter_command(
    keyword: str,
    cache_file: str,
    max_lines: int = 50,
    offset: int = 0,
) -> str:
    """生成二次过滤命令（filter 模式）。

    缓存文件是普通文本，使用 grep（无需 zgrep）。在已经缩小的快照上
    叠加关键字过滤，并支持分页。
    """
    quoted_pattern = shlex.quote(keyword)
    quoted_cache = shlex.quote(cache_file)
    cmd = f"grep -n {quoted_pattern} {quoted_cache}"
    cmd += _pagination_suffix(offset, max_lines)
    return cmd


def build_context_command(
    file_path: str,
    keyword: str,
    before: int = 20,
    after: int = 50,
    occurrence: int = 1,
    compressed_extensions: list[str] | None = None,
) -> str:
    """生成上下文获取命令（context 模式）。

    根据 file_path 是否为压缩文件，分别使用 zgrep / grep。

    实现策略：
    - occurrence == 1：直接使用 `-m1 -B{before} -A{after}` 一次性输出；
    - occurrence > 1：先通过 grep -n + sed 取得第 N 次匹配的行号，再用
      sed 截取 [行号-before, 行号+after] 范围。
    """
    if compressed_extensions is None:
        compressed_extensions = [".gz"]

    before = max(0, int(before))
    after = max(0, int(after))
    occurrence = max(1, int(occurrence))

    quoted_pattern = shlex.quote(keyword)
    quoted_file = shlex.quote(file_path)
    compressed = is_compressed(file_path, compressed_extensions)

    if occurrence == 1:
        grep_bin = "zgrep" if compressed else "grep"
        return (
            f"{grep_bin} -m1 -n -B{before} -A{after} "
            f"{quoted_pattern} {quoted_file}"
        )

    # occurrence > 1：两步命令，先取行号再截取范围
    if compressed:
        line_cmd = (
            f"zcat {quoted_file} | grep -n {quoted_pattern}"
            f" | sed -n '{occurrence}p' | cut -d: -f1"
        )
        slice_cmd = (
            f"zcat {quoted_file} | sed -n \"$((LINE-{before})),$((LINE+{after}))p\""
        )
    else:
        line_cmd = (
            f"grep -n {quoted_pattern} {quoted_file}"
            f" | sed -n '{occurrence}p' | cut -d: -f1"
        )
        slice_cmd = (
            f"sed -n \"$((LINE-{before})),$((LINE+{after}))p\" {quoted_file}"
        )

    return f"LINE=$({line_cmd}); [ -n \"$LINE\" ] && {slice_cmd}"


def parse_extract_result(raw_output: str, cache_path: str) -> dict:
    """解析 extract 命令的输出。

    extract 命令最后执行 `wc -l <cache>`，输出形如：
        "  8500 /tmp/xshell_cache_xxx.txt"

    Args:
        raw_output: 远端 shell 返回的原始文本（可能含多行/前置噪声）
        cache_path: 期望解析的缓存文件路径，用于截断行数判断

    Returns:
        dict 包含：
            - cache_file: 缓存文件路径
            - total_lines: 缓存中的实际行数（解析失败为 0）
            - truncated: 是否触达了 max_extract_lines 上限（由调用方比对）
              此处仅返回 total_lines，调用方根据 max_extract_lines 判断
    """
    total_lines = 0
    if raw_output:
        # 倒序扫描行，找到第一行符合 "<num> <path>" 格式的 wc 输出
        for line in reversed(raw_output.strip().splitlines()):
            stripped = line.strip()
            if not stripped:
                continue
            parts = stripped.split(None, 1)
            if not parts:
                continue
            try:
                total_lines = int(parts[0])
                break
            except ValueError:
                continue

    return {
        "cache_file": cache_path,
        "total_lines": total_lines,
        "truncated": False,
    }
