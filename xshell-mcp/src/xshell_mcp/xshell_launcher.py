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
