"""
M7A Wine 输入桥（在 Wine 环境内运行）

协议与 BetterGIWineInputBridge.exe 对齐（已逆向确证）：
  24 字节头：magic "BGI1" | flag=5 | command | field08 | payload_len | field10 | reserved=0

与 BGI 桥的差异：
  - 用 Python + ctypes 实现，无需 mingw 编译
  - 输入签名改为 "M7AWINE"（BGI 用 "BGIWINE"，互不干扰）
  - 只实现 M7A 实际需要的命令：1 探测 / 2 认证 / 7 切前台 / 0x17 鼠标移动 / 3 键鼠 INPUT

启动（在 Wine 的 Windows Python 下）：
  python m7a_wine_bridge.py --port 17400 --token <32-255字节>

首次使用需在 Yaagl 的 wineprefix 里安装 Windows 版 Python 3.x。
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import socket
import struct
import sys
import time

MAGIC = 0x31494742
FLAG = 5
HEADER_SIZE = 0x18
MAX_PAYLOAD = 0x10000
INPUT_SIGNATURE = 0x4D374157494E45   # ASCII "M7AWINE"

# ---------- Win32 定义 ----------

user32 = ctypes.WinDLL('user32', use_last_error=True)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000


ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', wt.LONG),
        ('dy', wt.LONG),
        ('mouseData', wt.DWORD),
        ('dwFlags', wt.DWORD),
        ('time', wt.DWORD),
        ('dwExtraInfo', ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', wt.WORD),
        ('wScan', wt.WORD),
        ('dwFlags', wt.DWORD),
        ('time', wt.DWORD),
        ('dwExtraInfo', ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ('uMsg', wt.DWORD),
        ('wParamL', wt.WORD),
        ('wParamH', wt.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [('type', wt.DWORD), ('u', _INPUT_UNION)]


MapVirtualKeyW = user32.MapVirtualKeyW
MapVirtualKeyW.argtypes = (wt.UINT, wt.UINT)
MapVirtualKeyW.restype = wt.UINT

MAPVK_VK_TO_VSC = 0

ClientToScreen = user32.ClientToScreen
ClientToScreen.argtypes = (wt.HWND, ctypes.POINTER(wt.POINT))
ClientToScreen.restype = wt.BOOL

GetClientRect = user32.GetClientRect
GetClientRect.argtypes = (wt.HWND, ctypes.POINTER(wt.RECT))
GetClientRect.restype = wt.BOOL

GetSystemMetrics = user32.GetSystemMetrics
GetSystemMetrics.argtypes = (ctypes.c_int,)
GetSystemMetrics.restype = ctypes.c_int

SM_CXSCREEN = 0
SM_CYSCREEN = 1

SendInput = user32.SendInput
SendInput.argtypes = (wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
SendInput.restype = wt.UINT

SetForegroundWindow = user32.SetForegroundWindow
SetForegroundWindow.argtypes = (wt.HWND,)
SetForegroundWindow.restype = wt.BOOL

GetForegroundWindow = user32.GetForegroundWindow
GetForegroundWindow.restype = wt.HWND

EnumWindows = user32.EnumWindows
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
GetWindowThreadProcessId = user32.GetWindowThreadProcessId
IsWindowVisible = user32.IsWindowVisible


# ---------- 输入执行 ----------

def _send_inputs(inputs):
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    sent = SendInput(n, arr, ctypes.sizeof(INPUT))
    return sent == n


def _key_input(vk=0, keyup=False, scan=0, unicode_char=None):
    """
    构造键盘 INPUT 事件

    三种模式：
      · vk + scan：标准虚拟键码（游戏按键用这个）
      · unicode_char：KEYEVENTF_UNICODE 直接发字符（文本输入用这个，绕开键盘布局）
    """
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    if unicode_char is not None:
        inp.u.ki.wVk = 0
        inp.u.ki.wScan = ord(unicode_char)
        inp.u.ki.dwFlags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if keyup else 0)
    else:
        inp.u.ki.wVk = vk
        inp.u.ki.wScan = scan
        inp.u.ki.dwFlags = KEYEVENTF_KEYUP if keyup else 0
    inp.u.ki.dwExtraInfo = INPUT_SIGNATURE
    return inp


def _mouse_input(dx, dy, flags, data=0):
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.u.mi.dx = dx
    inp.u.mi.dy = dy
    inp.u.mi.mouseData = data
    inp.u.mi.dwFlags = flags
    inp.u.mi.dwExtraInfo = INPUT_SIGNATURE
    return inp


def press_key(vk, hold_ms=80):
    """
    按下并释放一个键

    用 vk + 扫描码双填：Wine 下仅发 vk 偶发丢失，补扫描码后识别率显著提高。
    按下与抬起分两次 SendInput，中间留出 hold_ms 给游戏轮询。
    """
    scan = MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    down = _key_input(vk=vk, keyup=False, scan=scan)
    up = _key_input(vk=vk, keyup=True, scan=scan)
    if not _send_inputs([down]):
        return False
    time.sleep(max(hold_ms, 30) / 1000.0)
    if not _send_inputs([up]):
        return False
    time.sleep(0.02)
    return True


def type_text(text, interval_ms=60):
    """
    直接输入文本（KEYEVENTF_UNICODE，绕开键盘布局与大小写问题）
    适合输入框场景；游戏内按键仍用 press_key
    """
    for ch in text:
        down = _key_input(unicode_char=ch, keyup=False)
        up = _key_input(unicode_char=ch, keyup=True)
        if not _send_inputs([down]):
            return False
        time.sleep(0.01)
        if not _send_inputs([up]):
            return False
        time.sleep(max(interval_ms, 20) / 1000.0)
    return True


def client_to_screen(client_x, client_y, hwnd=None):
    """客户区坐标 → 屏幕坐标

    :param hwnd: 目标窗口；为 0/None 时回退到前台窗口
    """
    if not hwnd:
        hwnd = GetForegroundWindow()
    if not hwnd:
        return client_x, client_y
    pt = wt.POINT(int(client_x), int(client_y))
    if ClientToScreen(hwnd, ctypes.byref(pt)):
        return pt.x, pt.y
    return client_x, client_y


def ensure_focus(hwnd):
    """确保目标窗口在前台且有焦点（Wine 下失焦会吞掉首次输入）"""
    if not hwnd:
        return
    try:
        if GetForegroundWindow() != hwnd:
            ShowWindow(hwnd, SW_RESTORE)
            BringWindowToTop(hwnd)
            SetForegroundWindow(hwnd)
            SetFocus(hwnd)
            time.sleep(0.05)
    except Exception:
        pass


def mouse_click_abs(x, y, screen_w=1920, screen_h=1080, hold_ms=50, hwnd=None):
    """
    点击客户区坐标 (x, y)

    先把客户区坐标经 ClientToScreen 转成屏幕绝对坐标，再按真实桌面尺寸归一化，
    避免窗口偏移/标题栏导致落点漂移。
    """
    ensure_focus(hwnd)
    sx, sy = client_to_screen(x, y, hwnd)
    dw = GetSystemMetrics(SM_CXSCREEN) or screen_w
    dh = GetSystemMetrics(SM_CYSCREEN) or screen_h
    nx = int(sx * 65535 / dw)
    ny = int(sy * 65535 / dh)
    move = _mouse_input(nx, ny, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE)
    down = _mouse_input(nx, ny, MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_ABSOLUTE)
    up = _mouse_input(nx, ny, MOUSEEVENTF_LEFTUP | MOUSEEVENTF_ABSOLUTE)
    if not _send_inputs([move]):
        return False
    time.sleep(0.02)
    if not _send_inputs([down]):
        return False
    time.sleep(hold_ms / 1000.0)
    return _send_inputs([up])


def scroll_abs(x, y, delta, screen_w=1920, screen_h=1080, hwnd=None):
    ensure_focus(hwnd)
    sx, sy = client_to_screen(x, y, hwnd)
    dw = GetSystemMetrics(SM_CXSCREEN) or screen_w
    dh = GetSystemMetrics(SM_CYSCREEN) or screen_h
    nx = int(sx * 65535 / dw)
    ny = int(sy * 65535 / dh)
    move = _mouse_input(nx, ny, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE)
    wheel = _mouse_input(nx, ny, MOUSEEVENTF_WHEEL | MOUSEEVENTF_ABSOLUTE, delta)
    return _send_inputs([move]) and _send_inputs([wheel])


# ---------- 窗口定位 ----------

FindWindowW = user32.FindWindowW
FindWindowW.argtypes = (wt.LPCWSTR, wt.LPCWSTR)
FindWindowW.restype = wt.HWND

SetFocus = user32.SetFocus
SetFocus.argtypes = (wt.HWND,)
SetFocus.restype = wt.HWND

BringWindowToTop = user32.BringWindowToTop
BringWindowToTop.argtypes = (wt.HWND,)
BringWindowToTop.restype = wt.BOOL

ShowWindow = user32.ShowWindow
ShowWindow.argtypes = (wt.HWND, ctypes.c_int)
ShowWindow.restype = wt.BOOL

SW_RESTORE = 9


def find_window_by_title(title):
    """按窗口标题查找（Wine 内）"""
    return FindWindowW(None, title)



def find_windows_by_process(pid):
    """枚举属于指定 PID 的可见顶层窗口"""
    result = []

    def cb(hwnd, lparam):
        wpid = wt.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid and IsWindowVisible(hwnd):
            result.append(hwnd)
        return True

    EnumWindows(WNDENUMPROC(cb), 0)
    return result


# ---------- 服务端 ----------

class BridgeServer:
    def __init__(self, port, token, screen_w=1920, screen_h=1080):
        self.port = port
        self.token = token.encode('utf-8') if token else b''
        self.screen_w = screen_w
        self.screen_h = screen_h
        self.authenticated = False
        self.target_pid = 0
        self.target_hwnd = 0

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('127.0.0.1', self.port))
        srv.listen(1)
        sys.stderr.write('M7A_WINE_BRIDGE_READY\n')
        sys.stderr.flush()

        while True:
            conn, _ = srv.accept()
            try:
                self.serve_conn(conn)
            except Exception as e:
                sys.stderr.write(f'connection error: {e}\n')
                sys.stderr.flush()
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def serve_conn(self, conn):
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        while True:
            header = self._recv_exact(conn, HEADER_SIZE)
            if header is None:
                return
            magic, flag, command, f08, plen, f10 = struct.unpack('<iHHIiI', header[:20])
            reserved = struct.unpack('<I', header[20:24])[0]

            if magic != MAGIC or plen > MAX_PAYLOAD or reserved != 0:
                self._send_response(conn, command, 1, b'')
                return
            if flag != FLAG:
                self._send_response(conn, command, 2, b'')
                return

            payload = b''
            if plen:
                payload = self._recv_exact(conn, plen) or b''

            status, body = self.handle(command, payload)
            self._send_response(conn, command, status, body)
            sys.stderr.write(f'command={command} status={status} '
                             f'targetPID={self.target_pid} hwnd=0x{self.target_hwnd:x}\n')
            sys.stderr.flush()

    def handle(self, command, payload):
        if command == 1:   # PROBE
            body = struct.pack('<II4s4s', 5, 0x1FF, b'WINE', b'M7A ')
            return 0, body

        if command == 2:   # AUTH
            if not self.token:
                self.authenticated = True
                return 0, b''
            if payload == self.token:
                self.authenticated = True
                sys.stderr.write('client authenticated\n')
                sys.stderr.flush()
                return 0, b''
            return 5, b''

        if not self.authenticated and self.token:
            return 5, b''

        if command == 7:   # SET_FOREGROUND
            if not (4 <= len(payload) <= 260):
                return 1, b''
            target = struct.unpack('<I', payload[:4])[0]
            self.target_pid = target
            handles = []
            if len(payload) > 4:
                title = payload[4:].decode('utf-8', errors='ignore')
                hwnd = find_window_by_title(title)
                if hwnd:
                    handles = [hwnd]
                    sys.stderr.write('target window by title %r hwnd=0x%x\n' % (title, hwnd))
            if not handles and target > 0:
                handles = find_windows_by_process(target)
            if handles:
                self.target_hwnd = handles[0]
                wpid = wt.DWORD()
                GetWindowThreadProcessId(handles[0], ctypes.byref(wpid))
                if wpid.value:
                    self.target_pid = wpid.value
                ShowWindow(handles[0], SW_RESTORE)
                BringWindowToTop(handles[0])
                SetForegroundWindow(handles[0])
                SetFocus(handles[0])
                sys.stderr.write('foreground set hwnd=0x%x\n' % handles[0])
            else:
                sys.stderr.write('no target window found\n')
            sys.stderr.flush()
            return 0, b''

        if command == 0x17:  # MOUSE_ABS
            if len(payload) != 8:
                return 1, b''
            x, y = struct.unpack('<ii', payload)
            sx, sy = client_to_screen(x, y)
            dw = GetSystemMetrics(SM_CXSCREEN) or self.screen_w
            dh = GetSystemMetrics(SM_CYSCREEN) or self.screen_h
            nx = int(sx * 65535 / dw)
            ny = int(sy * 65535 / dh)
            ok = _send_inputs([_mouse_input(nx, ny,
                                            MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE)])
            return (0 if ok else 3), b''

        if command == 3:   # INPUT
            #   kind=1 键盘: [1][u16 vk][u16 hold]
            #   kind=2 点击: [1][u16 x][u16 y][u16 hold]
            #   kind=3 滚轮: [1][u16 x][u16 y][i16 delta]
            #   kind=4 文本: [1][utf-8 bytes...]
            if not (1 <= len(payload) <= 4096):
                return 1, b''
            ok = self._do_input(payload)
            if not ok:
                sys.stderr.write('input exec failed kind=%d len=%d\n' % (payload[0], len(payload)))
                sys.stderr.flush()
            return (0 if ok else 3), b''

        # 未实现分支：返回成功但不做事，便于客户端探测
        sys.stderr.write(f'command {command} not implemented\n')
        sys.stderr.flush()
        return 0, b''

    def _do_input(self, payload):
        kind = payload[0]
        hwnd = self.target_hwnd or None

        if kind == 1:   # 键盘（必须有焦点）
            if len(payload) < 3:
                return False
            vk = struct.unpack('<H', payload[1:3])[0]
            hold = struct.unpack('<H', payload[3:5])[0] if len(payload) >= 5 else 50
            ensure_focus(hwnd)
            return press_key(vk, hold)

        if kind == 2:   # 鼠标点击（客户区坐标）
            if len(payload) < 5:
                return False
            x, y = struct.unpack('<HH', payload[1:5])
            hold = struct.unpack('<H', payload[5:7])[0] if len(payload) >= 7 else 50
            return mouse_click_abs(x, y, self.screen_w, self.screen_h, hold, hwnd)

        if kind == 3:   # 滚轮
            if len(payload) < 5:
                return False
            x, y = struct.unpack('<HH', payload[1:5])
            delta = struct.unpack('<h', payload[5:7])[0] if len(payload) >= 7 else -120
            return scroll_abs(x, y, delta, self.screen_w, self.screen_h, hwnd)

        if kind == 4:   # 文本输入（KEYEVENTF_UNICODE）
            ensure_focus(hwnd)
            text = payload[1:].decode('utf-8', errors='ignore')
            return type_text(text)

        return False

    @staticmethod
    def _recv_exact(conn, n):
        buf = bytearray()
        while len(buf) < n:
            try:
                chunk = conn.recv(n - len(buf))
            except OSError:
                return None
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)

    @staticmethod
    def _send_response(conn, command, status, payload):
        header = struct.pack('<iHHIiI', MAGIC, FLAG, command, 0,
                             len(payload), status) + struct.pack('<I', 0)
        try:
            conn.sendall(header)
            if payload:
                conn.sendall(payload)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--port', type=int, default=17400)
    ap.add_argument('--token', type=str, default='')
    ap.add_argument('--width', type=int, default=1920)
    ap.add_argument('--height', type=int, default=1080)
    args = ap.parse_args()

    if args.token and not (32 <= len(args.token) <= 255):
        sys.stderr.write('BETTERGI_WINE_BRIDGE_TOKEN must contain 32-255 bytes\n')
        return 1

    BridgeServer(args.port, args.token, args.width, args.height).run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
