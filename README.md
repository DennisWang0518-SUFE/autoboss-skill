# autoboss-skill

![Python](https://img.shields.io/badge/python-3.8+-blue)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey)
![Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)
![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-8A2BE2)

> 🚀 **这是一个 [Claude Code Skill](https://docs.anthropic.com/claude/docs/claude-code-skills)** —— 装一次，之后只需要在 Claude Code 中说一句「**boss投递**」，就能全自动完成 BOSS直聘的批量沟通。无需打开终端、无需记任何命令、无需写任何代码。
>
> **两步上手**：① 把文件夹拖到 `~/.claude/skills/` ② 在 Claude Code 里说「boss投递」 

BOSS直聘自动投递机器人。底层通过原生 WebSocket 实现 Chrome DevTools Protocol (CDP) 通信，模拟真实手势操作，自动在 BOSS直聘完成批量"立即沟通"。**作为 Claude Code Skill 运行**，由 Claude 按 `SKILL.md` 的流程引导你完成扫码、调筛选、确认、投递、收尾——你只需要用自然语言对话，剩下的全部交给 AI。

## 使用前提

- macOS（其它平台需手改 `main.py` 顶部的 `CHROME_PATH_MAC`）
- 安装了 Google Chrome
- Python 3.8+（macOS 自带，无需额外装）
- 拥有 BOSS直聘账号
- [Claude Code](https://docs.anthropic.com/claude/docs/claude-code) CLI

## 安装

把整个 `boss-autosend` 文件夹**拖动**（或 `cp -r` / `git clone`）到 Claude Code 的 skills 目录：

```
~/.claude/skills/boss-autosend/
```

完整路径长这样：

```
~/.claude/skills/boss-autosend/
├── SKILL.md          ← Claude Code 启动时自动加载
├── README.md
├── CLAUDE.md
├── scripts/
├── references/
└── examples/
```

> 提示：`~` 是你的 home 目录（macOS 上是 `/Users/<你的用户名>`）。如果 `~/.claude/skills/` 不存在，先创建它：`mkdir -p ~/.claude/skills`

安装完成后**重启 Claude Code**（或新开一个对话），让它扫到这个 skill。

## 快速开始

在 Claude Code 里说一句：

> boss投递

Claude 会自动：
1. 拉起独立 profile Chrome（首次需扫码登录，cookies 持久化，下次免登录）
2. 导航到上次的筛选 URL
3. 把当前页面状态汇报给你（URL / 已加载卡片数 / 登录状态 / dry_run 设置 / max_jobs）
4. 等你在浏览器里调好筛选后回复「**已筛选**」
5. 跑投递循环，结束后自动关闭 Chrome、汇报成功/跳过/失败统计

首次使用建议先在 `scripts/config.py` 把 `dry_run` 设为 `True`、`max_jobs_per_run` 设为 5，跑通验证流程后再切换为真实投递。也可以直接告诉 Claude「先跑 dry_run 5 个看看」，它会帮你改 config。

## 文件结构

```
scripts/
├── main.py       # 入口：cmd_prepare / cmd_run + ensure_chrome / close_chrome
├── bot.py        # CDPClient (raw-WS) + BossBot (页面交互、滚动、弹窗处理)
├── config.py     # 单 CONFIG dict
├── state.py      # state.json 持久化
├── logger.py     # 日志
└── dom_probe.py  # DOM 调试工具

SKILL.md          # Claude Code skill 运行手册（给模型看的）
CLAUDE.md         # 维护者视角的架构 + 已知技术陷阱
references/       # CDP 原理 / 选择器调试指南
examples/         # 样例日志 / 样例 state.json
```

## 特性

- **零依赖**：Python 3.8+ 标准库即可运行，无需 pip install、无需 Playwright/Selenium
- **全自动浏览器生命周期**：脚本自己拉起独立 profile 的 Chrome、自己接管标签页、跑完自己关闭，**绝不影响你日常的 Chrome**
- **自动识别+关闭弹窗**：温馨提示、120 软警告、150 硬上限、已发送反馈等都会被自动识别并关掉，投递循环不中断
- **CDP 真实手势滚动**：用 `Input.dispatchMouseEvent` 模拟真鼠标滚轮（绕过 BOSS 对 JS scroll 的反爬），可靠触发懒加载
- **防重复投递**：内存去重 + `state.json` 跨次去重持久化
- **人类行为模拟**：随机延迟、批间长暂停、Cookie 持久化（不用重复扫码）
- **可调试**：`dry_run` 干跑模式 / `dom_probe.py` DOM 探查工具 / 滚动文件日志

## 配置参数（`config.py`）

| 参数 | 说明 |
|---|---|
| `target_url` | zhipin 职位列表页 URL，run 会自动用浏览器当前 URL 覆盖 |
| `cdp_endpoint` | 默认 `http://localhost:9222` |
| `max_jobs_per_run` | 单次 run 投递上限。首次试跑 5–20，常规 50–150 |
| `max_jobs_per_session` | 每达到这个数量触发一次 `session_break` 长暂停 |
| `delay_between_jobs` | 卡片间延迟区间（秒） |
| `delay_after_contact` | 点击立即沟通后等待区间（秒） |
| `session_break` | 批间长暂停区间（秒） |
| `dry_run` | True=只点不投递 / False=真实发起沟通 |
| `resume` | True=跳过 state.json 里的历史 job_id |
| `page_timeout` / `element_timeout` / `dialog_timeout` | 各类等待超时（毫秒） |

## 注意事项

- BOSS直聘的硬上限是每日 **150 次沟通**，120 时会弹软警告（"还剩 30 次"），脚本会自动处理两种情况
- 触发硬上限后脚本会自动关闭浏览器、汇总成功数、退出，明天 0 点重置
- 请合理设置投递数量、保持随机延迟，避免触发风控
- BOSS直聘前端可能更新导致选择器失效。最常见的失效点是职位卡片选择器（`.job-card-wrap`）和滚动容器。先跑 `python3 dom_probe.py` 排查，参考 `references/selector-debugging.md`
- **`state.json` 是去重账本，请勿删除**，否则会重复投递历史已沟通过的 BOSS
- 维护者请阅读 `CLAUDE.md`——里面记录了 6 个已踩过的技术陷阱（首张卡片预选 / mouseWheel 反爬 / fixed offsetParent / 软硬上限区分 / 弹窗白名单 / 多弹窗叠加）

## 故障排查

| 症状 | 解法 |
|---|---|
| 连接 Chrome 失败 / 502 | `pkill -f "remote-debugging-port=9222"` 然后重跑 `python3 main.py prepare` |
| Chrome 启动后 502 | 删 `/tmp/chrome_boss_debug` 后重跑 |
| 未找到职位卡片 | 跑 `python3 dom_probe.py`，查看选择器是否变化 |
| 滚动停在某个数 | 之前 BOSS 的反爬会拦截 JS scroll，已用 CDP `Input.dispatchMouseEvent` 修复；如果重现说明 BOSS 又改了，检查 `scroll_and_check_new()` |
| 检测到验证码停止 | 在浏览器手动完成验证后重跑 `python3 main.py run` |
| 弹窗未自动关闭 | 把弹窗内容关键字加到 `bot.py` 的 `BossBot._DISMISS_KEYWORDS` 元组 |

## 免责声明

本项目仅供**个人学习与技术研究**使用，旨在探索 Chrome DevTools Protocol 的自动化能力。使用者须自行：

- 遵守 [BOSS直聘用户协议](https://www.zhipin.com/) 及相关法律法规
- 控制投递频率，避免对目标站点造成负担
- 承担因使用本工具产生的全部风险（包括但不限于账号风控、封禁等）

作者不对任何使用行为及其后果负责。如本项目侵犯了相关方权益，请提 issue，将第一时间下架。

## License

MIT
