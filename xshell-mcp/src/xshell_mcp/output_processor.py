"""终端输出清理：ANSI 转义序列、命令回显、marker 行"""

import re

# ANSI CSI/OSC 等 escape 序列（不包含 \r\n，它单独处理）
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;]*[a-zA-Z]"       # CSI: ESC [ params letter
    r"|\x1b\][^\x07]*\x07"         # OSC: ESC ] ... BEL
    r"|\x1b\][^\\]*\x1b\\\\"        # OSC (ST terminator)
    r"|\x1b[\[\(][0-9;]*[^a-zA-Z]?" # other ESC sequences
    r"|\x1b\]0;[^\x07]*\x07"        # window title
    r"|\x1b\[\?[0-9;]*[a-zA-Z]"     # DEC private modes
)


def strip_ansi(text: str) -> str:
    """移除 ANSI 转义序列，规范化换行"""
    text = _ANSI_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def clean_command_output(raw: str, cmd: str, marker: str) -> str:
    """清理命令执行的原始输出

    终端输出格式:
      [prompt] cmd ; echo marker
      <实际输出>
      marker
      [prompt]

    处理：去除 ANSI、首行命令回显、marker 行、末尾 prompt 行
    """
    text = strip_ansi(raw)
    lines = text.split("\n")

    # 1. 找到 marker 所在行（marker 独占一行的索引）
    marker_idx = -1
    for i, line in enumerate(lines):
        if line.strip() == marker:
            marker_idx = i
            break
    # 如果没精确匹配，尝试包含匹配
    if marker_idx == -1:
        for i, line in enumerate(lines):
            if marker in line and len(line.strip()) < len(marker) + 10:
                marker_idx = i
                break

    # 2. 确定输出起始行：跳过首行命令回显
    # marker 不再出现在命令回显中（被 shell 空引号打断），只检查 cmd
    cmd_start = 0
    if lines and cmd in lines[0]:
        cmd_start = 1

    # 3. 提取输出行（命令回显之后、marker 之前）
    if marker_idx >= 0:
        output_lines = lines[cmd_start:marker_idx]
    else:
        output_lines = lines[cmd_start:]

    # 4. 去除末尾 prompt 行（在 marker 之后的那一行，通常是新提示符）
    while output_lines and _looks_like_prompt(output_lines[-1]):
        output_lines.pop(-1)

    # 5. 去除首尾空行
    while output_lines and not output_lines[0].strip():
        output_lines.pop(0)
    while output_lines and not output_lines[-1].strip():
        output_lines.pop(-1)

    return "\n".join(output_lines)


def _looks_like_prompt(line: str) -> bool:
    """判断一行是否像 shell 提示符"""
    s = line.strip()
    if not s:
        return False
    # 常见的提示符特征
    return s.endswith("#") or s.endswith("$") or s.endswith(">")


def truncate_output(text: str, max_chars: int = 100000) -> tuple[str, bool]:
    """截断过长输出"""
    if len(text) <= max_chars:
        return text, False
    head = text[:max_chars // 2]
    tail = text[-(max_chars // 2):]
    return head + f"\n\n... [{len(text) - max_chars} chars truncated] ...\n\n" + tail, True
