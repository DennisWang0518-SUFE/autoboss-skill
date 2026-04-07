"""
用原生 WebSocket CDP 探查 BOSS直聘页面 DOM，不影响 Chrome 标签页状态。
用法：python dom_probe.py

在选择器失效时运行此脚本，它会扫描页面中所有相关 class 名和候选选择器的匹配数量，
帮助你快速定位新的选择器并更新 bot.py。
"""
import base64
import json
import os
import socket
import struct
import urllib.request


CDP_ENDPOINT = "http://localhost:9222"


def get_zhipin_tab():
    resp = urllib.request.urlopen(f"{CDP_ENDPOINT}/json", timeout=5)
    tabs = json.loads(resp.read())
    for tab in tabs:
        if "zhipin.com" in tab.get("url", ""):
            return tab
    raise RuntimeError("未找到 zhipin.com 标签页，请先在Chrome中打开并筛选好职位列表页")


class _RawCDPSocket:
    """最小化 WebSocket 客户端，不发送 Origin 头，绕过 Chrome origin 检查。"""

    def __init__(self, host: str, port: int, path: str, timeout: int = 30):
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._buf = b""
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        self._sock.sendall(handshake.encode())
        raw = b""
        while b"\r\n\r\n" not in raw:
            raw += self._sock.recv(4096)
        status_line = raw.split(b"\r\n")[0].decode()
        if "101" not in status_line:
            raise ConnectionError(f"WebSocket 握手失败: {status_line}")
        header_end = raw.index(b"\r\n\r\n") + 4
        self._buf = raw[header_end:]

    def send_json(self, obj: dict):
        data = json.dumps(obj).encode()
        length = len(data)
        mask_key = os.urandom(4)
        masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))
        if length < 126:
            header = struct.pack(">BB", 0x81, 0x80 | length) + mask_key
        elif length < 65536:
            header = struct.pack(">BBH", 0x81, 0xFE, length) + mask_key
        else:
            header = struct.pack(">BBQ", 0x81, 0xFF, length) + mask_key
        self._sock.sendall(header + masked)

    def recv_frame(self):
        def recv_exact(n):
            while len(self._buf) < n:
                chunk = self._sock.recv(4096)
                if not chunk:
                    raise ConnectionError("连接断开")
                self._buf += chunk
            data, self._buf = self._buf[:n], self._buf[n:]
            return data

        b0, b1 = recv_exact(2)
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack(">H", recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", recv_exact(8))[0]
        payload = recv_exact(length)
        return json.loads(payload.decode())

    def recv_response(self, msg_id: int):
        """循环读取帧，直到找到匹配 id 的响应（跳过 Chrome 推送的事件消息）。"""
        while True:
            msg = self.recv_frame()
            if msg.get("id") == msg_id:
                return msg

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass


def cdp_eval(ws_url: str, expression: str):
    parts = ws_url.replace("ws://", "").split("/", 1)
    host_port = parts[0].split(":")
    host, port = host_port[0], int(host_port[1]) if len(host_port) > 1 else 9222
    path = "/" + parts[1]

    ws = _RawCDPSocket(host, port, path)
    try:
        ws.send_json({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True},
        })
        result = ws.recv_response(1)
        return result.get("result", {}).get("result", {}).get("value")
    finally:
        ws.close()


def main():
    tab = get_zhipin_tab()
    ws_url = tab["webSocketDebuggerUrl"]
    print(f"[+] 找到标签页: {tab['url']}")
    print(f"[+] WebSocket: {ws_url}\n")

    # 1. 收集含 job/card 关键字的 class 名
    job_classes = cdp_eval(ws_url, """
        (() => {
            const classes = new Set();
            document.querySelectorAll('[class]').forEach(el => {
                el.className.split(' ').forEach(c => {
                    if (c && (c.includes('job') || c.includes('card'))) classes.add(c);
                });
            });
            return [...classes].join(',');
        })()
    """)
    print("=== 含 job/card 的 class 名 ===")
    if job_classes:
        for cls in sorted(job_classes.split(",")):
            print(f"  .{cls}")
    else:
        print("  (无)")

    # 2. 统计 DOM 元素总数
    dom_count = cdp_eval(ws_url, "document.querySelectorAll('*').length")
    print(f"\n=== DOM 元素总数: {dom_count} ===")

    # 3. 尝试常见选择器，报告匹配数量
    selectors = [
        ".job-card-wrapper",
        ".job-card-box",
        ".job-card",
        "[class*='job-card']",
        ".search-job-result li",
        ".job-list-box li",
    ]
    print("\n=== 候选卡片选择器匹配数 ===")
    for sel in selectors:
        escaped = sel.replace("\\", "\\\\").replace("`", "\\`")
        count = cdp_eval(ws_url, f"document.querySelectorAll(`{escaped}`).length")
        mark = " <-- 推荐" if count and count > 0 else ""
        print(f"  {sel!r:45s} → {count}{mark}")

    # 4. 尝试"立即沟通"按钮选择器
    btn_selectors = [
        ".btn-startchat",
        "[class*='btn-chat']",
        "[class*='startchat']",
        "button[class*='chat']",
    ]
    print("\n=== 候选沟通按钮选择器 ===")
    for sel in btn_selectors:
        escaped = sel.replace("\\", "\\\\").replace("`", "\\`")
        count = cdp_eval(ws_url, f"document.querySelectorAll(`{escaped}`).length")
        print(f"  {sel!r:45s} → {count}")

    # 5. 收集含 chat/contact/btn 关键字的 class 名
    btn_classes = cdp_eval(ws_url, """
        (() => {
            const classes = new Set();
            document.querySelectorAll('[class]').forEach(el => {
                el.className.split(' ').forEach(c => {
                    if (c && (c.includes('chat') || c.includes('contact') || c.includes('startchat'))) classes.add(c);
                });
            });
            return [...classes].join(',');
        })()
    """)
    print("\n=== 含 chat/contact/startchat 的 class 名 ===")
    if btn_classes:
        for cls in sorted(btn_classes.split(",")):
            print(f"  .{cls}")
    else:
        print("  (无)")

    # 6. 滚动容器候选
    scroll_selectors = [
        ".job-list-box",
        ".search-job-result",
        ".job-scroll-list",
        ".job-list",
    ]
    print("\n=== 候选滚动容器选择器 ===")
    for sel in scroll_selectors:
        escaped = sel.replace("\\", "\\\\").replace("`", "\\`")
        count = cdp_eval(ws_url, f"document.querySelectorAll(`{escaped}`).length")
        print(f"  {sel!r:45s} → {count}")


if __name__ == "__main__":
    main()
