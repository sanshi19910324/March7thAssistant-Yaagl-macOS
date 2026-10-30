#!/usr/bin/env python
"""领取每日实训的活跃度奖励（单项奖励 + 里程碑礼盒）

M7A 的 daily/routine 任务只「完成任务」，不会点「领取」，
所以跑完 routine 后活跃度仍然是 0。本脚本补齐这一步。

流程：
  1. 切到 星际和平指南-每日实训
  2. 反复点「领取」，直到没有可领的（最多 12 次）
  3. 依次点 5 个活跃度里程碑礼盒（100/200/300/400/500）
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# token 从 config.yaml 的 yaagl_bridge_token 读取；也可用环境变量覆盖
# os.environ.setdefault("MARCH7TH_YAAGL_BRIDGE_TOKEN", "<你的token>")

from module.automation import auto
from module.game import get_game_controller
from module.game.wine_bridge_client import Command
from module.logger import log
from module.screen import screen
import struct

# 里程碑礼盒的 5 个位置（1920x1080 下的绝对坐标）
MILESTONE_X = [644, 888, 1132, 1376, 1620]
MILESTONE_Y = 320


def go_to_daily_tasks(game):
    """导航到每日实训页面"""
    for _ in range(3):
        game.client.raw_command(Command.INPUT, struct.pack('<BHH', 1, 0x1B, 80))
        time.sleep(0.9)

    screen.current_screen = None
    auto.take_screenshot()
    for name in ('main', 'menu'):
        if screen.check_screen(name):
            if name == 'main':
                screen.change_to('menu')
                time.sleep(1.2)
            screen.change_to('guide2')
            time.sleep(2)
            return True
    log.warning("未能识别主界面/菜单，跳过领奖")
    return False


def claim_task_rewards(max_clicks=12):
    """反复点「领取」，直到没有可领的"""
    claimed = 0
    for _ in range(max_clicks):
        auto.take_screenshot()
        if not auto.click_element('领取', 'text', max_retries=2):
            break
        claimed += 1
        time.sleep(2)
    log.info(f"单项奖励领取 {claimed} 次")
    return claimed


def claim_milestones(game):
    """依次点 5 个里程碑礼盒"""
    for i, x in enumerate(MILESTONE_X, 1):
        game.client.raw_command(Command.INPUT,
                                struct.pack('<BHHH', 2, x, MILESTONE_Y, 90))
        time.sleep(1.4)
    log.info("里程碑奖励已领取（100/200/300/400/500）")


def main():
    game = get_game_controller()
    game.switch_to_game()
    time.sleep(0.8)

    if not go_to_daily_tasks(game):
        return 1

    claim_task_rewards()
    claim_milestones(game)
    log.info("每日实训领奖完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
