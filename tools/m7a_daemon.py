#!/usr/bin/env python3
"""M7A 启动守护进程（常驻）

让你只需在 Yaagl 启动器里点「开始游戏」，剩下的自动完成：

  检测游戏进程 → 立即断网 120 秒（绕过 HoYoKProtect 反作弊）
  → 自动恢复网络 → 等游戏窗口出现 → 自动启动/重启 Wine 桥

特性：
  - 常驻循环，每次游戏启动只处理一次（进程消失后自动复位）
  - 游戏退出后可选自动关闭桥，释放 wineserver，避免下次启动冲突
  - 桥挂了/端口失联会自动拉起
  - 不截图、不碰桥协议，只做进程/网络/端口管理，不与 M7A 任务争用桥

用法：
  python3 m7a_daemon.py                # 前台跑（看日志）
  nohup python3 m7a_daemon.py &        # 后台常驻
"""
import os
import subprocess
import time
from datetime import datetime

# ============ 配置 ============
IFACE = os.environ.get("M7A_IFACE", "en1")
HOLD_SEC = int(os.environ.get("M7A_HOLD_SEC", "120"))       # 断网保持秒数
BRIDGE_PORT = int(os.environ.get("M7A_BRIDGE_PORT", "17400"))
BRIDGE_TOKEN = os.environ.get("M7A_BRIDGE_TOKEN", "")  # 留空则从 config.yaml 读取
SUPPORT_DIR = os.path.expanduser("~/Library/Application Support/Yaagl HSR")
PROJECT_DIR = os.path.expanduser("~/m7a-3c")
LOG_FILE = "/tmp/m7a_daemon.log"
POLL = 2                      # 轮询间隔（秒）
WINDOW_WAIT_TIMEOUT = 180     # 恢复网络后等游戏窗口的最长秒数
CLOSE_BRIDGE_ON_EXIT = True   # 游戏退出后是否关桥

STATE_IDLE = "IDLE"           # 等游戏进程
STATE_HOLDING = "HOLDING"     # 断网中
STATE_WAIT_WINDOW = "WAIT"    # 等游戏窗口出现
STATE_RUNNING = "RUNNING"     # 游戏+桥都就绪


def log(msg):
    line = f"{datetime.now():%H:%M:%S} {msg}"
    print(line, flush=True)
    try:
        # 日志超 2MB 自动截断，避免无限增长
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 2 * 1024 * 1024:
            with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                tail = f.readlines()[-500:]
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(tail)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def run(cmd, **kw):
    """执行 shell 命令，带超时保护，避免任何子进程把守护卡死"""
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=15, **kw)
    except subprocess.TimeoutExpired:
        if os.environ.get("M7A_DEBUG"):
            print(f"命令超时: {cmd[:80]}", flush=True)
        return subprocess.CompletedProcess(cmd, -1, "", "")


def sh(cmd):
    return run(cmd).stdout.strip()


def game_running():
    return run("pgrep -f 'StarRail.exe'").returncode == 0


def bridge_listening():
    return f"TCP *:{BRIDGE_PORT}" in sh(f"lsof -i :{BRIDGE_PORT} 2>/dev/null") or \
        f"localhost:{BRIDGE_PORT}" in sh(f"lsof -i :{BRIDGE_PORT} 2>/dev/null")


def net_ip():
    return sh(f"ifconfig {IFACE} 2>/dev/null | grep 'inet ' | awk '{{print $2}}'")


def game_window_exists():
    """用 Quartz 判游戏窗口是否出现（标题含星穹铁道，尺寸足够大）"""
    try:
        import Quartz
        wins = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID) or []
        for w in wins:
            name = str(w.get('kCGWindowName') or '')
            b = w.get('kCGWindowBounds') or {}
            wd = int(b.get('Width', 0) or 0)
            ht = int(b.get('Height', 0) or 0)
            if "星穹铁道" in name and wd > 800 and ht > 500:
                return True
    except Exception:
        pass
    return False


def kill_bridge_only():
    """只关桥进程，绝不碰游戏依赖的 winedevice/wineserver。

    游戏运行期间重启桥必须用这个 —— 杀了 winedevice/services.exe 会让
    游戏 msync 通道断裂，刷出海量 err:sync:server_register_wait 后卡死。
    """
    run("pkill -f m7a_wine_bridge")
    time.sleep(1.2)


def kill_bridge():
    """关桥并彻底清理整个 wine 进程组。

    仅限【游戏未运行】时调用（启动前预清理 / 游戏已退出后收尾）。
    winedevice.exe 会占用 wineprefix，残留会导致下次启动器执行
    winedrv_config.bat 失败（Fatal error: Command return non-zero code (1)）。
    """
    run("pkill -f m7a_wine_bridge")
    time.sleep(0.8)
    for pat in ("winedevice", "plugplay", "services.exe", "wineserver"):
        run(f"pkill -9 -f {pat}")
    time.sleep(1.5)


