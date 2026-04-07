---
name: boss-autosend
description: BOSS直聘自动投递机器人的使用与维护 skill。**触发关键词：「boss投递」**。当用户说出"boss投递"、"BOSS投递"、"跑 boss 投递"或类似表达时必须激活；维护场景（提到 bot.py / config.py / dom_probe.py / 选择器调试 / CDP）也激活。
---

# BOSS直聘自动投递机器人

通过原生 WebSocket 实现 Chrome DevTools Protocol (CDP) 通信，自动在 BOSS直聘完成批量"立即沟通"操作。**无第三方依赖，Python 3.8+ 即可运行。**

> **源码位置**：skill 根目录的 `scripts/` 子目录。执行任何命令前先用 Glob/ls 确认其绝对路径，下文用 `<S>` 占位。

---

## 进程模型 & 持久化

`prepare` 和 `run` 是**两个独立的 Python 进程**（不是同一个长跑进程的两个阶段）。它们之间的状态通过磁盘共享：
- **Chrome 进程**：`prepare` 启动 Chrome 后退出，9222 端口持续监听等待 `run` 接管；**`run` 完成（含正常结束/触发上限/异常）会自动关闭它**（仅杀我们启动的独立 profile，不影响日常 Chrome）
- **Cookies/登录态**：保存在 `/tmp/chrome_boss_debug` user-data-dir 里跨次复用，下次 `prepare` 不用重新扫码
- **state.json**：去重账本，记录历史投递的 job_id，**两次 run 之间持久化**，所以 `resume=True` 跨调用工作正常
- **config.py**：用户在浏览器调整筛选后，`run` 启动时会把最终 URL 自动写回 `target_url`

## 每次触发的完整流程

### Step 1：Prepare —— 拉起浏览器并报告页面+配置状态

执行**两条命令**（一条拉起浏览器，一条读 dry_run，省一次往返）：

```bash
cd <S> && python3 main.py prepare
cd <S> && python3 -c "from config import CONFIG; print(f'dry_run={CONFIG[\"dry_run\"]} max_jobs={CONFIG[\"max_jobs_per_run\"]}')"
```

`prepare` 会做：检测 9222 端口 → 未开则用独立 profile（`/tmp/chrome_boss_debug`）启动 Chrome → 接管或新建 zhipin 标签页 → 导航到 `config.py` 的 `target_url` → 打印一个 `[PREPARE]` 状态块后**自行退出**（不阻塞）。

输出形如：

```
[PREPARE] 浏览器已就绪
  当前 URL : https://www.zhipin.com/web/geek/jobs?...
  已加载卡片: 12
  登录状态  : 未登录（需扫码） / 已登录
```

**模型必须从输出中提取**：URL、卡片数、登录状态、dry_run、max_jobs；原样汇报给用户，并按以下分支引导：

- **未登录** → "请在弹出的 Chrome 窗口扫码登录 zhipin.com，然后调整筛选条件（城市/薪资/经验/学历/公司规模等），调好后回复『已筛选』。"
- **已登录** → "请在 Chrome 窗口里调整筛选条件（城市/薪资/经验/学历/公司规模等），调好后回复『已筛选』。"

同时根据 dry_run 提示模式：

- **dry_run=True** → "本次将以**安全模式**运行（只点击不投递）"
- **dry_run=False** → "⚠️ 本次将**真实发起沟通**（最多 N 个，N=max_jobs_per_run）"

如果用户想改 `max_jobs_per_run` 或切换 `dry_run`，用 Edit 改 `<S>/config.py` 里对应那行。

**然后停下来等用户回复。禁止自动推进到 Step 2。**

---

### Step 2：等用户确认筛选完成

确认信号（任一即可）：「已筛选」「ok」「好了」「开始」「投递吧」「go」。

非确认信号（**继续等**，不要 run）：「等等」「再加一个学历」「先不要」「先看看」。

收到确认后：
- 若 **dry_run=True** → 直接进入 Step 3
- 若 **dry_run=False** → **必须再追问一次**："⚠️ 即将真实投递最多 N 个职位、不可逆，确认开跑吗？" 用户明确同意后才进入 Step 3

---

### Step 3：Run —— 开始投递

执行：

```bash
cd <S> && python3 main.py run
```

`run` 会做：复用 9222 端口的 Chrome → 接管已打开的 zhipin 标签页（**不会重新导航**，保留用户调好的页面）→ 再做一次登录检测，未登录直接报错退出 → 把当前 URL 写回 `config.py` 的 `target_url` → 进入投递循环 → 跑完 / 触发上限 / 验证码后退出。

---

### Step 4：解读结果并汇报

日志最后会有汇总行：`任务完成：成功=X  跳过=Y  失败=Z` 和 `累计已投递: N 个职位`。

按 dry_run 区分汇报：

- **dry_run=True 通过**（日志含至少一次 `[dry_run] 跳过实际投递`，无 `未找到职位卡片`）：
  > "干跑测试通过，扫描了 X 个职位，选择器正常。是否切换到真实投递？"
  - 用户同意 → 用 Edit 把 `config.py` 中 `dry_run` 改为 `False`，**回到 Step 2 重新询问二次确认**（不要直接 run）
  - 用户拒绝 → 结束
- **dry_run=False 完成** → 报告 `成功投递: X | 跳过: Y | 失败: Z | 累计: N`

**异常分支**：

