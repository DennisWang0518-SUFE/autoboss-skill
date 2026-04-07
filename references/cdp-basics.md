# Chrome DevTools Protocol (CDP) 基础

本项目完全基于原生 CDP WebSocket 实现，不依赖 Playwright/Selenium。

## 为什么不用 Playwright/Selenium？

Playwright/Selenium 会创建自己管理的 Chrome 实例，或接管浏览器时会触发页面生命周期事件（如 `visibilitychange`、`blur`、`focus`）。对于需要保持登录态和已筛选条件的场景，这些事件可能导致页面重置或跳转。

原生 CDP WebSocket 只建立一条 socket 连接，关闭时仅关闭 socket，不向 Chrome 发送任何"断开"信号，标签页完全不受影响。

## CDP 连接流程

```
1. Chrome 启动时开放 HTTP 接口：http://localhost:9222/json
   返回所有标签页的 JSON 列表，每项包含：
   {
     "id": "ABCD1234",
     "url": "https://www.zhipin.com/...",
     "webSocketDebuggerUrl": "ws://localhost:9222/devtools/page/ABCD1234"
   }

2. 找到目标标签页后，用原生 socket 发起 WebSocket 握手：
   GET /devtools/page/ABCD1234 HTTP/1.1
   Host: localhost:9222
   Upgrade: websocket
   Connection: Upgrade
   Sec-WebSocket-Key: <base64随机16字节>
   Sec-WebSocket-Version: 13

3. Chrome 返回 HTTP 101 Switching Protocols，握手完成。
   之后通过 WebSocket 帧双向传输 JSON 消息。
```

## 消息格式

**发送（客户端 → Chrome）**：
```json
{
  "id": 1,
  "method": "Runtime.evaluate",
  "params": {
    "expression": "document.title",
    "returnByValue": true
  }
}
```

**接收（Chrome → 客户端）**：
```json
{
  "id": 1,
  "result": {
    "result": {
      "type": "string",
      "value": "BOSS直聘"
    }
  }
}
```

Chrome 还会主动推送事件帧（无 `id` 字段）。`CDPClient._call()` 的实现会跳过这些事件，只返回匹配 `id` 的响应。

## WebSocket 帧结构（简化）

客户端发送的帧必须进行掩码（MASK=1）处理：
```
Byte 0: 0x81 (FIN=1, opcode=1 text)
Byte 1: 0x80 | length  (MASK=1, payload长度)
Bytes 2-5: 4字节随机掩码
Bytes 6+: 掩码后的 JSON payload
```

服务端发送的帧不需要掩码（MASK=0）。

## 常用 CDP 方法

| 方法 | 说明 |
|------|------|
| `Runtime.evaluate` | 在页面上下文执行 JS 表达式，返回值 |
| `Page.navigate` | 导航到指定 URL（本项目未使用，用户手动导航） |
| `Input.dispatchMouseEvent` | 模拟鼠标事件（本项目用 JS click() 代替） |
| `DOM.getDocument` | 获取 DOM 树（本项目用 JS 查询代替） |

本项目只使用 `Runtime.evaluate`，通过注入 JavaScript 完成所有页面操作。这是最灵活、最直接的方式。

## 超时处理

`CDPClient._call()` 在发送消息后设置 socket 超时（默认15秒），轮询接收帧直到找到匹配 `id` 的响应。如果超时，会抛出 `socket.timeout` 异常，由上层 `try/except` 捕获处理。

## 调试技巧

在浏览器地址栏访问 `http://localhost:9222/json` 可以实时查看所有标签页信息，确认脚本能否找到目标标签页。
