"""Xshell MCP Server 入口"""

import logging
import sys


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [xshell-mcp] %(message)s",
    )
    logger = logging.getLogger("xshell_mcp")

    logger.info("Xshell MCP Server 启动中...")

    from .server import init_bridge, mcp

    try:
        init_bridge()
    except Exception as e:
        logger.warning("Bridge 初始化失败: %s", e)
        logger.warning("将继续启动 MCP Server，但命令执行需要 Bridge 在线")
        logger.warning("请手动在 Xshell 中运行 bridge/xshell_bridge.py 脚本")

    logger.info("MCP Server 就绪")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
