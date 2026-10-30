#!/usr/bin/env python3
"""
Yaagl 游戏窗口捕获预检

在跑正式任务前，用它逐层验证 3C 链路是否就绪：
  1. 进程：Wine 里跑着 StarRail.exe 吗
  2. 窗口：macOS 侧能枚举到游戏窗口吗
  3. 截图：ScreenCaptureKit/CGWindowList 能抓到非黑屏画面吗
  4. 分辨率：画面能否归一化到 1920x1080
  5. 识别：M7A 的模板匹配能认出当前界面吗
  6. 输入：Wine 桥连通吗

用法：
  ./.venv/bin/python tools/preflight_yaagl.py              # 全量检查
  ./.venv/bin/python tools/preflight_yaagl.py --save       # 保存截图到 /tmp
  ./.venv/bin/python tools/preflight_yaagl.py --watch 10   # 连续抓 10 帧测稳定性
"""

import argparse
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, ROOT)

STANDARD_WIDTH = 1920
STANDARD_HEIGHT = 1080


def _hr(title):
    print(f"\n{'=' * 62}\n  {title}\n{'=' * 62}")


def _ok(msg):
    print(f"  [✓] {msg}")


def _warn(msg):
    print(f"  [!] {msg}")


def _fail(msg):
    print(f"  [✗] {msg}")


# ---------- 1. 进程 ----------

def check_process(game_dir=None):
    _hr("1. 进程检查")
    try:
        import psutil
    except ImportError:
        _fail("缺少 psutil")
        return None

    hits = []
    for proc in psutil.process_iter(attrs=["pid", "name", "cmdline"]):
        try:
            name = proc.info["name"] or ""
            cmd = " ".join(proc.info["cmdline"] or "")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if "StarRail" in name or "StarRail.exe" in cmd:
            hits.append((proc.info["pid"], name, cmd[:100]))

    if hits:
        for pid, name, cmd in hits:
            _ok(f"pid={pid} name={name}")
            print(f"       cmd: {cmd}")
        return hits[0][0]
    _fail("未找到 StarRail 进程（游戏没启动？）")
    return None


# ---------- 2. 窗口 ----------

def check_windows():
    _hr("2. macOS 窗口枚举")
    try:
        import Quartz
    except ImportError:
        _fail("缺少 pyobjc-framework-Quartz")
        return []

    opts = (Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements)
    wins = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []
    _ok(f"共 {len(wins)} 个可见窗口")

    candidates = []
    print("\n  大窗口列表（>640x360）:")
    for w in wins:
        owner = w.get('kCGWindowOwnerName') or ''
        name = w.get('kCGWindowName') or ''
        b = w.get('kCGWindowBounds') or {}
        width = int(b.get('Width', 0) or 0)
        height = int(b.get('Height', 0) or 0)
        wid = int(w.get('kCGWindowNumber') or 0)
        if width < 640 or height < 360:
            continue
        print(f"    wid={wid:6d}  {width}x{height:4d}  owner={owner!r}  title={name!r}")
        hay = (owner + " " + name).lower()
        # 排除启动器窗口本身（它是 1280x730 左右的 UI，不是游戏画面）
        if "launcher" in hay or "yet another anime game launcher" in hay:
            continue
        # 游戏窗口特征：Wine 进程 + 接近 16:9 或全屏尺寸
        ratio = width / height if height else 0
        if "wine" in hay or "starrail" in hay or "崩坏" in hay or "honkai" in hay:
            candidates.append((wid, owner, name, width, height, ratio))

    if candidates:
        _ok(f"找到 {len(candidates)} 个游戏候选窗口")
    else:
        _warn("未找到游戏窗口（可能还在加载，或标题不匹配）")
    return candidates


# ---------- 3. 截图 ----------

