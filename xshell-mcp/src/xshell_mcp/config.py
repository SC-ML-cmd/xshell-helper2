import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class XshellConfig:
    xshell_path: str = r"D:\software\xshell8\Xshell.exe"
    bridge_script_path: str = ""
    ipc_dir: str = ""
    ipc_base: str = ""    # 多会话 IPC 根目录（注册和 sessions 的父目录）
    default_timeout: int = 30
    auto_bind_timeout: int = 120       # 启动时自动绑定的最长等待秒数
    auto_bind_poll_interval: int = 3   # 轮询空闲会话的间隔秒数
    screen_cols: int = 200
    marker_prefix: str = "__XSH_"
    log_dir: str = ""
    log_level: str = "INFO"
    log_mask_sensitive: bool = False

    def __post_init__(self):
        if not self.bridge_script_path:
            pkg_dir = Path(__file__).resolve().parent.parent.parent
            self.bridge_script_path = str(pkg_dir / "bridge" / "xshell_bridge_v7.py")
        if not self.ipc_dir:
            pkg_dir = Path(__file__).resolve().parent.parent.parent
            self.ipc_dir = str(pkg_dir / "ipc")
        if not self.ipc_base:
            pkg_dir = Path(__file__).resolve().parent.parent.parent
            self.ipc_base = str(pkg_dir / "ipc")
        if not self.log_dir:
            pkg_dir = Path(__file__).resolve().parent.parent.parent
            self.log_dir = str(pkg_dir / "logs")


@dataclass
class LogConfig:
    log_dir: str = "/logs"
    file_pattern: str = "*.log*"
    compressed_extensions: list = field(default_factory=lambda: [".gz"])
    log_format: str = "logback"
    timestamp_format: str = "yyyy-MM-dd HH:mm:ss.SSS"
    file_naming: str = ""
    max_extract_lines: int = 10000
    description: str = ""


def load_log_config() -> LogConfig | None:
    """加载项目日志配置文件。

    查找顺序：
    1. 环境变量 XSH_LOG_CONFIG 指定的路径
    2. 当前工作目录下的 .xshell-log.json

    Returns:
        LogConfig 实例，如未找到配置文件则返回 None
    """
    config_path = os.getenv("XSH_LOG_CONFIG", "")
    if not config_path:
        config_path = Path.cwd() / ".xshell-log.json"
    else:
        config_path = Path(config_path)

    if not config_path.exists():
        return None

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return LogConfig(
            log_dir=data.get("log_dir", "/logs"),
            file_pattern=data.get("file_pattern", "*.log*"),
            compressed_extensions=data.get("compressed_extensions", [".gz"]),
            log_format=data.get("log_format", "logback"),
            timestamp_format=data.get("timestamp_format", "yyyy-MM-dd HH:mm:ss.SSS"),
            file_naming=data.get("file_naming", ""),
            max_extract_lines=data.get("max_extract_lines", 10000),
            description=data.get("description", ""),
        )
    except (json.JSONDecodeError, IOError):
        return None


def load_config() -> XshellConfig:
    cfg = XshellConfig()
    if v := os.getenv("XSH_XSHELL_PATH"):
        cfg.xshell_path = v
    if v := os.getenv("XSH_BRIDGE_SCRIPT"):
        cfg.bridge_script_path = v
    if v := os.getenv("XSH_IPC_DIR"):
        cfg.ipc_dir = v
    if v := os.getenv("XSH_IPC_BASE"):
        cfg.ipc_base = v
    if v := os.getenv("XSH_DEFAULT_TIMEOUT"):
        cfg.default_timeout = int(v)
    if v := os.getenv("XSH_AUTO_BIND_TIMEOUT"):
        cfg.auto_bind_timeout = int(v)
    if v := os.getenv("XSH_AUTO_BIND_POLL_INTERVAL"):
        cfg.auto_bind_poll_interval = int(v)
    if v := os.getenv("XSH_SCREEN_COLS"):
        cfg.screen_cols = int(v)
    if v := os.getenv("XSH_LOG_DIR"):
        cfg.log_dir = v
    if v := os.getenv("XSH_LOG_LEVEL"):
        cfg.log_level = v
    if v := os.getenv("XSH_LOG_MASK_SENSITIVE"):
        cfg.log_mask_sensitive = v.lower() in ("1", "true", "yes")
    return cfg
