"""
Yaagl（Wine 跑 Windows 版崩铁）游戏控制器

架构（与用户已验证的 betterGI-mac 同构）：
  ┌ macOS 原生层：本控制器
  │   · 截图：ScreenCaptureKit / CGWindowListCreateImage（DXMT 渲染窗口必须走原生）
  │   · 视觉识别：M7A 既有能力（OpenCV + RapidOCR）
  └─────── TCP 127.0.0.1:<port> ────────
  ┌ Wine 层：m7a_wine_bridge.py（SendInput 注入 Wine 桌面）
  └ Yaagl 的 Wine 环境：Windows 版崩铁（DXMT 渲染）
"""

import os
import subprocess
import threading
import time
from typing import Optional

import cv2
import numpy as np
import psutil
from PIL import Image

from module.config.config import Config
from module.game.base import GameControllerBase
from module.game.wine_bridge_client import WineBridgeClient
from utils.logger.logger import Logger

STANDARD_WIDTH = 1920
STANDARD_HEIGHT = 1080

# Yaagl HSR 的默认位置
YAAGL_HSR_APP = "/Applications/Yaagl HSR.app"
YAAGL_SUPPORT_DIR = os.path.expanduser("~/Library/Application Support/Yaagl HSR")
DEFAULT_GAME_DIR = os.path.expanduser("~/Games/StarRail")