| 日志特征 | 含义 | 应对 |
|---|---|---|
| `检测到每日上限（120 弹窗）` 或 `检测到每日上限提示` | 已达 BOSS 当日 120 条沟通上限，是**正常硬停**，不是失败 | 告诉用户："今日已达 BOSS 沟通上限，明天再试。本次成功投递 X 个" |
| `检测到验证码，停止本次投递` | 触发了行为验证 | 告诉用户："请在浏览器里手动完成验证，然后回复『继续』，我再跑一次 `python3 main.py run`" |
| `当前页面仍未登录` 退出 | `run` 启动时登录态丢失 | 告诉用户重新登录，然后**回到 Step 1 重跑 prepare**（不要直接 run，因为标签页可能已经在登录页） |
| `未找到职位卡片` warning | 选择器可能失效 | 引导用户运行 `python3 dom_probe.py`，参考 `references/selector-debugging.md` |

---

## 状态机速查（给模型）

```
用户触发
   ↓
Step 1: prepare + 读 dry_run        ← 一次性，浏览器拉起并退出
   ↓
   汇报 [PREPARE]+模式 + 引导用户调筛选
   ↓
Step 2: 等待『已筛选』                ← 必须停在这里
   ↓
   dry_run=False? → 二次确认
   ↓
Step 3: python3 main.py run         ← 阻塞至循环结束
   ↓
Step 4: 解读日志
   ├─ dry_run=True 通过 → 用户同意切真投递 → 改 config → 回到 Step 2
   ├─ dry_run=False 完成 → 结束
   └─ 异常（120/验证码/未登录/选择器失效）→ 按对应分支处理
```

**禁止的反模式**：
- ❌ 跑 prepare 后立刻跑 run
- ❌ 用户未明确同意时跑 run
- ❌ dry_run=False 时跳过二次确认
- ❌ 把 prepare 和 run 拼成 `prepare && run` 一次执行
- ❌ 把 120 上限或验证码硬停误判为"失败"

---

## 配置参数 (config.py)

> 当前默认值以 `config.py` 为准（用 `python3 -c "from config import CONFIG; print(CONFIG)"` 可读取）。下表只描述每个参数的语义。

| 参数 | 说明 |
|------|------|
| `target_url` | zhipin 职位列表页 URL（含筛选参数）。`run` 启动时会自动用浏览器当前 URL 覆盖它。 |
| `cdp_endpoint` | Chrome DevTools 协议端点，默认 `http://localhost:9222` |
| `max_jobs_per_run` | 单次 `run` 的投递上限。首次试跑建议设小（5–20）；常规使用 50–150 |
| `max_jobs_per_session` | 每达到这个数量触发一次 `session_break` 长暂停 |
| `delay_between_jobs` | 卡片间随机延迟区间（秒），如 `(2.0, 5.0)` |
| `delay_after_contact` | 点击"立即沟通"后的等待区间（秒） |
| `session_break` | 批间长暂停区间（秒） |
| `dry_run` | True=只点不投递（安全验证）；False=真实发起沟通 |
| `resume` | True=跳过 `state.json` 里已投递的 job_id（推荐保持 True） |
| `page_timeout` / `element_timeout` / `dialog_timeout` | 各类等待超时（毫秒），网络慢时调大 |

---

## 关键选择器

| 用途 | 选择器 |
|------|--------|
| 职位卡片 | `.job-card-wrap` |
| 滚动容器 | `.job-list-container` |
| 沟通按钮 | `.op-btn-chat` 或 `[class*="btn-chat"]` |
| 详情面板 | `.job-detail-box, .job-detail-card, .detail-box, .job-detail` |

**BOSS直聘前端会不定期更新，选择器可能失效。** 遇到问题先运行 `dom_probe.py`，参考 `references/selector-debugging.md`。

---

## 常见故障

| 症状 | 解法 |
|------|------|
| 连接 Chrome 失败 / 502 | 先 `pkill -f "remote-debugging-port=9222"`，再重跑 `python3 main.py prepare` |
| Chrome 启动后仍然 502 | 关闭所有 Chrome 窗口，删除 `/tmp/chrome_boss_debug`，再重跑 `python3 main.py prepare` |
| Chrome 路径不存在 | 装在非默认路径时，修改 `main.py` 顶部的 `CHROME_PATH_MAC` |
| 未找到职位卡片 | 确认浏览器在正确页面；运行 `python3 dom_probe.py` |
| 所有职位都跳过 | 改 `dry_run: True` + 跑 `python3 dom_probe.py` 确认按钮选择器 |
| 弹窗超时 | 增大 `dialog_timeout`；检查"留在此页"文案是否变化 |
| `检测到验证码，停止本次投递` | 在浏览器手动完成验证后，让用户回复『继续』，再跑 `python3 main.py run` |
| `检测到每日上限（120 弹窗）` | BOSS 当日 120 条沟通上限，是正常硬停，告诉用户明天再试 |
| 重复投递 | 保持 `resume: True`；勿删除 `state.json` |

---

## 调试速查

```bash
# 探查 DOM（选择器失效时）
cd <S> && python3 dom_probe.py

# 查看已投递数量
cd <S> && python3 -c "from state import StateManager; print(StateManager().total())"

# 实时日志
tail -f <S>/logs/boss_autosend.log
```

---

## 延伸阅读

- `references/cdp-basics.md` — CDP 协议原理、WebSocket 握手、消息格式
- `references/selector-debugging.md` — 选择器失效完整修复流程
- `examples/sample-log.txt` — 典型运行日志（成功/跳过/失败场景）
- `examples/sample-state.json` — state.json 文件结构
