"""输出处理器单元测试"""
import pytest
from xshell_mcp.output_processor import strip_ansi, clean_command_output, truncate_output


class TestStripAnsi:
    def test_color_codes(self):
        assert strip_ansi("\x1b[32mgreen\x1b[0m") == "green"

    def test_bold(self):
        assert strip_ansi("\x1b[1mbold\x1b[0m") == "bold"

    def test_crlf_to_lf(self):
        assert strip_ansi("line1\r\nline2\r\n") == "line1\nline2\n"

    def test_window_title(self):
        assert strip_ansi("\x1b]0;title\x07text") == "text"

    def test_no_ansi(self):
        assert strip_ansi("plain text") == "plain text"


class TestCleanCommandOutput:
    def test_removes_command_echo_and_marker(self):
        # 真实终端格式: prompt + cmd;echo marker / output / marker / prompt
        raw = "[root@host ~]# ls -la ; echo __XSH_X__\nfile1\nfile2\n__XSH_X__\n[root@host ~]# "
        result = clean_command_output(raw, "ls -la", "__XSH_X__")
        assert "ls -la" not in result
        assert "__XSH_X__" not in result
        assert "file1" in result
        assert "file2" in result

    def test_keeps_output_lines(self):
        raw = "[root@k8s ~]# kubectl get pods ; echo __XSH_X__\nNAME  READY\npod-a  1/1\n__XSH_X__\n[root@k8s ~]# "
        result = clean_command_output(raw, "kubectl get pods", "__XSH_X__")
        assert "NAME  READY" in result
        assert "pod-a  1/1" in result
        assert "__XSH_X__" not in result
        assert "kubectl" not in result

    def test_single_line_output(self):
        raw = "user@host:~$ whoami ; echo __XSH_MARK__\nroot\n__XSH_MARK__\nuser@host:~$ "
        result = clean_command_output(raw, "whoami", "__XSH_MARK__")
        assert result == "root"

    def test_handles_prompt_prefix(self):
        raw = "[root@host ~]# ls ; echo __XSH_X__\noutput\n__XSH_X__\n[root@host ~]# "
        result = clean_command_output(raw, "ls", "__XSH_X__")
        assert "output" in result
        assert "__XSH_X__" not in result

    def test_marker_not_at_last_line(self):
        # marker 可能在 prompt 之前，而不是最后一行
        raw = "cmd ; echo __XSH_X__\ndata\n__XSH_X__\nprompt$ "
        result = clean_command_output(raw, "cmd", "__XSH_X__")
        assert result == "data"


class TestTruncate:
    def test_no_truncation_needed(self):
        text, truncated = truncate_output("short", 1000)
        assert not truncated
        assert text == "short"

    def test_truncation(self):
        text, truncated = truncate_output("x" * 2000, 100)
        assert truncated
        assert len(text) < 2000
        assert "truncated" in text
