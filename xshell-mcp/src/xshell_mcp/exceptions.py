class XshellMCPError(Exception):
    """MCP Server 基础异常"""
    pass


class BridgeNotReadyError(XshellMCPError):
    """Bridge 未启动或未连接"""
    pass


class BridgeTimeoutError(XshellMCPError):
    """Bridge 命令执行超时"""
    pass


class BridgeConnectionError(XshellMCPError):
    """与 Bridge 通信失败"""
    pass


class NoActiveSessionError(XshellMCPError):
    """Xshell 中没有活跃的终端会话"""
    pass


class SessionNotFoundError(XshellMCPError):
    """指定的 XShell 会话不存在"""
    pass


class SessionOccupiedError(XshellMCPError):
    """指定的 XShell 会话已被其他窗口占用"""
    pass


class SessionNotBoundError(XshellMCPError):
    """当前 MCP Server 尚未绑定 XShell 会话"""
    pass
