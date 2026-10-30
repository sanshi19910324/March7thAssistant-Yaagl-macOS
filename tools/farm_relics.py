#!/usr/bin/env python
"""密伶之径连续刷取（侵蚀隧洞 - 真珠遗器）

前置：游戏已停在「侵蚀隧洞-密伶之径」挑战页，挑战次数已设 6，
      「快捷补充开拓力」已开启。

流程：
  第 1 轮：点「挑战」→ 点「开始挑战」→ 等 6 场打完
  后续轮：点「再来一次」→ 等 6 场打完
  重复直到体力/后备耗尽、找不到按钮或达到上限。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# token 从 config.yaml 的 yaagl_bridge_token 读取；也可用环境变量覆盖
# os.environ.setdefault("MARCH7TH_YAAGL_BRIDGE_TOKEN", "<你的token>")

from module.automation import auto
from module.game import get_game_controller
from module.logger import log
from tasks.power.instance import Instance

MAX_CYCLES = int(os.environ.get("M7A_MAX_CYCLES", "8"))
ATTEMPTS = int(os.environ.get("M7A_ATTEMPTS", "6"))
INSTANCE_TYPE = "侵蚀隧洞"


def start_new_challenge():
    """从挑战页开始一轮：挑战 → 开始挑战"""
    if not auto.click_element("挑战", "text", max_retries=10, need_ocr=True):
        return False
    time.sleep(1)
    try:
        from tasks.power.character import Character
        Character.borrow()
    except Exception as exc:
        log.debug(f"借用支援角色跳过: {exc}")
    if not auto.click_element("开始挑战", "text", max_retries=10,
                              crop=(1518 / 1920, 960 / 1080, 334 / 1920, 61 / 1080)):
        return False
    return True


def main():
    game = get_game_controller()
    game.switch_to_game()
    time.sleep(1.0)

    total = 0
    for cycle in range(1, MAX_CYCLES + 1):
        log.hr(f"第 {cycle} 轮（{ATTEMPTS} 场）", 1)

        # 结算页有「再来一次」就直接续战（沿用体力补充设置），否则从挑战页发起
        clicked = auto.click_element("./assets/images/zh_CN/fight/fight_again.png",
                                     "image", 0.88, max_retries=3)
        if clicked:
            log.info("检测到结算页，点「再来一次」续战")
            time.sleep(3)
        else:
            if not start_new_challenge():
                log.warning("未能发起挑战，结束")
                break

        if not Instance.wait_fight(cycle, timeout=3600):
            log.warning("战斗失败或中断，结束")
            break

        total += ATTEMPTS
        log.info(f"累计约 {total} 场（{total} 件遗器掉落）")

    log.hr("刷取结束", 2)
    log.info(f"总计约 {total} 场战斗")


if __name__ == "__main__":
    main()
