"""启动 Xshell 并加载 Bridge 脚本"""

import os
import subprocess
import time
from pathlib import Path

from .config import XshellConfig
from .bridge_client import BridgeClient


def find_xshell(config: XshellConfig) -> str:
    """查找 Xshell.exe 路径"""
    path = config.xshell_path
    if path and Path(path).exists():
        return path

    # 常见安装路径
    candidates = [
        r"D:\software\xshell8\Xshell.exe",
        r"C:\Program Files\NetSarang\Xshell 8\Xshell.exe",
        r"C:\Program Files (x86)\NetSarang\Xshell 8\Xshell.exe",
        r"C:\Program Files\NetSarang\Xshell 7\Xshell.exe",
        r"C:\Program Files (x86)\NetSarang\Xshell 7\Xshell.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return ""


def launch_xshell(config: XshellConfig) -> bool:
    """启动 Xshell 并加载 Bridge 脚本"""
    xshell_exe = find_xshell(config)
    if not xshell_exe:
        raise FileNotFoundError("找不到 Xshell.exe，请设置 XSH_XSHELL_PATH 环境变量")

    bridge_script = config.bridge_script_path
    if not Path(bridge_script).exists():
        raise FileNotFoundError("找不到 Bridge 脚本: {}".format(bridge_script))

    # 启动 Xshell，通过 -script 参数加载 Bridge
    cmd = [xshell_exe, "-script", bridge_script]
    subprocess.Popen(cmd, cwd=str(Path(xshell_exe).parent))

    return True


def wait_for_bridge(client: BridgeClient, timeout: int = 20) -> bool:
    """等待 Bridge 就绪"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.check_bridge():
            return True
        time.sleep(0.5)
    return False


def get_bridge_guidance() -> str:
    """返回引导用户手动运行 bridge 脚本的提示文本"""
    return (
        "请在 XShell 中手动运行 Bridge 脚本：\n"
        "1. 打开 XShell，连接到目标主机\n"
        "2. 菜单 → 工具 → 脚本 → 运行\n"
        "3. 选择 xshell_bridge_v7.py 脚本\n"
        "4. 每个需要使用的 XShell 页签都需要运行一次\n"
        "5. 运行后，在 Claude Code 中调用 list_sessions() 查看可用会话\n"
        "6. 调用 connect_session() 绑定一个会话"
    )
