"""Xshell MCP Server 入口"""
import sys
import traceback


def main():
    from .config import load_config

    cfg = load_config()

    from .log_config import setup_logging

    setup_logging(cfg.log_dir, cfg.log_level)

    import logging
    logger = logging.getLogger("xshell_mcp")
    logger.info("Xshell MCP Server 启动中...")
    logger.debug("log_dir=%s ipc_base=%s ipc_dir=%s",
                 cfg.log_dir, cfg.ipc_base, cfg.ipc_dir)

    from .server import _init_session_manager, start_auto_bind, mcp

    try:
        _init_session_manager()
    except Exception as e:
        logger.warning("Session Manager 初始化失败: %s", e, exc_info=True)

    start_auto_bind()

    logger.info("MCP Server 就绪，等待通过 connect_session() 绑定 XShell 会话")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
