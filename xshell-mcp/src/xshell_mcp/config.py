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


def _split_csv_env(value: str | None, default: list[str]) -> list[str]:
    """将逗号分隔环境变量解析为字符串列表。"""
    if not value:
        return default
    items = [item.strip() for item in value.split(",")]
    filtered = [item for item in items if item]
    return filtered or default


def load_log_config() -> LogConfig:
    """加载日志检索配置（项目级配置，来自 MCP 参数/环境变量）。

    说明：
    - 不再依赖 .xshell-log.json；
    - 由 config.py 默认值 + 环境变量覆盖组成；
    - 一个项目只需配置一次，search_logs 调用时直接复用。
    """
    cfg = LogConfig()

    if v := os.getenv("XSH_SEARCH_LOG_DIR"):
        cfg.log_dir = v
    if v := os.getenv("XSH_SEARCH_FILE_PATTERN"):
        cfg.file_pattern = v
    if v := os.getenv("XSH_SEARCH_COMPRESSED_EXTENSIONS"):
        cfg.compressed_extensions = _split_csv_env(v, [".gz"])
    if v := os.getenv("XSH_SEARCH_LOG_FORMAT"):
        cfg.log_format = v
    if v := os.getenv("XSH_SEARCH_TIMESTAMP_FORMAT"):
        cfg.timestamp_format = v
    if v := os.getenv("XSH_SEARCH_FILE_NAMING"):
        cfg.file_naming = v
    if v := os.getenv("XSH_SEARCH_MAX_EXTRACT_LINES"):
        cfg.max_extract_lines = int(v)
    if v := os.getenv("XSH_SEARCH_LOG_DESCRIPTION"):
        cfg.description = v

    return cfg


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
