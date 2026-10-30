#!/bin/bash
# M7A 崩铁日常一键脚本（macOS + Yaagl + Wine 桥）
#
# 一条命令完成：环境检查 → 启动游戏（断网绕过反作弊）→ 启动 Wine 桥 → 执行日常
#
# 用法：
#   bash m7a_daily.sh              # 完整流程（默认执行 routine）
#   bash m7a_daily.sh daily        # 只跑每日实训
#   bash m7a_daily.sh power        # 只跑清体力
#   bash m7a_daily.sh --status     # 只看状态，不执行

set -uo pipefail

# ============ 配置 ============
PROJECT_DIR="$HOME/m7a-3c"
YAAGL_APP="/Applications/Yaagl HSR.app"
SUPPORT_DIR="$HOME/Library/Application Support/Yaagl HSR"
BRIDGE_PORT=17400
BRIDGE_TOKEN="${M7A_BRIDGE_TOKEN:-}"   # 留空则从 config.yaml 读取
NET_IFACE="en1"
GAME_TITLE="崩坏：星穹铁道"
PLAY_TIME_WAIT=120          # 游戏启动后强制等多久（秒）

# 颜色
G='\033[32m'; R='\033[31m'; Y='\033[33m'; B='\033[36m'; N='\033[0m'
ok()   { echo -e "${G}  ✓ $*${N}"; }
bad()  { echo -e "${R}  ✗ $*${N}"; }
warn() { echo -e "${Y}  ! $*${N}"; }
info() { echo -e "${B}  · $*${N}"; }
hr()   { echo -e "\n${B}==== $* ====${N}"; }

# ============ 基础检查 ============
game_running() { pgrep -f "StarRail.exe" > /dev/null 2>&1; }
bridge_running() { pgrep -f "m7a_wine_bridge" > /dev/null 2>&1; }
bridge_alive()  { lsof -i :"$BRIDGE_PORT" 2>/dev/null | grep -q LISTEN; }
net_ok()        { ifconfig "$NET_IFACE" 2>/dev/null | grep -q "inet "; }

show_status() {
  hr "当前状态"
  game_running   && ok "游戏进程：运行中" || bad "游戏进程：未运行"
  bridge_alive   && ok "Wine 桥：监听 $BRIDGE_PORT" || bad "Wine 桥：未监听"
  net_ok         && ok "网络：正常" || bad "网络：异常"
  local wid
  wid=$(osascript -e 'tell application "System Events" to get name of every process whose background only is false' 2>/dev/null | tr ',' '\n' | grep -c "Yaagl" || true)
  [ "${wid:-0}" -gt 0 ] && ok "Yaagl 启动器：运行中" || warn "Yaagl 启动器：未运行"
}

# ============ 启动游戏 ============
launch_game() {
  hr "启动游戏"

  if game_running; then
    ok "游戏已在运行，跳过启动"
    return 0
  fi

  # 启动器必须在跑
  if ! pgrep -f "Yaagl HSR.app" > /dev/null 2>&1; then
    info "启动 Yaagl HSR 启动器..."
    open -a "$YAAGL_APP" || { bad "启动器启动失败"; return 1; }
    sleep 12
  else
    info "启动器已在运行"
  fi

  # 部署断网监视器（检测到游戏进程立即断网 120 秒）
  info "部署断网监视器（绕过反作弊）..."
  rm -f /tmp/watch_start.log
  sudo -n -u "$(whoami)" /bin/bash "$PROJECT_DIR/tools/start_game_with_bypass.sh" > /dev/null 2>&1 &

  echo
  echo -e "${Y}  ┌────────────────────────────────────────────┐${N}"
  echo -e "${Y}  │  请现在手动点击启动器的「开始游戏」按钮      │${N}"
  echo -e "${Y}  │  （首次需要点，脚本会自动断网 120 秒）       │${N}"
  echo -e "${Y}  └────────────────────────────────────────────┘${N}"
  echo

  # 等游戏进程出现（最多 5 分钟）
  local waited=0
  while [ $waited -lt 300 ]; do
    if game_running; then
      ok "检测到游戏进程（${waited}s）"
      break
    fi
    sleep 3; waited=$((waited + 3))
    printf "\r  ${B}· 等待游戏启动... ${waited}s${N}"
  done
  echo

  if ! game_running; then
    bad "等待超时，游戏未启动"
    return 1
  fi

  # 等断网窗口结束 + 游戏加载到可操作界面
  info "等待断网窗口结束与游戏加载（${PLAY_TIME_WAIT}s）..."
  local t=0
  while [ $t -lt $PLAY_TIME_WAIT ]; do
    sleep 10; t=$((t + 10))
    printf "\r  ${B}· 等待中... ${t}/${PLAY_TIME_WAIT}s${N}"
    # 网络恢复了说明断网窗口结束
    if net_ok && [ $t -ge 90 ]; then
      printf "\r  ${G}✓ 网络已恢复，游戏加载完成 ${t}s${N}\n"
      break
    fi
  done
  echo

  sleep 5
  net_ok && ok "网络正常" || { warn "网络异常，尝试恢复"; sudo -n /sbin/ifconfig "$NET_IFACE" up; sudo -n /usr/sbin/ipconfig set "$NET_IFACE" DHCP 2>/dev/null; }
  return 0
}