def start_bridge():
    """在与游戏相同的 wineprefix 里起桥

    必须用 Popen 且完全脱离（start_new_session + DEVNULL 管道），
    否则 subprocess.run 会一直等桥进程的 stdout 管道关闭，
    导致守护卡死在这个 poll 上不再轮询（实测调用栈阻塞在 select_poll_poll）。
    """
    py = f"{SUPPORT_DIR}/wineprefix/drive_c/m7apy"
    if not os.path.exists(f"{py}/python.exe"):
        log("✗ 桥 Python 不存在，跳过")
        return False
    run(f"cp {PROJECT_DIR}/tools/m7a_wine_bridge.py {py}/")
    cmd = (
        f'WINEPREFIX="{SUPPORT_DIR}/wineprefix" WINEMSYNC=1 WINEDEBUG=-all '
        f'./wine "C:\\\\m7apy\\\\python.exe" "C:\\\\m7apy\\\\m7a_wine_bridge.py" '
        f'--port {BRIDGE_PORT} --token "{BRIDGE_TOKEN}"'
    )
    try:
        with open("/tmp/m7a_bridge.log", "w") as bridge_log:
            subprocess.Popen(
                cmd, shell=True, cwd=f"{SUPPORT_DIR}/wine/bin",
                stdin=subprocess.DEVNULL,
                stdout=bridge_log, stderr=bridge_log,
                start_new_session=True,   # 脱离会话，父进程退出也不影响
            )
        log("→ 已发起桥启动（已脱离，不阻塞守护）")
        return True
    except Exception as exc:
        log(f"✗ 桥启动失败：{exc}")
        return False


def main():
    log("=" * 50)
    log(f"M7A 守护启动：断网 {HOLD_SEC}s，桥端口 {BRIDGE_PORT}")

    # 启动预清理：若上次异常退出留下 wine 进程组，先清掉，避免下次启动器
    # 执行 winedrv_config.bat 时报 Fatal error（prefix 被占用）
    leftovers = sh("pgrep -f 'winedevice|wineserver|m7a_wine_bridge' | wc -l").strip()
    if leftovers and leftovers != "0":
        log(f"发现 {leftovers} 个残留 wine 进程，执行预清理")
        kill_bridge()
        log("预清理完成")

    state = STATE_IDLE
    hold_start = 0.0
    wait_start = 0.0

    # 启动时游戏已在跑：直接进入运行态，不打断当前游戏，只保证桥在
    if game_running():
        log("检测到游戏已在运行，跳过断网流程，仅确保桥在线")
        if not bridge_listening():
            kill_bridge_only()      # 游戏在跑，只能杀桥，不能碰 winedevice
            start_bridge()
        state = STATE_RUNNING

    log(f"当前状态 {state}，等待游戏启动…（点启动器「开始游戏」即可）")

    while True:
        try:
            in_game = game_running()

            if state == STATE_IDLE:
                if in_game:
                    log("检测到游戏进程 → 立即断网")
                    run(f"sudo -n /sbin/ifconfig {IFACE} down")
                    hold_start = time.time()
                    state = STATE_HOLDING

            elif state == STATE_HOLDING:
                if not in_game:
                    log("游戏进程消失，恢复网络并复位")
                    run(f"sudo -n /sbin/ifconfig {IFACE} up")
                    run(f"sudo -n /usr/sbin/ipconfig set {IFACE} DHCP")
                    state = STATE_IDLE
                elif time.time() - hold_start >= HOLD_SEC:
                    log(f"断网 {HOLD_SEC}s 已满，恢复网络")
                    run(f"sudo -n /sbin/ifconfig {IFACE} up")
                    run(f"sudo -n /usr/sbin/ipconfig set {IFACE} DHCP")
                    time.sleep(4)
                    log(f"网络恢复 ip={net_ip() or '获取中'}")
                    wait_start = time.time()
                    state = STATE_WAIT_WINDOW

            elif state == STATE_WAIT_WINDOW:
                if not in_game:
                    log("等待窗口期间游戏退出，复位")
                    state = STATE_IDLE
                else:
                    if game_window_exists():
                        log("游戏窗口已出现，启动桥（不碰游戏 wine 进程）")
                        kill_bridge_only()
                        start_bridge()
                        state = STATE_RUNNING
                    elif time.time() - wait_start > WINDOW_WAIT_TIMEOUT:
                        log("等窗口超时，仍尝试起桥")
                        kill_bridge_only()
                        start_bridge()
                        state = STATE_RUNNING

            elif state == STATE_RUNNING:
                if not in_game:
                    log("游戏退出")
                    if CLOSE_BRIDGE_ON_EXIT:
                        kill_bridge()
                        log("已关闭桥并清理 wineserver")
                    state = STATE_IDLE
                else:
                    # 游戏在跑但桥端口失联 → 自动拉起
                    if not bridge_listening():
                        log("桥端口失联，自动拉起")
                        start_bridge()
                    else:
                        # 桥进程可能还在但端口没起来（启动中），不要重复杀
                        pass

            time.sleep(POLL)
        except KeyboardInterrupt:
            log("收到中断，守护退出")
            break
        except Exception as e:
            log(f"异常：{e}")
            time.sleep(POLL)


if __name__ == "__main__":
    main()
