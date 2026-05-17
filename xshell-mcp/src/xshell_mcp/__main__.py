"""Xshell MCP Server 入口"""

import logging
import sys


def main():
    from .config import load_config

    cfg = load_config()

    from .log_config import setup_logging

    setup_logging(cfg.log_dir, cfg.log_level)

    logger = logging.getLogger("xshell_mcp")
    logger.info("Xshell MCP Server 启动中...")

    from .server import _init_session_manager, mcp

    try:
        _init_session_manager()
    except Exception as e:
        logger.warning("Session Manager 初始化失败: %s", e)
        logger.warning("将继续启动 MCP Server，请在 XShell 中运行 xshell_bridge_v7.py 脚本")

    logger.info("MCP Server 就绪，等待通过 connect_session() 绑定 XShell 会话")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
