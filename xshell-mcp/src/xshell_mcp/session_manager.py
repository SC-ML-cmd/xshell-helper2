"""XShell 多会话管理器 — 管理 Bridge 注册、绑定、占用标记"""

import json
import ctypes
import logging
import os
import shutil
import time
from pathlib import Path

from .bridge_client import BridgeClient
from .exceptions import SessionNotFoundError, SessionOccupiedError

logger = logging.getLogger("xshell_mcp")

# 心跳超时阈值（秒）— 超过此时间未更新心跳则视为 bridge 可能已死
_HEARTBEAT_TIMEOUT = 600  # 10 分钟（Bridge 心跳间隔 60s，10min 足够覆盖深空闲模式）


class SessionManager:
    """管理 XShell session 的发现、注册、绑定"""

    def __init__(self, ipc_base: str, timeout: int = 30):
        self._ipc_base = ipc_base
        self._registry_dir = Path(ipc_base) / "registry"
        self._sessions_dir = Path(ipc_base) / "sessions"
        self._timeout = timeout
        # 确保目录存在
        self._registry_dir.mkdir(parents=True, exist_ok=True)
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

    def discover(self) -> list[dict]:
        """扫描 registry 目录，返回所有已注册且存活的 bridge 信息列表"""
        results = []
        if not self._registry_dir.exists():
            return results
        for reg_file in self._registry_dir.glob("session_*.json"):
            info = self._read_registry(reg_file)
            if info is None:
                continue
            session_id = info.get("session_id", "")
            if not session_id:
                continue
            # 检查 bridge 是否存活
            if not self._is_bridge_alive(session_id):
                logger.info("发现已死亡的 bridge（心跳过期或进程不存在）: %s，跳过", session_id)
                continue
            # 附加占用状态描述
            bound_by = info.get("bound_by", 0)
            if bound_by and self._is_process_alive(bound_by):
                info["status"] = "已占用"
            else:
                if bound_by:
                    # 幽灵占用，自动释放
                    self._release_binding(reg_file, info)
                info["status"] = "空闲"
            results.append(info)
        return results

    def list_available(self) -> list[dict]:
        """返回所有可绑定（未被占用且存活）的 session 列表"""
        return [s for s in self.discover() if s.get("status") == "空闲"]

    def is_available(self, session_id: str) -> bool:
        """检查指定 session 是否可绑定（注册文件存在 + 未被占用 + bridge 存活）"""
        reg_file = self._registry_dir / f"{session_id}.json"
        if not reg_file.exists():
            return False
        info = self._read_registry(reg_file)
        if info is None:
            return False
        if not self._is_bridge_alive(session_id):
            return False
        bound_by = info.get("bound_by", 0)
        if bound_by and self._is_process_alive(bound_by):
            return False
        return True

    def bind(self, session_id: str, mcp_pid: int) -> BridgeClient:
        """
        CAS 绑定指定 session：
        1. 读取注册文件，检查 bound_by == 0
        2. 写入 bound_by = mcp_pid
        3. 短暂等待
        4. 再次读取验证 bound_by == mcp_pid
        5. 验证失败则抛 SessionOccupiedError

        Returns: BridgeClient 实例
        """
        reg_file = self._registry_dir / f"{session_id}.json"
        if not reg_file.exists():
            raise SessionNotFoundError(f"会话 {session_id} 不存在")

        info = self._read_registry(reg_file)
        if info is None:
            raise SessionNotFoundError(f"无法读取会话 {session_id} 的注册信息")

        # 检查 bridge 是否存活
        if not self._is_bridge_alive(session_id):
            raise SessionNotFoundError(f"会话 {session_id} 的 bridge 已断开")

        # CAS Step 1: 检查是否空闲
        bound_by = info.get("bound_by", 0)
        if bound_by:
            if self._is_process_alive(bound_by):
                raise SessionOccupiedError(
                    f"会话 {session_id} 已被 PID {bound_by} 占用"
                )
            else:
                # 幽灵占用，先释放
                logger.info("检测到幽灵占用 session=%s bound_by=%d，自动释放",
                           session_id, bound_by)

        # CAS Step 2: 写入占用标记
        info["bound_by"] = mcp_pid
        info["bound_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._write_registry(reg_file, info)

        # CAS Step 3: 短暂等待
        time.sleep(0.05)

        # CAS Step 4: 再次读取验证
        verify = self._read_registry(reg_file)
        if verify is None or verify.get("bound_by") != mcp_pid:
            raise SessionOccupiedError(
                f"会话 {session_id} 绑定冲突，请重试"
            )

        # CAS Step 5: 创建 BridgeClient
        ipc_dir = info.get("ipc_dir", "")
        if not ipc_dir:
            ipc_dir = str(self._sessions_dir / session_id)

        client = BridgeClient(ipc_dir, timeout=self._timeout)
        client.initialize()

        logger.info("已绑定 session=%s ipc_dir=%s", session_id, ipc_dir)
        return client

    def unbind(self, session_id: str):
        """解除绑定：清除占用标记"""
        reg_file = self._registry_dir / f"{session_id}.json"
        if not reg_file.exists():
            logger.warning("解绑时注册文件不存在: %s", session_id)
            return
        info = self._read_registry(reg_file)
        if info is None:
            return
        info["bound_by"] = 0
        info["bound_at"] = ""
        self._write_registry(reg_file, info)
        logger.info("已解绑 session=%s", session_id)

    def check_stale_bindings(self):
        """清理幽灵占用和僵尸注册文件"""
        if not self._registry_dir.exists():
            return
        for reg_file in self._registry_dir.glob("session_*.json"):
            info = self._read_registry(reg_file)
            if info is None:
                continue
            session_id = info.get("session_id", "")
            bound_by = info.get("bound_by", 0)

            bridge_alive = self._is_bridge_alive(session_id) if session_id else False
            bounder_alive = self._is_process_alive(bound_by) if bound_by else False

            if not bridge_alive:
                # bridge 进程已死（心跳过期），清理僵尸注册文件
                reg_file.unlink(missing_ok=True)
                logger.warning("清理僵尸注册文件: %s（心跳已过期，session_id=%s）",
                             reg_file.name, session_id)
                continue

            if bound_by and not bounder_alive:
                # 仅 bound_by 幽灵占用，释放即可；bridge 本身还活着
                self._release_binding(reg_file, info)

    def cleanup_stale_session_dirs(self):
        """清理已退出 bridge 遗留的 session 目录

        遍历 ipc/sessions/ 下所有 session_* 目录，
        若对应 registry 中没有注册文件，则该 session 已退出，删除目录。
        """
        if not self._sessions_dir.exists():
            return
        active_sessions = set()
        if self._registry_dir.exists():
            for reg_file in self._registry_dir.glob("session_*.json"):
                active_sessions.add(reg_file.stem)  # 去掉 .json
        for session_dir in self._sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue
            if not session_dir.name.startswith("session_"):
                continue
            if session_dir.name not in active_sessions:
                _rmtree(session_dir)
                logger.warning("清理已退出 session 目录: %s", session_dir)

    def get_session_info(self, session_id: str) -> dict | None:
        """读取指定 session 的注册信息"""
        reg_file = self._registry_dir / f"{session_id}.json"
        if not reg_file.exists():
            return None
        return self._read_registry(reg_file)

    def _is_bridge_alive(self, session_id: str) -> bool:
        """检查 bridge 是否存活：注册文件存在 + heartbeat 未过期"""
        reg_file = self._registry_dir / f"{session_id}.json"
        if not reg_file.exists():
            return False
        info = self._read_registry(reg_file)
        if info is None:
            return False
        # 检查心跳是否过期
        last_hb = info.get("last_heartbeat", "")
        if last_hb:
            try:
                hb_time = time.mktime(time.strptime(last_hb, "%Y-%m-%dT%H:%M:%S"))
                if time.time() - hb_time > _HEARTBEAT_TIMEOUT:
                    logger.debug("bridge 心跳过期: %s last_hb=%s", session_id, last_hb)
                    return False
            except (ValueError, OverflowError):
                pass  # 解析失败则忽略心跳检查
        return True

    @staticmethod
    def _is_process_alive(pid: int) -> bool:
        """检查指定 PID 的进程是否还在运行（Windows 原生 API）"""
        if pid <= 0:
            return False
        try:
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False

    def _release_binding(self, reg_file: Path, info: dict):
        """释放一个幽灵占用"""
        old_pid = info.get("bound_by", 0)
        info["bound_by"] = 0
        info["bound_at"] = ""
        self._write_registry(reg_file, info)
        logger.info("已释放幽灵占用: session=%s old_pid=%d",
                    info.get("session_id", "?"), old_pid)

    @staticmethod
    def _read_registry(reg_file: Path) -> dict | None:
        """读取注册文件，返回 dict 或 None"""
        try:
            with open(reg_file, "r", encoding="utf-8") as f:
                return json.loads(f.read())
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("读取注册文件失败 %s: %s", reg_file, e)
            return None

    @staticmethod
    def _write_registry(reg_file: Path, data: dict):
        """原子写注册文件（写 .tmp 再 rename）"""
        tmp = str(reg_file) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(reg_file))


def _rmtree(path: Path):
    """删除目录树，忽略文件不存在等错误"""
    try:
        shutil.rmtree(str(path))
    except OSError as e:
        logger.warning("删除目录失败 %s: %s", path, e)
