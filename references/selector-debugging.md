# 选择器调试指南

BOSS直聘前端会不定期更新，导致硬编码的 CSS 选择器失效。本指南说明如何快速定位新选择器并修复。

## 症状识别

| 报错/现象 | 可能失效的选择器 |
|-----------|-----------------|
| "未找到职位卡片，退出" | `CARD_SELECTOR = ".job-card-wrap"` |
| 所有职位都跳过，显示"无沟通按钮" | `.op-btn-chat` 或 `[class*="btn-chat"]` |
| 点击卡片后面板未刷新 | 详情面板选择器（`.job-detail-box` 等） |
| 滚动后未加载新卡片 | `SCROLL_CONTAINER = ".job-list-container"` |

## 第一步：运行 dom_probe.py

```bash
python3 dom_probe.py
```

输出示例：
```
[+] 找到标签页: https://www.zhipin.com/web/geek/jobs?...

=== 含 job/card 的 class 名 ===
  .job-card-body
  .job-card-left
  .job-card-wrap        ← 如果这里有，说明卡片选择器没变
  .job-card-wrapper     ← 如果这里出现但上面的没有，说明选择器变了

=== 候选卡片选择器匹配数 ===
  '.job-card-wrapper'   → 30  <-- 推荐
  '.job-card-box'       → 0
  '.job-card'           → 0

=== 候选沟通按钮选择器 ===
  "[class*='btn-chat']" → 1   ← 有匹配，说明按钮选择器仍有效
  ".btn-startchat"      → 0
```

根据输出找到匹配数量 > 0 的选择器，即为当前有效选择器。

## 第二步：更新 bot.py

找到新选择器后，更新以下位置：

**卡片选择器**（两处）：
```python
# bot.py 顶部
class BossBot:
    CARD_SELECTOR = ".job-card-wrap"      # ← 改这里
    SCROLL_CONTAINER = ".job-list-container"  # ← 改这里
```

**详情面板选择器**（多处硬编码在 JS 字符串中）：
```python
# 搜索 bot.py 中的这个字符串并更新：
'.job-detail-box, .job-detail-card, .detail-box, .job-detail'
```

**沟通按钮选择器**（`find_contact_button` 和 `click_contact_and_stay` 中）：
```python
const sels = ['.op-btn-chat', '[class*="btn-chat"]'];
```

## 第三步：用 dry_run 验证

修改选择器后，先在 `config.py` 中设置 `dry_run: True` 运行一次：

```bash
python3 main.py
```

观察日志输出：
- `找到 30 个职位卡片` → 卡片选择器正常
- `[dry_run] 跳过实际投递: xxx` → 脚本在正常遍历卡片
- `未找到职位卡片，退出` → 卡片选择器仍然失效

验证通过后，将 `dry_run` 改回 `False` 正式运行。

## 进阶：手动执行 JS 调试

如果 dom_probe.py 的预设选择器都不匹配，可以在 Chrome DevTools Console 手动探查：

```javascript
// 在 zhipin.com 页面打开 DevTools → Console，输入：

// 找所有带 job 关键词的 class
Array.from(document.querySelectorAll('[class]'))
  .flatMap(el => el.className.split(' '))
  .filter(c => c.includes('job') || c.includes('card'))
  .filter((c, i, arr) => arr.indexOf(c) === i)  // 去重
  .sort()

// 测试某个选择器
document.querySelectorAll('.your-selector').length

// 找"立即沟通"按钮的实际 class
Array.from(document.querySelectorAll('button'))
  .filter(btn => btn.textContent.includes('立即沟通'))
  .map(btn => btn.className)
```

找到正确选择器后，同步更新 dom_probe.py 的候选列表（`selectors` 和 `btn_selectors`），方便下次快速检测。

## 常见变更模式

BOSS直聘历史上的选择器变更通常有规律：
- `job-card-wrap` → `job-card-wrapper` → `job-card-box`（class 后缀变化）
- `op-btn-chat` → `btn-chat` → `startchat-btn`（前缀顺序变化）
- 详情面板的 class 变化相对较少，通常保留 `job-detail` 关键词

优先检查包含关键词的属性选择器（如 `[class*="job-card"]`），比精确 class 名更耐改版。