# ============ 启动桥 ============
start_bridge() {
  hr "启动 Wine 桥"

  if bridge_alive; then
    ok "桥已在监听 $BRIDGE_PORT"
    return 0
  fi

  if bridge_running; then
    warn "桥进程存在但未监听，重启"
    pkill -f "m7a_wine_bridge" 2>/dev/null
    sleep 2
  fi

  local py="$SUPPORT_DIR/wineprefix/drive_c/m7apy"
  if [ ! -f "$py/python.exe" ]; then
    bad "wineprefix 内缺少 Python：$py"
    info "请先部署（解压 python-embed.zip 到该目录）"
    return 1
  fi
  cp "$PROJECT_DIR/tools/m7a_wine_bridge.py" "$py/" 2>/dev/null

  info "启动桥（WINEMSYNC=1）..."
  (
    export WINEPREFIX="$SUPPORT_DIR/wineprefix"
    export WINEMSYNC=1
    export WINEDEBUG=-all
    cd "$SUPPORT_DIR/wine/bin"
    nohup ./wine 'C:\m7apy\python.exe' 'C:\m7apy\m7a_wine_bridge.py' \
      --port "$BRIDGE_PORT" --token "$BRIDGE_TOKEN" > /tmp/m7a_bridge.log 2>&1 &
  )

  local waited=0
  while [ $waited -lt 40 ]; do
    if bridge_alive; then
      ok "桥已就绪（${waited}s）"
      return 0
    fi
    sleep 2; waited=$((waited + 2))
  done
  bad "桥启动超时，日志：/tmp/m7a_bridge.log"
  return 1
}

# ============ 执行日常 ============
run_task() {
  local task="${1:-routine}"
  hr "执行任务：$task"

  cd "$PROJECT_DIR" || return 1
  local log="/tmp/m7a_task_${task}_$(date +%H%M%S).log"

  info "日志：$log"
  MARCH7TH_DOCKER_STARTED=true MARCH7TH_LOG_LEVEL=INFO \
    ./.venv/bin/python main.py "$task" 2>&1 | tee "$log"

  local rc=${PIPESTATUS[0]}
  echo
  hr "结果摘要"

  grep -E "已完成：|活跃度|兑换码使用成功|成功使用了|未检测到|任务无法完成|ERROR" "$log" 2>/dev/null | tail -25

  echo
  if [ "$rc" -eq 0 ]; then
    ok "任务完成"
  else
    warn "任务退出码 $rc（可查看上方日志）"
  fi
  return "$rc"
}

# ============ 主流程 ============
main() {
  local arg="${1:-routine}"
  echo -e "${B}"
  echo "  ╔══════════════════════════════════════════════╗"
  echo "  ║   M7A 崩铁日常 · macOS + Yaagl + Wine 桥      ║"
  echo "  ╚══════════════════════════════════════════════╝"
  echo -e "${N}"

  if [ "$arg" = "--status" ] || [ "$arg" = "-s" ]; then
    show_status; exit 0
  fi

  show_status

  launch_game || { bad "游戏启动失败，终止"; exit 1; }
  start_bridge || { bad "桥启动失败，终止"; exit 1; }

  # 让游戏回到可操作状态（Esc 退出可能残留的面板）
  if game_running; then
    info "重置游戏界面（按 Esc）..."
    cd "$PROJECT_DIR" && ./.venv/bin/python - <<'PY' 2>/dev/null || true
import sys, struct, time
sys.path.insert(0, '.')
from module.game import get_game_controller
from module.game.wine_bridge_client import Command
c = get_game_controller()
c.switch_to_game(); time.sleep(0.5)
for _ in range(2):
    c.client.raw_command(Command.INPUT, struct.pack('<BHH', 1, 0x1B, 80))
    time.sleep(0.6)
PY
  fi

  run_task "$arg"

  # routine/daily 只做任务不领奖，补一步领取活跃度奖励
  if [ "$arg" = "routine" ] || [ "$arg" = "daily" ]; then
    hr "领取今日活跃度奖励"
    ./.venv/bin/python tools/claim_daily_rewards.py 2>&1 | grep -E "领取|里程碑|完成" || true
  fi
}

main "$@"
