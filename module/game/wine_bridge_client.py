"""
Wine 输入桥协议客户端

协议由 BetterGIWineInputBridge.exe 逆向得出（REA + Ghidra 12.1.4）：
  报文头 24 字节（小端）：
    0x00  int     magic   = 0x31494742 （ASCII "BGI1"）
    0x04  short   flag    = 必须 5
    0x06  ushort  command = 命令字
    0x08  uint    未知字段（请求侧取源结构 +0x08，响应侧回显）
    0x0C  uint    payload 长度（上限 0x10000）
    0x10  uint    请求侧取 r8d；响应侧为 status
    0x14  int     reserved = 必须 0
  认证（命令 2）：
    payload 与 token 补齐后逐字节 XOR 再 OR 归约，全 0 即通过（等价于 payload == token）
"""

import socket
import struct
import threading
import time
from enum import IntEnum

from module.logger import log


MAGIC = 0x31494742          # ASCII "BGI1"
FLAG = 5
HEADER_SIZE = 0x18
MAX_PAYLOAD = 0x10000


class Command(IntEnum):
    """已还原的命令字（逆向自 switch 分支）"""

    PROBE = 1          # 版本探测，返回 16 字节：{5, 0x1ff, "WINE", "BGI"}
    AUTH = 2           # Token 认证
    INPUT = 3          # 构造 INPUT 结构（载荷 1..62 字节）
    INPUT_BIG = 4      # 大结构输入（载荷 0x50 = 80 字节）
    NOOP = 5           # 空响应
    FOREGROUND_DIAG = 6    # 前台诊断（载荷 4）
    SET_FOREGROUND = 7     # SetForegroundWindow（载荷 4）
    FOREGROUND_STATE = 8   # 前台状态（载荷 0）
    MOUSE_EVENT = 9        # 鼠标事件（载荷 0）
    KEY_EVENT = 10         # 键盘事件（载荷 8）
    KEY_EVENT2 = 11
    KEY_WITH_MOD = 12      # 键盘事件含修饰键（载荷 8）
    BUTTON_1 = 0x14
    BUTTON_2 = 0x15
    BUTTON_3 = 0x16
    MOUSE_ABS = 0x17       # 鼠标绝对移动（载荷 8：x, y 各 4 字节）
    MOUSE_ABS_ALT = 0x18
    MOUSE_EVENT2 = 0x19    # 鼠标事件（载荷 4）
    BATCH_INPUT = 0x1E     # 批量输入（偶数长度）
    KEY_STATE = 0x28       # GetAsyncKeyState（载荷 4）
    KEY_STATE_VK = 0x29    # 按虚拟键码查状态（载荷 4）


class Status(IntEnum):
    OK = 0
    BAD_HEADER = 1     # 魔数 / 长度 / reserved 非法
    BAD_FLAG = 2       # flag != 5
    AUTH_FAIL = 5
    ALREADY_AUTH = 6


