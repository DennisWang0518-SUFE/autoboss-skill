# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This repo is a Claude Code **skill** (`boss-autosend`), not a standalone app. The authoritative operating runbook for end-users lives in `SKILL.md` — read it first whenever the user triggers anything BOSS直聘 / 自动投递 / CDP related. The runnable code lives in `scripts/`. This file (CLAUDE.md) is for the **maintainer/dev** view, not the runtime workflow.

## Architecture

Zero-dependency Python automation that drives a real Chrome via Chrome DevTools Protocol over a **raw WebSocket** (no Playwright/Selenium, stdlib only, Python 3.8+).

- `scripts/main.py` — two subcommands: `prepare` (拉起 Chrome + 导航) and `run` (接管标签页 + 投递循环 + 自动关闭 Chrome)
- `scripts/bot.py` — `CDPClient` (raw-WS DevTools client) + `BossBot` (page interaction, card scanning, click/dialog flow, scroll, dialog dismissal)
- `scripts/config.py` — single `CONFIG` dict; all tunables live here. `target_url` is auto-rewritten by `run` based on the user's actual URL after they adjust filters.
- `scripts/state.py` — `StateManager` persists `state.json` (the dedupe ledger of contacted job_ids); enables `resume=True` across runs.
- `scripts/dom_probe.py` — standalone DOM inspector for when selectors break.
- `scripts/logger.py` — logging to `scripts/logs/boss_autosend.log`.

### Two-process lifecycle (CRITICAL — don't merge into one)

```
prepare 进程            run 进程
  ├─ ensure_chrome       ├─ ensure_chrome (复用 9222)
  ├─ navigate            ├─ connect(navigate=False)
  ├─ page_status         ├─ sync_url_to_config (回写 config)
  └─ exit (Chrome 留后台)  ├─ 投递循环
                          ├─ disconnect
                          └─ close_chrome (杀 9222 chrome)
```

- **Chrome process**: launched by `ensure_chrome()` as an isolated profile (`/tmp/chrome_boss_debug`); `close_chrome()` at end of `cmd_run` kills it via `pgrep -f "remote-debugging-port=9222.*<user-data-dir>"` — **double-filter** so we never touch the user's daily Chrome.
- **State sharing across the two processes**: filesystem only. `state.json`, `config.py` (target_url rewritten by run), and the Chrome user-data-dir (cookies/login) all persist.
- **Why split**: `run` blocks for minutes; `prepare` returns immediately so the harness (Claude Code) can show the user the page state and wait for natural-language confirmation ("已筛选") before invoking `run`.

### Key technical traps (each one took a debugging cycle to find)

1. **First card always "fails" on naive snapshot diff**: BOSS preselects card 0 on page load → right panel already shows it → clicking it doesn't change panel content → diff check returns False. Fix in `click_job_card`: if `card.title` already in `old_snapshot`, treat as already-selected and skip the wait. (`bot.py`)

2. **Lazy-load won't trigger from JS scroll** (anti-bot): `window.scrollBy` actually moves `scrollY` but BOSS's IntersectionObserver ignores programmatic scroll events. **Must use `Input.dispatchMouseEvent { type: "mouseWheel" }`** via the raw CDP method (not via JS eval). This is the **only** thing that triggers懒加载. See `scroll_and_check_new()`.

3. **`offsetParent !== null` is broken for `position:fixed` elements** (always returns null). BOSS's dialogs are all fixed-positioned. Use `getComputedStyle().display !== 'none' && getBoundingClientRect().width > 0` instead. See `_dismiss_warning_dialog()`.

4. **120 popup is a SOFT warning, not the hard limit**. BOSS has two stages: at 120 → "还剩 30 次沟通机会哦" (continue investing OK), at 150 → "无法进行沟通...休息一下,明天再来吧" (real hard stop). `check_daily_limit()` matches only hard markers (`今日沟通人数已达上限/沟通次数已用完/明天再来/还剩0次`). Soft warnings are auto-dismissed by `dismiss_soft_warning_if_present()` and the loop continues.

5. **Dialog dismissal must be whitelist-based** to avoid clobbering useful elements (login modals, detail panels). The whitelist is `BossBot._DISMISS_KEYWORDS` — add new keywords there if BOSS rolls out a new popup type. Dismiss strategy: leaf-text strict-match button click (`好/确定/知道了/...`) **+ `d.remove()` fallback** so even if click is intercepted, the DOM node is gone.

6. **Multiple overlapping dialogs**: BOSS sometimes stacks 2-3 popups. `_dismiss_warning_dialog` loops through ALL visible dialogs in one call, not just the first.

## Common commands

```bash
# Stage 1: launch Chrome + navigate, then exit (non-blocking)
cd scripts && python3 main.py prepare

# Stage 2: real投递 (blocking until complete or limit hit; auto-closes Chrome)
cd scripts && python3 main.py run

# Inspect config
cd scripts && python3 -c "from config import CONFIG; print(CONFIG['target_url'], CONFIG['dry_run'], CONFIG['max_jobs_per_run'])"

# Inspect dedupe ledger
cd scripts && python3 -c "from state import StateManager; print(StateManager().total())"

# DOM probe (when selectors break or new popup appears)
cd scripts && python3 dom_probe.py

# Live logs
tail -f scripts/logs/boss_autosend.log
```

No build, no test suite, no linter. Use `python3 -m py_compile main.py bot.py` for a quick syntax check.

## Maintainer rules

- **Always follow the Step 1–4 flow in `SKILL.md`** when invoked at runtime — it's the contract with the user. Don't auto-merge prepare and run.
- **Don't modify `state.json`** — it's the cross-run dedupe ledger.
- When editing `config.py`, edit the `CONFIG` dict in place; the regex in `_write_config_url()` only handles the `"target_url": "..."` line.
- New popup types → add keywords to `BossBot._DISMISS_KEYWORDS`. New dismiss button text → add to the regex `^(好|确定|知道了|...)$` in `_dismiss_warning_dialog()`.
- Selector breakage is the #1 failure mode. Class-level constants are at the top of `BossBot` (`CARD_SELECTOR`, `SCROLL_CONTAINER`); detail panel / contact button selectors are inline in `click_job_card` / `find_contact_button`. `dom_probe.py` is the diagnostic entry point.
- macOS-only by default (`CHROME_PATH_MAC` in `main.py`). Other platforms need to extend `ensure_chrome()`.