class YaaglGameController(GameControllerBase):
    """
    macOS 上通过 Yaagl（Wine）运行的 Windows 版崩坏：星穹铁道

    截图走 macOS 原生 API（DXMT 渲染窗口），输入经 Wine 桥注入。
    对外统一使用 1920x1080 标准坐标。
    """

    def __init__(self, cfg: Config, logger: Optional[Logger] = None) -> None:
        super().__init__(script_path=cfg.script_path, logger=logger)
        self.cfg = cfg
        self.game_dir = cfg.get_value("yaagl_game_dir", DEFAULT_GAME_DIR)
        self.app_path = cfg.get_value("yaagl_app_path", YAAGL_HSR_APP)

        self.client = WineBridgeClient(
            host=cfg.get_value("yaagl_bridge_host", "127.0.0.1"),
            port=int(cfg.get_value("yaagl_bridge_port", 17400)),
            token=str(cfg.get_value("yaagl_bridge_token", "")),
        )

        self._input_handler = None
        self._lock = threading.RLock()
        self._window_id: int = 0
        self._pid: int = 0
        self._last_frame: np.ndarray | None = None
        self._last_frame_ts: float = 0.0
        self._frame_interval = 1.0 / float(cfg.get_value("yaagl_capture_fps", 15))

    # ---------- 生命周期 ----------

    def start_game_process(self) -> bool:
        """启动 Yaagl 中的游戏（不抢前台）"""
        if self._find_wine_pid() is not None:
            return True
        if not os.path.exists(self.app_path):
            self.log_error(f"未找到 Yaagl：{self.app_path}")
            return False

        result = subprocess.run(["open", "-g", "-a", self.app_path],
                                capture_output=True, text=True)
        if result.returncode != 0:
            self.log_error(f"启动 Yaagl 失败：{result.stderr.strip()}")
            return False
        self.log_info(f"Yaagl 启动：{self.app_path}")
        return True

    def stop_game(self) -> bool:
        """终止游戏进程"""
        try:
            if self._input_handler is not None:
                try:
                    self._input_handler.release_all()
                except Exception:
                    pass
            self.client.close()
            pid = self._find_wine_pid()
            if pid is None:
                return True
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except psutil.TimeoutExpired:
                proc.kill()
            self.log_info(f"游戏终止：{pid}")
            return True
        except Exception as e:
            self.log_error(f"终止游戏时发生错误：{e}")
            return False

    # ---------- 进程与窗口 ----------

    def _find_wine_pid(self) -> Optional[int]:
        """找到运行崩铁的 Wine 进程

        Wine 下进程名可能是 wine/wine64/preloader，真正的游戏标识在 cmdline 里的 StarRail.exe。
        同时排除 winedevice.exe / wineserver 等辅助进程。
        """
        game_name = "StarRail.exe"
        exclude = ("winedevice", "wineserver", "wineboot", "winecfg",
                   "services.exe", "plugplay", "explorer.exe")
        for proc in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
            try:
                name = (proc.info["name"] or "")
                cmdline = " ".join(proc.info["cmdline"] or [])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            # 只看含 StarRail.exe 的进程
            if game_name.lower() not in cmdline.lower():
                continue
            # 排除辅助进程
            low = (name + " " + cmdline).lower()
            if any(e in low for e in exclude):
                continue
            return proc.info["pid"]
        return None

    def get_window_handle(self) -> int:
        """无 Win32 句柄；返回 Wine 进程 PID 表示游戏在运行"""
        pid = self._find_wine_pid()
        if pid is None:
            return 0
        self._pid = pid
        if not self.client.is_connected:
            self.client.connect()
        return pid

    def switch_to_game(self) -> bool:
        """把游戏窗口切到前台并通知桥注册目标

        桥内部会 SetForegroundWindow + SetFocus，之后 ClientToScreen 与 SendInput
        才能落在游戏客户区上（键盘需要窗口有焦点）。
        """
        pid = self.get_window_handle()
        if pid == 0:
            self.log_debug("游戏未运行")
            return False
        # macOS 层：把 wine 应用提到最前（否则截图会抓到遮挡它的窗口）
        try:
            subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to set frontmost of '
                 '(first process whose name is "wine") to true'],
                capture_output=True, timeout=3)
        except Exception:
            pass

        # Wine 层：注册目标窗口并 SetFocus（键盘需要焦点）
        title = self.cfg.get_value("game_title_name", "崩坏：星穹铁道")
        self.client.set_foreground(pid, title=title)
        return True

    def get_resolution(self):
        return STANDARD_WIDTH, STANDARD_HEIGHT

    def get_input_handler(self):
        if self._input_handler is None:
            from module.automation.yaagl_input import YaaglInput
            self._input_handler = YaaglInput(self, self.logger)
        return self._input_handler

    def copy(self, text: str) -> None:
        """macOS 剪贴板（Wine 与 macOS 共享剪贴板）"""
        subprocess.run(["pbcopy"], input=str(text).encode("utf-8"), check=False)

    # ---------- 截图（ScreenCaptureKit / CGWindowList） ----------

    _TITLE_HINTS = ("崩坏", "星穹铁道", "StarRail", "Honkai")

    def _find_game_window(self) -> int:
        """在 macOS 窗口列表里找 Yaagl 的游戏窗口

        三级回退，避免游戏过场/战斗时窗口标题或层级变化导致找不到：
          1) 屏幕上的窗口 + 标题命中
          2) 所有窗口 + 标题命中
          3) 任何 owner=wine 且尺寸足够大的窗口（取最大，排除细长辅助窗口）
        """
        try:
            import Quartz
        except ImportError:
            self.log_error("缺少 pyobjc，无法截图")
            return 0

        def _collect(onscreen_only: bool):
            opts = Quartz.kCGWindowListOptionAll
            if onscreen_only:
                opts = (Quartz.kCGWindowListOptionOnScreenOnly
                        | Quartz.kCGWindowListExcludeDesktopElements)
            out = []
            for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []:
                b = w.get('kCGWindowBounds') or {}
                out.append({
                    'wid': int(w.get('kCGWindowNumber') or 0),
                    'owner': str(w.get('kCGWindowOwnerName') or ''),
                    'name': str(w.get('kCGWindowName') or ''),
                    'w': int(b.get('Width', 0) or 0),
                    'h': int(b.get('Height', 0) or 0),
                })
            return out

        def _usable(w):
            # 排除细长辅助窗（Wine 的 2560x30 之类）
            return w['w'] >= 640 and w['h'] >= 360 and w['h'] > 200

        def _is_wine(w):
            return 'wine' in w['owner'].lower()

        def _title_hit(w):
            hay = (w['owner'] + ' ' + w['name']).lower()
            return any(h.lower() in hay for h in self._TITLE_HINTS)

        # 1) 最可靠：owner 含 wine（游戏跑在 Wine 里）且标题命中
        #    注意不能只按标题——其他应用窗口标题也可能含「星穹铁道」
        for w in _collect(True) + _collect(False):
            if _usable(w) and _is_wine(w) and _title_hit(w):
                return w['wid']

        # 2) owner 含 wine 的窗口里取最大（不管标题）
        wine_wins = [w for w in _collect(False) if _usable(w) and _is_wine(w)]
        if wine_wins:
            wine_wins.sort(key=lambda x: x['w'] * x['h'], reverse=True)
            self.log_debug(f"按 owner=wine 取最大窗口 wid={wine_wins[0]['wid']}")
            return wine_wins[0]['wid']

        # 3) 没有 wine 窗口 = 游戏没在跑。此时绝不能按标题兜底，
        #    否则会抓到标题恰好含「星穹铁道」的其他应用（实测抓到过编辑器窗口，
        #    截图内容全是代码，导致后续所有识别失效）。
        self.log_debug("未找到 wine 游戏窗口（游戏可能未启动）")
        return 0

    def screenshot_bgr(self) -> np.ndarray | None:
        """标准分辨率 BGR 截图（带帧率限制缓存）"""
        now = time.monotonic()
        if self._last_frame is not None and (now - self._last_frame_ts) < self._frame_interval:
            return self._last_frame

        try:
            import Quartz
        except ImportError:
            return None

        # 最多重试 3 次：窗口切换/过场时偶发抓取失败
        img = None
        for _attempt in range(3):
            wid = self._window_id or self._find_game_window()
            if not wid:
                time.sleep(0.1)
                continue
            img = Quartz.CGWindowListCreateImage(
                Quartz.CGRectNull,
                Quartz.kCGWindowListOptionIncludingWindow,
                wid,
                Quartz.kCGWindowImageBoundsIgnoreFraming,
            )
            if img is not None:
                self._window_id = wid
                break
            self._window_id = 0
            time.sleep(0.15)
        if img is None:
            self.log_debug("截图连续失败，未取得游戏窗口画面")
            return None

        width = Quartz.CGImageGetWidth(img)
        height = Quartz.CGImageGetHeight(img)
        bytes_per_row = Quartz.CGImageGetBytesPerRow(img)
        data_provider = Quartz.CGImageGetDataProvider(img)
        data = Quartz.CGDataProviderCopyData(data_provider)
        buf = np.frombuffer(data, dtype=np.uint8)

        # CGImage 是 BGRA 或 RGBA，按 bitmapInfo 判断
        bpp = Quartz.CGImageGetBitsPerPixel(img) // 8
        arr = buf.reshape((height, bytes_per_row // bpp, bpp))[:, :width, :]
        if bpp == 4:
            bgr = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
        elif bpp == 3:
            bgr = arr
        else:
            return None

        # Wine 窗口含标题栏（约 32px @ 1080p 比例），裁掉后才是游戏客户区
        h, w = bgr.shape[:2]
        if h > w * 9 / 16:
            title_bar = h - int(w * 9 / 16)
            if 0 < title_bar < h // 4:      # 合理范围校验
                bgr = bgr[title_bar:, :]
                self.log_debug(f"裁剪标题栏 {title_bar}px: {w}x{h} -> {bgr.shape[1]}x{bgr.shape[0]}")

        if bgr.shape[1] != STANDARD_WIDTH or bgr.shape[0] != STANDARD_HEIGHT:
            bgr = cv2.resize(bgr, (STANDARD_WIDTH, STANDARD_HEIGHT),
                             interpolation=cv2.INTER_AREA)

        self._last_frame = bgr
        self._last_frame_ts = now
        return bgr

    def take_screenshot(self, crop=(0, 0, 1, 1)):
        """
        截图并按比例裁剪

        Returns:
            (PIL RGB 图像, (left, top, width, height), 缩放比例)；失败返回 None
        """
        image = self.screenshot_bgr()
        if image is None:
            return None
        left = min(max(int(STANDARD_WIDTH * crop[0]), 0), STANDARD_WIDTH - 1)
        top = min(max(int(STANDARD_HEIGHT * crop[1]), 0), STANDARD_HEIGHT - 1)
        width = max(min(int(STANDARD_WIDTH * crop[2]), STANDARD_WIDTH - left), 1)
        height = max(min(int(STANDARD_HEIGHT * crop[3]), STANDARD_HEIGHT - top), 1)
        image = image[top:top + height, left:left + width]
        screenshot = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        return screenshot, (left, top, width, height), 1

    def wait_bridge(self, timeout: float = 120) -> bool:
        """等待 Wine 桥就绪"""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.client.connect():
                return True
            time.sleep(2)
        return False