class WineBridgeClient:
    """
    Wine 输入桥 TCP 客户端

    桥运行在 Wine 环境内（Windows 程序），监听 127.0.0.1:port。
    macOS 侧通过本客户端下发输入指令，由桥在 Wine 桌面内执行 SendInput。
    """

    def __init__(self, host: str = '127.0.0.1', port: int = 17400,
                 token: str = '', timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.token = token
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._lock = threading.RLock()
        self._authenticated = False
        self._connecting = False
        self.bridge_version = 0
        self.bridge_caps = 0

    # ---------- 连接 ----------

    @property
    def is_connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> bool:
        """连接桥并完成握手 + 认证"""
        with self._lock:
            if self._sock is not None:
                return True
            self._connecting = True
            try:
                sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self._sock = sock

                # 探测版本（此时 _sock 已赋值，_request 可正常工作）
                payload = self._request(Command.PROBE, b'')
                if payload and len(payload) >= 16:
                    self.bridge_version, self.bridge_caps = struct.unpack('<II', payload[:8])
                    log.info(f'Wine 桥已连接 {self.host}:{self.port} '
                             f'版本 {self.bridge_version} 能力 0x{self.bridge_caps:x}')
                else:
                    log.info(f'Wine 桥已连接 {self.host}:{self.port}（无版本信息）')

                # Token 认证
                if self.token:
                    if not self._authenticate():
                        log.error('Wine 桥认证失败')
                        self.close()
                        return False
                else:
                    log.warning('未配置 token，跳过认证（桥可能拒绝后续命令）')

                return True
            except OSError as e:
                log.debug(f'Wine 桥连接失败 {self.host}:{self.port} {e}')
                self._close_unlocked()
                return False
            finally:
                self._connecting = False

    def _authenticate(self) -> bool:
        payload = self._request(Command.AUTH, self.token.encode('utf-8'))
        # 认证成功时桥返回空载荷；失败返回 status=5
        if payload is None:
            return False
        self._authenticated = True
        log.debug('Wine 桥认证通过')
        return True

    def close(self) -> None:
        with self._lock:
            self._close_unlocked()

    def _close_unlocked(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._authenticated = False

    # ---------- 报文收发 ----------

    @staticmethod
    def build_header(command: int, payload_len: int, field08: int = 0, field10: int = 0) -> bytes:
        """构造 24 字节报文头（布局由机器码确证）"""
        return struct.pack(
            '<iHHIiI',
            MAGIC,          # 0x00  int
            FLAG,           # 0x04  short
            command,        # 0x06  ushort
            field08,        # 0x08  uint
            payload_len,    # 0x0C  uint
            field10,        # 0x10  uint
        ) + struct.pack('<I', 0)   # 0x14  reserved = 0

    def _recv_exact(self, n: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = self._sock.recv(n - len(buf))
            except OSError as e:
                log.debug(f'Wine 桥接收失败：{e}')
                return None
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _request(self, command: int, payload: bytes = b'') -> bytes | None:
        """
        发送命令并读取响应

        Returns:
            响应载荷；失败返回 None
        """
        with self._lock:
            # 未连接时自动连接；_connecting 防止 connect 内部调用本方法造成递归
            if self._sock is None:
                if self._connecting:
                    return None
                if not self.connect():
                    return None

            if len(payload) > MAX_PAYLOAD:
                log.error(f'载荷过大：{len(payload)} > {MAX_PAYLOAD}')
                return None

            header = self.build_header(int(command), len(payload))
            try:
                self._sock.sendall(header)
                if payload:
                    self._sock.sendall(payload)
            except OSError as e:
                log.debug(f'Wine 桥发送失败：{e}')
                self._close_unlocked()
                return None

            # 读响应头
            resp = self._recv_exact(HEADER_SIZE)
            if resp is None:
                log.debug('Wine 桥响应头读取失败')
                self._close_unlocked()
                return None

            magic, flag, rcmd, f08, plen, status = struct.unpack('<iHHIiI', resp[:20])
            if magic != MAGIC:
                log.error(f'Wine 桥响应魔数错误 0x{magic:x}')
                self._close_unlocked()
                return None

            # 读响应载荷
            body = b''
            if plen:
                body = self._recv_exact(plen) or b''

            if status != Status.OK:
                log.debug(f'Wine 桥命令 {int(command)} 返回状态 {status}'
                          f'（{Status(status).name if status in Status._value2member_map_ else "未知"}）')
                if status in (Status.AUTH_FAIL, Status.BAD_HEADER, Status.BAD_FLAG):
                    return None

            return body

    # ---------- 具体命令 ----------

    def set_foreground(self, hwnd_or_pid: int, title: str = '') -> bool:
        """命令 7：把目标窗口切到前台

        :param hwnd_or_pid: 窗口句柄或进程 PID
        :param title: 可选窗口标题，桥会优先按标题精确匹配并 SetFocus
        """
        payload = struct.pack('<I', hwnd_or_pid)
        if title:
            payload += title.encode('utf-8')
        return self._request(Command.SET_FOREGROUND, payload) is not None

    def move_mouse(self, x: int, y: int) -> bool:
        """命令 0x17：鼠标绝对移动（载荷 8 字节：x, y 各 4 字节）"""
        return self._request(Command.MOUSE_ABS, struct.pack('<ii', x, y)) is not None

    def raw_command(self, command: int, payload: bytes = b'') -> bytes | None:
        """下发原始命令（用于继续逆向未展开的分支）"""
        return self._request(command, payload)

    # ---------- 生命周期 ----------

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
