import re
import time
import random
import json
import os
import pathlib
import base64
import struct
import socket
import urllib.request
import urllib.parse
from typing import Optional, List, Any

from config import CONFIG
from logger import get_logger

log = get_logger()


# ---------------------------------------------------------------------------
# 原生 WebSocket CDP 客户端 — 关闭时不触发任何 Chrome 页面生命周期事件
# ---------------------------------------------------------------------------

class CDPClient:
    def __init__(self, ws_url: str, timeout: int = 15):
        parts = ws_url.replace("ws://", "").split("/", 1)
        host_port = parts[0].split(":")
        host = host_port[0]
        port = int(host_port[1]) if len(host_port) > 1 else 9222
        path = "/" + parts[1]

        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._buf = b""
        self._msg_id = 0

        key = base64.b64encode(os.urandom(16)).decode()
        hs = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        self._sock.sendall(hs.encode())
        raw = b""
        while b"\r\n\r\n" not in raw:
            raw += self._sock.recv(4096)
        if b"101" not in raw:
            raise ConnectionError("WebSocket 握手失败")
        self._buf = raw[raw.index(b"\r\n\r\n") + 4:]

    def _rx(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("CDP 连接断开")
            self._buf += chunk
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def _send(self, obj: dict):
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        n = len(data)
        if n < 126:
            hdr = struct.pack(">BB", 0x81, 0x80 | n) + mask
        elif n < 65536:
            hdr = struct.pack(">BBH", 0x81, 0xFE, n) + mask
        else:
            hdr = struct.pack(">BBQ", 0x81, 0xFF, n) + mask
        self._sock.sendall(hdr + masked)

    def _recv_frame(self) -> dict:
        b0, b1 = self._rx(2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._rx(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._rx(8))[0]
        return json.loads(self._rx(n))

    def _call(self, method: str, params: dict = None, timeout: int = 15) -> Any:
        self._msg_id += 1
        msg_id = self._msg_id
        self._send({"id": msg_id, "method": method, "params": params or {}})
        old = self._sock.gettimeout()
        self._sock.settimeout(timeout)
        try:
            while True:
                msg = self._recv_frame()
                if msg.get("id") == msg_id:
                    if "error" in msg:
                        raise RuntimeError(f"CDP error: {msg['error']}")
                    return msg.get("result", {})
        finally:
            self._sock.settimeout(old)

    def eval(self, expression: str, timeout: int = 10) -> Any:
        """执行 JS 表达式，返回 Python 值（returnByValue）。"""
        result = self._call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": False,
        }, timeout=timeout)
        r = result.get("result", {})
        if r.get("subtype") == "error":
            raise RuntimeError(f"JS 执行错误: {r.get('description')}")
        if r.get("type") == "undefined":
            return None
        return r.get("value")

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# BossBot
# ---------------------------------------------------------------------------

