#!/bin/bash
# 崩铁断网启动监视器 —— 检测到游戏进程立即断网，维持指定秒数后自动恢复
#
# 用途：绕过 HoYoKProtect 反作弊（Wine 无法加载 WDFLDR.SYS 内核驱动）
#       断网后反作弊连不上服务器，超时放行。
#
# 用法：start_game_with_bypass.sh [最长等待秒数] [断网保持秒数]
#   默认：等 900 秒（15 分钟）、断网保持 120 秒

IFACE="en1"
LOG=/tmp/watch_start.log
WAIT_MAX="${1:-900}"      # 最长等待游戏进程出现的时间
HOLD_SEC="${2:-120}"      # 断网保持时长

exec >> "$LOG" 2>&1

log() { echo "$(date '+%H:%M:%S') $*"; }

log "=== 监视启动 (最长等待 ${WAIT_MAX}s, 断网保持 ${HOLD_SEC}s) ==="

# 记录启动前的 ip，用于恢复校验
ORIG_IP=$(/sbin/ifconfig "$IFACE" 2>/dev/null | grep 'inet ' | awk '{print $2}')
log "启动前 ip=${ORIG_IP:-无}"

# 放宽进程检测：StarRail.exe / StarRail / 游戏主程序
detect_game() {
    pgrep -f "StarRail.exe" > /dev/null 2>&1 && return 0
    pgrep -f "StarRail" > /dev/null 2>&1 && return 0
    pgrep -f "GenshinImpact\|YuanShen" > /dev/null 2>&1 && return 0
    return 1
}

elapsed=0
while [ "$elapsed" -lt "$WAIT_MAX" ]; do
    if detect_game; then
        log "检测到游戏进程（等待 ${elapsed}s），立即断网"
        sudo -n /sbin/ifconfig "$IFACE" down
        log "已断网，保持 ${HOLD_SEC}s 等待反作弊超时放行"

        slept=0
        while [ "$slept" -lt "$HOLD_SEC" ]; do
            sleep 5
            slept=$((slept + 5))
            # 游戏中途退出就不用继续等了
            if ! detect_game; then
                log "游戏进程已消失（${slept}s），提前恢复网络"
                break
            fi
        done

        log "恢复网络"
        sudo -n /sbin/ifconfig "$IFACE" up
        sudo -n /usr/sbin/ipconfig set "$IFACE" DHCP 2>/dev/null
        sleep 5
        NEW_IP=$(/sbin/ifconfig "$IFACE" 2>/dev/null | grep 'inet ' | awk '{print $2}')
        log "恢复后 ip=${NEW_IP:-获取失败}"
        log "=== END ==="
        exit 0
    fi
    sleep 1
    elapsed=$((elapsed + 1))
done

log "=== 超时未检测到游戏（等了 ${WAIT_MAX}s）==="
