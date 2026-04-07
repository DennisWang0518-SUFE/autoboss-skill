"""
BOSS直聘自动投递机器人

使用前：
1. 启动带调试端口的Chrome：
   open -a "Google Chrome" --args --remote-debugging-port=9222
2. 在该Chrome中登录 zhipin.com 并导航到已筛选好条件的职位列表页
3. 运行本脚本：python main.py
"""

import time
import random
import sys
import os
import subprocess
import urllib.request

from config import CONFIG
from bot import BossBot
from state import StateManager
from logger import get_logger

log = get_logger()


CHROME_PATH_MAC = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_USER_DATA_DIR = "/tmp/chrome_boss_debug"


def ensure_chrome():
    """确保 9222 端口的 Chrome 已就绪；未就绪则自动启动一个独立 profile 的 Chrome。"""
    endpoint = CONFIG["cdp_endpoint"]

    def probe():
        try:
            urllib.request.urlopen(f"{endpoint}/json/version", timeout=1)
            return True
        except Exception:
            return False

    if probe():
        log.info("检测到 Chrome 已在 9222 端口运行")
        return

    if not os.path.exists(CHROME_PATH_MAC):
        raise RuntimeError(
            f"未找到 Chrome 可执行文件: {CHROME_PATH_MAC}\n"
            "如 Chrome 装在非默认路径，请手动启动后重试，或修改 main.py 中 CHROME_PATH_MAC。"
        )

    log.info("Chrome 未运行，正在启动独立 profile 实例…")
    subprocess.Popen(
        [
            CHROME_PATH_MAC,
            "--remote-debugging-port=9222",
            f"--user-data-dir={CHROME_USER_DATA_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.time() + 10
    while time.time() < deadline:
        if probe():
            log.info("Chrome 已就绪")
            return
        time.sleep(0.5)

    raise RuntimeError(
        "Chrome 启动后 10s 内 9222 端口仍未响应。\n"
        "请手动关闭所有 Chrome 窗口（含日常 profile）后重试。"
    )


def close_chrome():
    """关闭由 ensure_chrome 启动的独立 profile Chrome 实例。
    只杀含 --remote-debugging-port=9222 + 我们的 user-data-dir 的进程，
    不会影响日常 Chrome。"""
    try:
        # pgrep 精确匹配 9222 端口的 chrome 进程
        result = subprocess.run(
            ["pgrep", "-f", f"remote-debugging-port=9222.*{CHROME_USER_DATA_DIR}"],
            capture_output=True, text=True, timeout=3,
        )
        pids = [p for p in result.stdout.strip().split("\n") if p]
        if not pids:
            return
        for pid in pids:
            try:
                subprocess.run(["kill", pid], timeout=2)
            except Exception:
                pass
        log.info(f"已关闭独立 profile Chrome（{len(pids)} 个进程）")
    except Exception as e:
        log.debug(f"close_chrome 异常: {e}")


def human_delay(range_tuple: tuple):
    t = random.uniform(*range_tuple)
    log.debug(f"等待 {t:.1f}s…")
    time.sleep(t)


def session_break():
    t = random.uniform(*CONFIG["session_break"])
    log.info(f"批间休息 {t:.0f}s，模拟人类行为…")
    time.sleep(t)


def cmd_prepare():
    """Stage 1：拉起 Chrome、导航到 target_url、报告页面状态后退出。
    Claude 把状态汇报给用户，等用户在浏览器里调好筛选条件再触发 cmd_run。"""
    try:
        ensure_chrome()
    except Exception as e:
        log.error(str(e))
        sys.exit(1)

    bot = BossBot()
    try:
        bot.connect(navigate=True)
    except Exception as e:
        log.error(f"连接 Chrome 失败: {e}")
        sys.exit(1)

    status = bot.page_status()
    bot.disconnect()

    print("=" * 60)
    print("[PREPARE] 浏览器已就绪")
    print(f"  当前 URL : {status['url']}")
    print(f"  已加载卡片: {status['cards']}")
    print(f"  登录状态  : {'未登录（需扫码）' if status['not_logged_in'] else '已登录'}")
    print("=" * 60)
    print("下一步：用户在浏览器中调整筛选条件后，运行 `python3 main.py run`")


def cmd_run():
    """Stage 2：接管现有标签页（不再导航），读取最终 URL 写回 config，进入投递循环。"""
    dry_run = CONFIG["dry_run"]
    resume = CONFIG["resume"]
    max_jobs = CONFIG["max_jobs_per_run"]
    session_size = CONFIG["max_jobs_per_session"]

    log.info("=" * 60)
    log.info("BOSS直聘自动投递机器人启动")
    log.info(f"dry_run={dry_run}  resume={resume}  max_jobs={max_jobs}")
    log.info("=" * 60)

    try:
        ensure_chrome()
    except Exception as e:
        log.error(str(e))
        sys.exit(1)

    state = StateManager()
    log.info(f"历史已投递: {state.total()} 个职位")

    bot = BossBot()
    try:
        bot.connect(navigate=False)
    except Exception as e:
        log.error(f"连接 Chrome 失败: {e}")
        sys.exit(1)

    # 投递前拒绝跑在未登录页面上
    status = bot.page_status()
    if status["not_logged_in"]:
        log.error("当前页面仍未登录 zhipin.com，请先在浏览器扫码登录后重跑")
        bot.disconnect()
        sys.exit(1)

    # 把用户在浏览器里调整后的最终 URL 回写 config
    bot.sync_url_to_config(status["url"])

    bot.navigate_to_target()

    contacted = 0       # 本次运行已投递数
    skipped = 0         # 跳过数
    failed = 0          # 失败数
    processed_ids = set()  # 本次已处理的 job_id（避免重复点击同一张卡）

    while contacted < max_jobs:
        # 检查验证码：非交互模式下直接退出，让用户在浏览器手动过验证后重跑
        if bot.check_captcha():
            log.warning("检测到验证码，停止本次投递。请在浏览器手动完成验证后重跑 `python3 main.py run`")
            break

        cards = bot.get_job_cards()
        if not cards:
            log.warning("未找到职位卡片，可能页面未加载或选择器失效，退出")
            break

        new_cards_in_batch = 0

        for card in cards:
            if contacted >= max_jobs:
                break

            job_id = bot.get_job_id(card)
            if job_id is None:
                log.debug("无法获取 job_id，跳过此卡片")
                continue

            if job_id in processed_ids:
                continue
            processed_ids.add(job_id)

            # resume 模式：跳过历史已投递
            if resume and state.is_contacted(job_id):
                log.debug(f"[resume] 已投递过 {job_id}，跳过")
                skipped += 1
                continue

            log.info(f"[{contacted+1}/{max_jobs}] 处理职位 {job_id}")

            # 处理上一轮可能残留的反馈/警告弹窗，避免遮挡卡片
            bot._dismiss_warning_dialog()

            # 点击卡片
            ok = bot.click_job_card(card)
            if not ok:
                log.warning(f"点击卡片失败: {job_id}")
                failed += 1
                continue

            new_cards_in_batch += 1

            if dry_run:
                log.info(f"[dry_run] 跳过实际投递: {job_id}")
                human_delay(CONFIG["delay_between_jobs"])
                continue

            # 查找沟通按钮
            if not bot.find_contact_button():
                reason = bot.classify_skip_reason()
                log.info(f"跳过 {job_id} — {reason}")
                skipped += 1
                human_delay((1.0, 2.5))
                continue

            # 检查每日上限
            if bot.check_daily_limit():
                log.warning("已达今日沟通上限，停止投递")
                break

            # 点击"立即沟通" → "留在此页"
            success = bot.click_contact_and_stay()
            if bot._daily_limit_hit:
                log.warning("检测到每日上限（120 弹窗），停止本次投递")
                break
            if success:
                state.mark_contacted(job_id)
                contacted += 1
                log.info(f"成功投递 {job_id}（本次共 {contacted} 个）")
                human_delay(CONFIG["delay_between_jobs"])
            else:
                log.warning(f"投递失败: {job_id}")
                failed += 1
                human_delay((1.5, 3.0))

            # 批间休息
            if contacted > 0 and contacted % session_size == 0:
                log.info(f"已完成 {contacted} 个，进入批间休息")
                session_break()

        # 滚动加载更多
        if contacted >= max_jobs:
            break

        log.info("尝试滚动加载更多职位…")
        has_new = bot.scroll_and_check_new()
        if not has_new:
            log.info("没有更多职位可加载，任务结束")
            break

    log.info("=" * 60)
    log.info(f"任务完成：成功={contacted}  跳过={skipped}  失败={failed}")
    log.info(f"累计已投递: {state.total()} 个职位")
    log.info("=" * 60)

    bot.disconnect()
    # 投递结束自动关闭独立 profile 的 Chrome（cookies 留在 user-data-dir，下次复用）
    close_chrome()


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("prepare", "run"):
        print("用法：python3 main.py prepare   # 拉起浏览器并导航")
        print("      python3 main.py run       # 用户调好筛选后开始投递")
        sys.exit(2)
    if args[0] == "prepare":
        cmd_prepare()
    else:
        cmd_run()


if __name__ == "__main__":
    main()