class BossBot:
    CARD_SELECTOR = ".job-card-wrap"
    SCROLL_CONTAINER = ".job-list-container"

    def __init__(self):
        self._cdp: Optional[CDPClient] = None
        self._daily_limit_hit = False  # 由 check_daily_limit / click_contact_and_stay 设置

    # ------------------------------------------------------------------
    # 连接 & 初始化
    # ------------------------------------------------------------------
    def connect(self, navigate: bool = True):
        """接管或新建 zhipin 标签页。
        navigate=True：若不在 target_url 则跳过去（Stage 1 prepare 用）
        navigate=False：仅接管现有标签页，不动 URL（Stage 2 run 用）"""
        endpoint = CONFIG["cdp_endpoint"]
        target_url = CONFIG["target_url"]

        def list_tabs():
            resp = urllib.request.urlopen(f"{endpoint}/json", timeout=5)
            return json.loads(resp.read())

        tabs = list_tabs()
        tab = next((t for t in tabs if "zhipin.com" in t.get("url", "")), None)

        if tab is None:
            # 新建一个 zhipin 标签页
            log.info("未发现 zhipin 标签页，新建一个…")
            req = urllib.request.Request(
                f"{endpoint}/json/new?{urllib.parse.quote(target_url, safe='')}",
                method="PUT",
            )
            try:
                urllib.request.urlopen(req, timeout=5)
            except Exception:
                # 旧版 Chrome 可能不允许 PUT，退回 GET
                urllib.request.urlopen(
                    f"{endpoint}/json/new?{urllib.parse.quote(target_url, safe='')}",
                    timeout=5,
                )
            time.sleep(1.0)
            tabs = list_tabs()
            tab = next((t for t in tabs if "zhipin.com" in t.get("url", "")), None)
            if tab is None:
                raise RuntimeError("新建 zhipin 标签页失败")

        self._cdp = CDPClient(tab["webSocketDebuggerUrl"])
        self._tab_url = tab["url"]
        log.info(f"已接管标签页: {self._tab_url}")

        # 仅 prepare 阶段才允许导航；run 阶段必须保留用户调好的页面
        if navigate and target_url and target_url not in self._tab_url:
            log.info(f"导航到 target_url: {target_url}")
            self._cdp._call("Page.enable")
            self._cdp._call("Page.navigate", {"url": target_url})

        # 等待 document.readyState === 'complete'
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                state = self._cdp.eval("document.readyState")
                if state == "complete":
                    break
            except Exception:
                pass
            time.sleep(0.5)
        # 刷新 _tab_url
        try:
            self._tab_url = self._cdp.eval("location.href") or self._tab_url
        except Exception:
            pass

    def page_status(self) -> dict:
        """读取页面当前状态（URL、卡片数、是否未登录）。
        会先等 body 内容稳定，规避 zhipin 的 _security_check 重定向。"""
        deadline = time.time() + 6
        while time.time() < deadline:
            body_len = self._cdp.eval("document.body && document.body.innerText.length") or 0
            if body_len > 50:
                break
            time.sleep(0.5)
        return {
            "url": self._cdp.eval("location.href"),
            "cards": self._cdp.eval(
                f"document.querySelectorAll('{self.CARD_SELECTOR}').length"
            ) or 0,
            "not_logged_in": bool(self._cdp.eval(
                "(document.body.innerText || '').includes('登录/注册')"
            )),
        }

    def sync_url_to_config(self, current_url: str):
        """如果用户在浏览器里改了筛选，把最新 URL 写回 config.py 的 target_url。"""
        if current_url and current_url != CONFIG.get("target_url"):
            try:
                self._write_config_url(current_url)
                log.info(f"已更新 config.py → target_url = {current_url}")
            except Exception as e:
                log.warning(f"回写 config.py 失败: {e}")
        self._tab_url = current_url or self._tab_url

    def _write_config_url(self, new_url: str):
        path = pathlib.Path(__file__).parent / "config.py"
        text = path.read_text(encoding="utf-8")
        new_text = re.sub(
            r'("target_url"\s*:\s*)"[^"]*"',
            lambda m: f'{m.group(1)}"{new_url}"',
            text,
            count=1,
        )
        path.write_text(new_text, encoding="utf-8")

    def disconnect(self):
        if self._cdp:
            self._cdp.close()
            self._cdp = None

    def navigate_to_target(self):
        log.info(f"当前页面: {self._tab_url}")
        # 页面加载时第一张卡片会被自动预选，点击空白区重置预选状态
        # 否则 click_job_card 对第 0 张卡的"面板变化"检测会永远失败
        self._cdp.eval("document.body.click()")
        time.sleep(0.5)

    # ------------------------------------------------------------------
    # 职位卡片
    # ------------------------------------------------------------------
    def get_job_cards(self) -> List[dict]:
        """返回所有卡片信息列表，每项含 index / href / title。"""
        deadline = time.time() + 8
        while time.time() < deadline:
            count = self._cdp.eval(
                f"document.querySelectorAll('{self.CARD_SELECTOR}').length"
            )
            if count and count > 0:
                break
            time.sleep(0.5)
        else:
            log.warning(f"等待职位卡片超时 (selector={self.CARD_SELECTOR})")
            return []

        cards = self._cdp.eval(f"""
            (() => {{
                const cards = document.querySelectorAll('{self.CARD_SELECTOR}');
                return [...cards].map((card, i) => {{
                    const a = card.querySelector('a');
                    const href = a ? a.getAttribute('href') : '';
                    const title = (card.querySelector('.job-title, .job-name') || {{}}).textContent || '';
                    return {{ index: i, href: href, title: title.trim() }};
                }});
            }})()
        """)
        log.debug(f"找到 {len(cards) if cards else 0} 个职位卡片")
        return cards or []

    def get_job_id(self, card: dict) -> Optional[str]:
        """从卡片 href 提取唯一 job_id。"""
        href = card.get("href", "")
        m = re.search(r"/job_detail/([^/?#]+?)(?:\.html)?(?:[/?#]|$)", href)
        if m:
            return m.group(1)
        return href.split("?")[0].strip("/") or None

    def click_job_card(self, card: dict) -> bool:
        """点击职位卡片，等待右侧详情面板刷新为新内容。"""
        try:
            idx = card["index"]

            # 记录点击前面板内容（用于检测是否已刷新）
            old_snapshot = self._cdp.eval("""
                (() => {
                    const panel = document.querySelector(
                        '.job-detail-box, .job-detail-card, .detail-box, .job-detail'
                    );
                    return panel ? panel.innerText.slice(0, 200) : '';
                })()
            """) or ''

            # Bug 1 修复：BOSS 加载后第一张卡片是预选状态，右侧面板已显示它。
            # 此时点击它面板内容不变，会被误判为失败。用卡片标题兜底识别。
            title = (card.get("title") or "").strip()
            if title and len(title) >= 2 and title in old_snapshot:
                log.debug(f"卡片已是当前选中状态（panel 已含 '{title}'），跳过点击等待")
                return True

            self._cdp.eval(f"""
                (() => {{
                    const cards = document.querySelectorAll('{self.CARD_SELECTOR}');
                    const card = cards[{idx}];
                    if (card) {{
                        card.scrollIntoView({{block: 'center', behavior: 'instant'}});
                        const a = card.querySelector('a');
                        if (a) a.click(); else card.click();
                    }}
                }})()
            """)
            time.sleep(random.uniform(0.3, 0.8))

            # 等待面板内容实际更新（不只是存在）
            deadline = time.time() + CONFIG["element_timeout"] / 1000
            while time.time() < deadline:
                new_snapshot = self._cdp.eval("""
                    (() => {
                        const panel = document.querySelector(
                            '.job-detail-box, .job-detail-card, .detail-box, .job-detail'
                        );
                        return panel ? panel.innerText.slice(0, 80) : '';
                    })()
                """) or ''
                if new_snapshot and new_snapshot != old_snapshot:
                    time.sleep(random.uniform(0.3, 0.6))
                    return True
                time.sleep(0.3)

            log.warning("点击职位卡片后右侧面板未刷新")
            return False
        except Exception as e:
            log.warning(f"click_job_card 异常: {e}")
            return False

    # ------------------------------------------------------------------
    # 沟通按钮
    # ------------------------------------------------------------------
    def find_contact_button(self) -> bool:
        """
        等待右侧面板加载，检测"立即沟通"按钮是否可用。
        True = 可点击投递，False = 跳过（已沟通/无按钮/禁用）。
        """
        deadline = time.time() + CONFIG["element_timeout"] / 1000
        while time.time() < deadline:
            result = self._cdp.eval("""
                (() => {
                    const panel = document.querySelector(
                        '.job-detail-box, .job-detail-card, .detail-box, .job-detail'
                    );
                    if (!panel) return null;

                    if (panel.innerText.includes('继续沟通')) return 'contacted';

                    const sels = ['.op-btn-chat', '[class*="btn-chat"]'];
                    for (const sel of sels) {
                        const btn = panel.querySelector(sel) || document.querySelector(sel);
                        if (btn) {
                            const disabled = btn.disabled
                                || btn.getAttribute('disabled') !== null
                                || btn.className.includes('disabled')
                                || window.getComputedStyle(btn).pointerEvents === 'none';
                            return disabled ? 'disabled' : 'available';
                        }
                    }
                    return 'no-button';
                })()
            """)
            if result == 'contacted':
                log.debug('已沟通（右侧面板显示"继续沟通"），跳过')
                return False
            elif result == 'available':
                return True
            elif result in ('disabled', 'no-button'):
                log.debug(f'按钮状态: {result}')
                return False
            # result 为 None 说明面板还未加载，继续等待
            time.sleep(0.3)

        log.debug('等待右侧面板内容超时')
        return False

    def click_contact_and_stay(self) -> bool:
        """点击"立即沟通" → 等待弹窗 → 点击"留在此页"。"""
        try:
            self._cdp.eval("""
                (() => {
                    const sels = ['.op-btn-chat', '[class*="btn-chat"]'];
                    for (const sel of sels) {
                        const btn = document.querySelector(sel);
                        if (btn) { btn.click(); return; }
                    }
                })()
            """)
            log.debug('已点击"立即沟通"，等待弹窗…')
            time.sleep(random.uniform(0.5, 1.0))

            deadline = time.time() + CONFIG["dialog_timeout"] / 1000
            while time.time() < deadline:
                found = self._cdp.eval("document.body.innerText.includes('留在此页')")
                if found:
                    break
                # 同步检查硬上限
                if self.check_daily_limit():
                    log.warning("点击'立即沟通'后触发硬上限，停止投递")
                    return False
                # 软警告（"还剩 N 次"）：自动关闭后继续等"留在此页"
                self.dismiss_soft_warning_if_present()
                time.sleep(0.3)
            else:
                log.warning('等待"留在此页"超时，弹窗未出现')
                self._try_dismiss_dialog()
                return False

            time.sleep(random.uniform(0.3, 0.8))
            self._cdp.eval("""
                (() => {
                    const all = [...document.querySelectorAll('button, a, span, div')];
                    const btn = all.find(el => el.textContent.trim() === '留在此页');
                    if (btn) btn.click();
                })()
            """)
            log.debug('已点击"留在此页"')
            time.sleep(random.uniform(*CONFIG["delay_after_contact"]))
            return True

        except Exception as e:
            log.warning(f"click_contact_and_stay 异常: {e}")
            self._try_dismiss_dialog()
            return False

    def _try_dismiss_dialog(self):
        try:
            self._cdp.eval("""
                (() => {
                    const texts = ['取消', '关闭'];
                    const all = [...document.querySelectorAll('button, a, span')];
                    for (const t of texts) {
                        const btn = all.find(el => el.textContent.trim() === t);
                        if (btn) { btn.click(); return; }
                    }
                })()
            """)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 上限检测 & 验证码
    # ------------------------------------------------------------------
    # 已知"应当被自动关闭"的弹窗内容关键字（白名单）
    _DISMISS_KEYWORDS = (
        "温馨提示",          # "您今天已与120位BOSS沟通..."
        "还剩",              # 软警告 "还剩 N 次沟通机会"
        "已向BOSS发送消息",  # greet-boss-dialog 反馈
        "已向boss发送消息",
        "已沟通",            # 各种"已沟通过"提示
        "今日已与",
        "今天已与",          # "您今天已与150位boss沟通"
        "无法进行沟通",       # 硬上限弹窗
        "明天再来",          # 硬上限弹窗
        "休息一下",          # 硬上限弹窗
    )

    def _dismiss_warning_dialog(self) -> int:
        """关闭所有可见的 BOSS 提示/反馈弹窗。**白名单制**——只关内容含已知关键字的，
        避免误关登录弹窗、详情面板等有用元素。
        - 用 getComputedStyle 判断可见（offsetParent 对 fixed 元素失效）
        - 循环遍历，叠多个全部清掉
        - 优先点击叶子节点文本 '好/知道了/...'
        - 兜底：d.remove() 整个 dialog 节点
        返回关闭数量。
        """
        try:
            keywords_js = "[" + ",".join(f'"{k}"' for k in self._DISMISS_KEYWORDS) + "]"
            count = self._cdp.eval(f"""
                (() => {{
                    const KEYWORDS = {keywords_js};
                    const isVisible = (el) => {{
                        const s = window.getComputedStyle(el);
                        const r = el.getBoundingClientRect();
                        return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
                    }};
                    const BTN_RE = /^(好|确定|知道了|我知道了|确认|好的|关闭|取消)$/;
                    const dlgs = document.querySelectorAll(
                        '.dialog-wrap, .boss-dialog, .modal, [class*="dialog"], [class*="Dialog"], [class*="modal"], [class*="Modal"]'
                    );
                    let dismissed = 0;
                    for (const d of dlgs) {{
                        if (!isVisible(d)) continue;
                        const text = (d.innerText || '').trim();
                        // 白名单：必须含至少一个关键字才关
                        if (!KEYWORDS.some(k => text.includes(k))) continue;
                        // 优先点击 "好/知道了/..." 按钮
                        const all = [...d.querySelectorAll('*')];
                        let btn = all.find(el =>
                            el.children.length === 0 && BTN_RE.test((el.textContent || '').trim())
                        );
                        if (btn) {{
                            // 沿父链找可点击祖先
                            let p = btn;
                            while (p && p !== d) {{
                                if (p.tagName === 'BUTTON' || p.tagName === 'A' || p.onclick || p.getAttribute('role') === 'button') {{
                                    btn = p; break;
                                }}
                                p = p.parentElement;
                            }}
                            try {{ btn.click(); }} catch (e) {{}}
                        }}
                        // 兜底：移除节点（无论 click 成功与否）
                        try {{ d.remove(); }} catch (e) {{}}
                        dismissed++;
                    }}
                    return dismissed;
                }})()
            """) or 0
            if count:
                log.info(f"关闭了 {count} 个提示弹窗")
            return count
        except Exception:
            return 0

    def check_daily_limit(self) -> bool:
        """检测**真正的硬上限**（已达 150 次或类似表述）。
        注意：BOSS 在 120 次时会弹一个软警告 "还剩 30 次沟通机会哦"，那不是硬停，
        会被 dismiss_soft_warning_if_present() 处理。本函数只匹配真正的"已达上限"。"""
        try:
            body = self._cdp.eval("document.body.innerText") or ""
            hard_markers = [
                "今日沟通人数已达上限",
                "沟通次数已用完",
                "明天再来",
                "还剩0次",
                "还剩 0 次",
            ]
            if any(t in body for t in hard_markers):
                log.warning("检测到每日硬上限")
                self._daily_limit_hit = True
                self._dismiss_warning_dialog()
                return True
        except Exception:
            pass
        return False

    def dismiss_soft_warning_if_present(self) -> bool:
        """检测并关闭 BOSS 的软警告（'还剩 N 次沟通机会哦'，N>0）。
        N>0 表示还能继续投递，关掉后正常进行；N=0 走 check_daily_limit 的硬停路径。
        BOSS 弹窗是 position:fixed，必须用 getComputedStyle 判断可见性。"""
        try:
            warning_text = self._cdp.eval("""
                (() => {
                    const isVisible = (el) => {
                        const s = window.getComputedStyle(el);
                        const r = el.getBoundingClientRect();
                        return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
                    };
                    const dlgs = document.querySelectorAll(
                        '.dialog-wrap, .boss-dialog, .modal, [class*="dialog"], [class*="Dialog"], [class*="modal"], [class*="Modal"]'
                    );
                    for (const d of dlgs) {
                        if (!isVisible(d)) continue;
                        const t = d.innerText || '';
                        if (/还剩\\s*[1-9]\\d*\\s*次沟通机会/.test(t)) return t;
                    }
                    return '';
                })()
            """) or ""
            if warning_text:
                log.info(f"检测到 BOSS 软警告，自动关闭：{warning_text.strip()[:60]}")
                self._dismiss_warning_dialog()
                time.sleep(0.5)
                return True
        except Exception:
            pass
        return False

    def check_captcha(self) -> bool:
        try:
            count = self._cdp.eval(
                'document.querySelectorAll(\'#captcha, .geetest_holder, [class*="captcha"]\').length'
            )
            return bool(count and count > 0)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 滚动加载更多
    # ------------------------------------------------------------------
    def scroll_and_check_new(self) -> bool:
        """用 CDP 原生 Input.dispatchMouseEvent 发真实 mouseWheel 触发懒加载。
        BOSS 的 lazy-load 不响应 JS 模拟的 scroll 事件（反爬），必须用浏览器层
        面的真实手势。实测 wheel 后卡片正常加载。"""
        prev_count = self._cdp.eval(
            f"document.querySelectorAll('{self.CARD_SELECTOR}').length"
        ) or 0
        log.debug(f"开始滚动：当前卡片数 {prev_count}")

        # 最多 6 次滚轮事件，每次 deltaY=1500（约 1.5 个视口）
        for attempt in range(6):
            try:
                self._cdp._call("Input.dispatchMouseEvent", {
                    "type": "mouseWheel",
                    "x": 400,
                    "y": 400,
                    "deltaX": 0,
                    "deltaY": 1500,
                })
            except Exception as e:
                log.debug(f"dispatchMouseEvent 异常: {e}")
            time.sleep(random.uniform(1.8, 2.8))
            new_count = self._cdp.eval(
                f"document.querySelectorAll('{self.CARD_SELECTOR}').length"
            ) or 0
            log.debug(f"滚轮 #{attempt+1}: 卡片数 {prev_count} → {new_count}")
            if new_count > prev_count:
                return True

        # 6 次都没新增，文本特征双重确认是否真到底
        end_marker = self._cdp.eval(
            "(document.body.innerText || '').includes('没有更多') || "
            "(document.body.innerText || '').includes('到底了')"
        )
        if end_marker:
            log.info("页面显示已到底（'没有更多/到底了'）")
        else:
            log.warning(
                f"滚动 6 次后卡片数未增长（停在 {prev_count}），但未发现到底标志。"
                "建议运行 dom_probe.py 排查：可能 BOSS 改了懒加载触发机制。"
            )
        return False

    def classify_skip_reason(self) -> str:
        try:
            body = self._cdp.eval("document.body.innerText") or ""
            if "继续沟通" in body:
                return "已沟通"
            if "暂停招聘" in body:
                return "暂停招聘"
            if "职位已下线" in body:
                return "职位已下线"
        except Exception:
            pass
        return "无沟通按钮"