def check_capture(window_id=None, save=False):
    _hr("3. 截图测试")
    try:
        import Quartz
        import cv2
        import numpy as np
    except ImportError as e:
        _fail(f"缺少依赖：{e}")
        return None

    if window_id is None:
        cands = check_windows()
        if not cands:
            _fail("没有可截图的窗口")
            return None
        window_id = cands[0][0]

    img = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        window_id,
        Quartz.kCGWindowImageBoundsIgnoreFraming | Quartz.kCGWindowImageNominalResolution,
    )
    if img is None:
        _fail("截图返回 None（可能无屏幕录制权限）")
        return None

    width = Quartz.CGImageGetWidth(img)
    height = Quartz.CGImageGetHeight(img)
    bpr = Quartz.CGImageGetBytesPerRow(img)
    bpp = Quartz.CGImageGetBitsPerPixel(img) // 8
    _ok(f"截图成功 {width}x{height} bpp={bpp}")

    data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(img))
    buf = np.frombuffer(data, dtype=np.uint8)
    arr = buf.reshape((height, bpr // bpp, bpp))[:, :width, :]

    if bpp == 4:
        bgr = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
    else:
        bgr = arr[:, :, :3]

    mean = float(bgr.mean())
    std = float(bgr.std())
    _ok(f"画面统计：mean={mean:.1f} std={std:.1f}")
    if mean < 3 and std < 3:
        _fail("画面几乎全黑——保存后人工确认（可能是 DXMT 渲染窗口抓不到）")
    elif std < 8:
        _warn("画面差异度偏低，可能是纯色/加载页")
    else:
        _ok("画面有内容（非黑屏）")

    if save:
        out = f"/tmp/yaagl_capture_{int(time.time())}.png"
        cv2.imwrite(out, bgr)
        _ok(f"已保存：{out}")

    return bgr


# ---------- 4. 分辨率归一化 ----------

def check_resize(bgr):
    _hr("4. 分辨率归一化到 1920x1080")
    import cv2
    if bgr is None:
        _fail("无截图可处理")
        return None
    h, w = bgr.shape[:2]
    _ok(f"原始 {w}x{h}")
    if (w, h) == (STANDARD_WIDTH, STANDARD_HEIGHT):
        _ok("已是标准分辨率，无需缩放")
        return bgr
    out = cv2.resize(bgr, (STANDARD_WIDTH, STANDARD_HEIGHT), interpolation=cv2.INTER_AREA)
    _ok(f"已缩放到 {STANDARD_WIDTH}x{STANDARD_HEIGHT}")
    ratio = w / h
    target = STANDARD_WIDTH / STANDARD_HEIGHT
    if abs(ratio - target) > 0.05:
        _warn(f"宽高比 {ratio:.2f} 与 16:9({target:.2f}) 差距大，模板匹配可能失真")
    return out


# ---------- 5. 界面识别 ----------

def check_recognition(bgr):
    _hr("5. M7A 界面识别")
    if bgr is None:
        _fail("无截图可供识别")
        return

    try:
        from module.automation.automation import Automation
        _ok("Automation 模块可导入")
    except Exception as e:
        _fail(f"Automation 导入失败：{e}")
        return

    # 直接用 OpenCV 做一次模板匹配探测，不依赖完整 Automation 上下文
    try:
        import cv2
        import glob
    except ImportError:
        _fail("缺少 cv2")
        return

    templates = [
        ("主界面", "assets/images/screen/main.png"),
        ("手机菜单", "assets/images/share/menu/more.png"),
        ("登录页", "assets/images/screen/login.png"),
        ("游戏内", "assets/images/screen/click_enter.png"),
    ]
    found_any = False
    for label, path in templates:
        full = os.path.join(ROOT, path)
        if not os.path.exists(full):
            continue
        tpl = cv2.imread(full)
        if tpl is None:
            continue
        if tpl.shape[0] > bgr.shape[0] or tpl.shape[1] > bgr.shape[1]:
            _warn(f"{label} 模板比画面大，跳过")
            continue
        res = cv2.matchTemplate(bgr, tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(res)
        mark = "✓" if max_val >= 0.85 else " "
        print(f"    [{mark}] {label:8s} 相似度 {max_val:.3f}  ({path})")
        if max_val >= 0.85:
            found_any = True

    if found_any:
        _ok("识别到已知界面（模板匹配链路通）")
    else:
        _warn("未匹配到已知模板——可能停在非常规界面，或需要重新校准")


# ---------- 6. Wine 桥 ----------

def check_bridge(host, port, token):
    _hr("6. Wine 输入桥")
    try:
        from module.game.wine_bridge_client import WineBridgeClient
    except Exception as e:
        _fail(f"客户端导入失败：{e}")
        return False

    cli = WineBridgeClient(host, port, token)
    if not cli.connect():
        _fail(f"连接失败 {host}:{port}")
        return False
    _ok(f"已连接 {host}:{port}  版本={cli.bridge_version} 能力=0x{cli.bridge_caps:x}")
    cli.close()
    return True


# ---------- 稳定性 ----------

def watch(window_id, frames):
    _hr(f"连续抓 {frames} 帧测稳定性")
    import Quartz
    import numpy as np

    prev = None
    diffs = []
    t0 = time.monotonic()
    for i in range(frames):
        img = Quartz.CGWindowListCreateImage(
            Quartz.CGRectNull,
            Quartz.kCGWindowListOptionIncludingWindow,
            window_id,
            Quartz.kCGWindowImageBoundsIgnoreFraming | Quartz.kCGWindowImageNominalResolution,
        )
        if img is None:
            _fail(f"第 {i+1} 帧失败")
            continue
        bpr = Quartz.CGImageGetBytesPerRow(img)
        bpp = Quartz.CGImageGetBitsPerPixel(img) // 8
        h = Quartz.CGImageGetHeight(img)
        w = Quartz.CGImageGetWidth(img)
        data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(img))
        arr = np.frombuffer(data, dtype=np.uint8).reshape((h, bpr // bpp, bpp))[:, :w, :]
        cur = arr[:, :, :3].astype(np.float32)
        if prev is not None and prev.shape == cur.shape:
            diffs.append(float(np.abs(cur - prev).mean()))
        prev = cur
        time.sleep(1 / 15)

    elapsed = time.monotonic() - t0
    fps = frames / elapsed if elapsed else 0
    _ok(f"抓帧率 {fps:.1f} fps（目标 15）")
    if diffs:
        print(f"    帧间差异：min={min(diffs):.2f} max={max(diffs):.2f} avg={sum(diffs)/len(diffs):.2f}")
        if max(diffs) < 0.5:
            _warn("帧几乎不动——画面可能是静态的（加载页）或抓的是缓存")
        else:
            _ok("画面在变化（正常）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="保存截图到 /tmp")
    ap.add_argument("--watch", type=int, default=0, metavar="N", help="连续抓 N 帧测稳定性")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=17400)
    ap.add_argument("--token", default=os.environ.get("M7A_BRIDGE_TOKEN", ""))
    args = ap.parse_args()

    print("\n" + "=" * 62)
    print("  Yaagl 3C 链路预检")
    print("=" * 62)

    pid = check_process()
    cands = check_windows()
    window_id = cands[0][0] if cands else None
    bgr = check_capture(window_id, save=args.save)
    std = check_resize(bgr)
    check_recognition(std)
    bridge_ok = check_bridge(args.host, args.port, args.token)

    if args.watch and window_id:
        watch(window_id, args.watch)

    _hr("总结")
    print(f"  游戏进程      : {'✓' if pid else '✗'}")
    print(f"  窗口枚举      : {'✓' if cands else '✗'}")
    print(f"  截图          : {'✓' if bgr is not None else '✗'}")
    print(f"  Wine 桥       : {'✓' if bridge_ok else '✗'}")
    print()
    if pid and cands and bgr is not None and bridge_ok:
        print("  3C 链路全部就绪，可以跑 main.py 任务了。")
        return 0
    print("  有环节未就绪，请按上面提示逐项排查。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
