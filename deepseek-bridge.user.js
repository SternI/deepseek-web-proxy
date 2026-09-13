// ==UserScript==
// @name         DeepSeek Web Bridge
// @namespace    https://github.com/your-username/deepseek-web-proxy
// @version      1.0.0
// @description  Automates chat.deepseek.com bridge for local OpenAI-compatible proxy
// @match        https://chat.deepseek.com/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==

(function () {
  "use strict";

  if (window.__DEEPSEEK_BRIDGE_INITIALIZED__) {
    console.log("[DeepSeek Bridge] Already running.");
    return;
  }
  window.__DEEPSEEK_BRIDGE_INITIALIZED__ = true;

  const WS_URL = "ws://127.0.0.1:1337/ws";
  const JOB_TIMEOUT_MS = 600_000;

  const isCompletionUrl = (url) => {
    if (!url) return false;
    const s = typeof url === "string" ? url : (url.url || (url.toString ? url.toString() : ""));
    return s.includes("/api/v0/chat/completion") || s.includes("/api/v0/chat/continue");
  };

  const log = (msg, color = "#38bdf8") => {
    const time = new Date().toLocaleTimeString();
    console.log(`%c[DeepSeek Bridge ${time}] ${msg}`, `color:${color};font-weight:bold;`);
  };

  const badge = document.createElement("div");
  badge.id = "deepseek-bridge-badge";
  badge.title = "Click to reconnect to proxy";
  Object.assign(badge.style, {
    position: "fixed",
    bottom: "16px",
    right: "16px",
    zIndex: "999999",
    padding: "6px 14px",
    borderRadius: "20px",
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    fontSize: "12px",
    fontWeight: "600",
    color: "#fff",
    backgroundColor: "rgba(15, 23, 42, 0.85)",
    backdropFilter: "blur(6px)",
    border: "1px solid rgba(255, 255, 255, 0.12)",
    boxShadow: "0 4px 12px rgba(0, 0, 0, 0.25)",
    display: "flex",
    alignItems: "center",
    gap: "8px",
    cursor: "pointer",
    userSelect: "none",
    transition: "all 0.2s ease",
  });

  const dot = document.createElement("span");
  Object.assign(dot.style, {
    width: "8px",
    height: "8px",
    borderRadius: "50%",
    backgroundColor: "#ef4444",
    transition: "background-color 0.2s ease",
  });

  const text = document.createElement("span");
  text.textContent = "Bridge: Disconnected";

  badge.appendChild(dot);
  badge.appendChild(text);

  function updateBadge(state, message) {
    if (!badge.parentElement && document.body) {
      document.body.appendChild(badge);
    }
    if (state === "connected") {
      dot.style.backgroundColor = "#10b981";
      text.textContent = message || "Bridge: Connected (Ready)";
    } else if (state === "busy") {
      dot.style.backgroundColor = "#f59e0b";
      text.textContent = message || "Bridge: Working...";
    } else {
      dot.style.backgroundColor = "#ef4444";
      text.textContent = message || "Bridge: Disconnected";
    }
  }

  async function waitForIdle(maxWaitMs = 8_000) {
    const start = Date.now();
    while (Date.now() - start < maxWaitMs) {
      const stopBtn = document.querySelector(
        "button[aria-label*='Stop'], .ds-button--primary[aria-label*='Stop'], div[role='button']._52c986b--stop"
      );
      if (!stopBtn) return true;
      updateBadge("busy", "Bridge: Waiting for previous turn to finish...");
      await new Promise((r) => setTimeout(r, 120));
    }
    return true;
  }

  if (document.body) {
    document.body.appendChild(badge);
  } else {
    window.addEventListener("DOMContentLoaded", () => document.body.appendChild(badge));
  }

  function getCurrentSessionId() {
    const match = window.location.pathname.match(/\/a\/chat\/s\/([a-zA-Z0-9_-]+)/);
    return match ? match[1] : null;
  }

  function locateSubmit() {
    const candidates = [
      document.querySelector("textarea"),
      document.querySelector('div[contenteditable="true"]'),
      document.querySelector('div[role="button"]._52c986b'),
      document.querySelector("div.ds-button--primary"),
      document.querySelector("form"),
    ].filter(Boolean);

    for (const el of candidates) {
      let node = el;
      while (node) {
        const key = Object.keys(node).find(
          (k) =>
            k.startsWith("__reactFiber$") ||
            k.startsWith("__reactInternalInstance$")
        );
        if (key && node[key]) {
          let fiber = node[key];
          while (fiber) {
            const props = fiber.memoizedProps || fiber.pendingProps;
            if (props && typeof props.onSubmit === "function") {
              return props.onSubmit;
            }
            fiber = fiber.return;
          }
        }
        node = node.parentElement;
      }
    }
    return null;
  }

  function locateChatController() {
    if (window.__deepseek_chatController) return window.__deepseek_chatController;

    const candidates = [
      document.querySelector("textarea"),
      document.querySelector('div[contenteditable="true"]'),
      document.querySelector("#root"),
      document.body,
    ].filter(Boolean);

    for (const el of candidates) {
      let node = el;
      while (node) {
        const key = Object.keys(node).find(
          (k) =>
            k.startsWith("__reactFiber$") ||
            k.startsWith("__reactInternalInstance$")
        );
        if (key && node[key]) {
          let fiber = node[key];
          while (fiber) {
            const props = fiber.memoizedProps || fiber.pendingProps;
            if (props?.value?.chatController) {
              window.__deepseek_chatController = props.value.chatController;
              return props.value.chatController;
            }
            if (props?.chatController) {
              window.__deepseek_chatController = props.chatController;
              return props.chatController;
            }
            fiber = fiber.return;
          }
        }
        node = node.parentElement;
      }
    }
    return null;
  }

  const initialSubmit = locateSubmit();
  if (initialSubmit) {
    window.mySubmit = initialSubmit;
    log("Bound to window.mySubmit", "#10b981");
  }

  window.deleteCurrentChat = async () => {
    const sessionId = getCurrentSessionId();
    if (!sessionId) {
      log("Not currently in a chat session (/a/chat/s/...)", "orange");
      return;
    }
    log(`Deleting chat session ${sessionId}...`, "#facc15");
    try {
      await fetch("/api/v0/chat_session/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_session_ids: [sessionId] }),
      });
      log("Chat deleted successfully!", "#10b981");
      setTimeout(() => { window.location.href = "/"; }, 150);
    } catch (e) {
      console.error("Failed to delete chat session:", e);
    }
  };

  class SSEParser {
    constructor() {
      this.buffer = "";
      this.fragments = {};
      this.lastFragIdx = -1;
      this.finished = false;
      this.isIncomplete = false;
      this.accumulatedTokenUsage = 0;
      this.initialTokenUsage = 0;
      this.messageId = null;
      this.sessionId = null;
    }

    touch(idx, str) {
      if (idx < 0) idx = 0;
      if (!this.fragments[idx]) {
        this.fragments[idx] = { type: "RESPONSE", content: "" };
      }
      this.fragments[idx].content += str;
      if (idx > this.lastFragIdx) this.lastFragIdx = idx;
    }

    feed(chunk) {
      if (this.finished) return true;
      this.buffer += chunk.replace(/\r\n/g, "\n").replace(/\r/g, "\n");

      let boundary;
      while ((boundary = this.buffer.indexOf("\n\n")) !== -1) {
        const block = this.buffer.slice(0, boundary);
        this.buffer = this.buffer.slice(boundary + 2);

        let evtName = "message";
        const dataLines = [];

        for (const line of block.split("\n")) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          if (trimmed.startsWith("event:")) {
            evtName = trimmed.slice(6).trim();
          } else if (trimmed.startsWith("data:")) {
            dataLines.push(trimmed.slice(5).trim());
          }
        }

        const dataStr = dataLines.join("\n");
        if (this.processEvent(evtName, dataStr)) {
          this.finished = true;
          return true;
        }
      }
      return false;
    }

    processEvent(evtName, dataStr) {
      if (!dataStr) {
        if (evtName === "close") {
          return !this.isIncomplete;
        }
        return false;
      }

      let p;
      try {
        p = JSON.parse(dataStr);
      } catch {
        return false;
      }

      if (p.response_message_id) {
        this.messageId = p.response_message_id;
      }
      if (p.chat_session_id) {
        this.sessionId = p.chat_session_id;
      }

      if (evtName === "hint" || p.event === "hint" || p.type === "error") {
        const hintText = p.content || (p.data && p.data.content) || (typeof p.v === "string" ? p.v : null);
        if (hintText) {
          log(`Server hint: ${hintText}`, "orange");
          this.touch(0, hintText);
          return true;
        }
      }

      if (p.p === "response/status" && p.o === "SET") {
        if (p.v === "FINISHED") {
          this.isIncomplete = false;
          return true;
        }
        if (p.v === "INCOMPLETE") {
          this.isIncomplete = true;
          return false;
        }
      }

      if (p.p === "response" && p.o === "BATCH" && Array.isArray(p.v)) {
        for (const item of p.v) {
          if (item.p === "accumulated_token_usage" && typeof item.v === "number") {
            this.accumulatedTokenUsage = item.v;
          }
          if (item.quasi_status === "FINISHED" || item.status === "FINISHED") {
            this.isIncomplete = false;
            return true;
          }
          if (item.quasi_status === "INCOMPLETE" || item.status === "INCOMPLETE") {
            this.isIncomplete = true;
          }
        }
      }

      if (p.p === "response/accumulated_token_usage" && typeof p.v === "number") {
        this.accumulatedTokenUsage = p.v;
      }

      if (p.v && typeof p.v === "object" && p.v.response) {
        if (typeof p.v.response.accumulated_token_usage === "number") {
          if (!this.initialTokenUsage) {
            this.initialTokenUsage = p.v.response.accumulated_token_usage;
          }
          this.accumulatedTokenUsage = p.v.response.accumulated_token_usage;
        }
        if (p.v.response.message_id) {
          this.messageId = p.v.response.message_id;
        }
        if (p.v.response.chat_session_id) {
          this.sessionId = p.v.response.chat_session_id;
        }
        if (p.v.response.status === "FINISHED") {
          this.isIncomplete = false;
        } else if (p.v.response.status === "INCOMPLETE") {
          this.isIncomplete = true;
        } else if (p.v.response.status === "WIP") {
          this.isIncomplete = false;
        }

        const frags = p.v.response.fragments || [];
        frags.forEach((f, i) => {
          const incomingContent = f.content || "";
          const existingContent = (this.fragments[i] && this.fragments[i].content) || "";
          this.fragments[i] = {
            type: f.type || "RESPONSE",
            content: incomingContent.length >= existingContent.length ? incomingContent : existingContent,
          };
          if (i > this.lastFragIdx) this.lastFragIdx = i;
        });
        return false;
      }

      if (p.p && p.o === "APPEND" && typeof p.v === "string") {
        const m = p.p.match(/fragments\/(-?\d+)\/content/);
        if (m) {
          let idx = parseInt(m[1], 10);
          if (idx < 0) idx = this.lastFragIdx >= 0 ? this.lastFragIdx : 0;
          this.touch(idx, p.v);
        }
        return false;
      }

      if (
        p.p &&
        (p.o === "INSERT" || p.o === "SET") &&
        p.v &&
        typeof p.v === "object" &&
        p.p.includes("fragments")
      ) {
        const newIdx = this.lastFragIdx + 1;
        this.fragments[newIdx] = {
          type: p.v.type || "RESPONSE",
          content: p.v.content || "",
        };
        this.lastFragIdx = newIdx;
        return false;
      }

      if (typeof p.v === "string") {
        const idx = this.lastFragIdx >= 0 ? this.lastFragIdx : 0;
        this.touch(idx, p.v);
        return false;
      }

      if (evtName === "close") {
        return !this.isIncomplete;
      }

      return false;
    }

    getResult() {
      if (this.buffer.trim()) {
        const block = this.buffer;
        this.buffer = "";
        let evtName = "message";
        const dataLines = [];
        for (const line of block.split("\n")) {
          const trimmed = line.trim();
          if (!trimmed) continue;
          if (trimmed.startsWith("event:")) evtName = trimmed.slice(6).trim();
          else if (trimmed.startsWith("data:")) dataLines.push(trimmed.slice(5).trim());
        }
        if (dataLines.length) this.processEvent(evtName, dataLines.join("\n"));
      }

      const sorted = Object.entries(this.fragments)
        .sort(([a], [b]) => Number(a) - Number(b))
        .map(([, f]) => f);

      const text = sorted
        .filter((f) => f.type === "RESPONSE" || !f.type)
        .map((f) => f.content)
        .join("")
        .trim();

      const reasoning = sorted
        .filter((f) => f.type === "THINKING")
        .map((f) => f.content)
        .join("")
        .trim();

      return {
        text,
        reasoning: reasoning || undefined,
        accumulatedTokens: this.accumulatedTokenUsage || 0,
        initialTokens: this.initialTokenUsage || 0,
      };
    }
  }

  let _pendingCapture = null;

  async function triggerContinue(sessionId, messageId) {
    await new Promise((r) => setTimeout(r, 250));

    const controller = locateChatController() || window.__deepseek_chatController;
    if (controller && typeof controller.continueCompletion === "function") {
      log(`Triggering continueCompletion via ChatController (session=${sessionId}, message=${messageId})...`, "#38bdf8");
      try {
        await controller.continueCompletion({
          chatSessionId: sessionId,
          messageId: messageId,
          allowParallelStreams: true,
        });
        return true;
      } catch (e) {
        log(`ChatController.continueCompletion error: ${e}`, "orange");
      }
    }

    const continueBtn = Array.from(document.querySelectorAll("button, div[role='button']")).find((el) => {
      const t = (el.textContent || "").trim().toLowerCase();
      return t.includes("continue") || t.includes("继续生成") || t.includes("继续");
    });
    if (continueBtn) {
      let node = continueBtn;
      while (node) {
        const key = Object.keys(node).find((k) => k.startsWith("__reactFiber$") || k.startsWith("__reactInternalInstance$"));
        if (key && node[key]) {
          let fiber = node[key];
          while (fiber) {
            const props = fiber.memoizedProps || fiber.pendingProps;
            if (props && typeof props.onClick === "function") {
              try {
                const fakeEvt = new Event("click");
                try { Object.defineProperty(fakeEvt, "isTrusted", { value: true, configurable: true }); } catch (_) { }
                props.onClick({ nativeEvent: fakeEvt });
                log("Triggered continue via button fiber onClick", "#38bdf8");
                return true;
              } catch (e) { }
            }
            fiber = fiber.return;
          }
        }
        node = node.parentElement;
      }
    }

    if (sessionId && messageId) {
      log(`Triggering continue via direct fetch POST /api/v0/chat/continue (session=${sessionId}, message=${messageId})...`, "#38bdf8");
      try {
        fetch("/api/v0/chat/continue", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Accept: "text/event-stream",
          },
          body: JSON.stringify({
            chat_session_id: sessionId,
            message_id: messageId,
            fallback_to_resume: true,
          }),
        }).catch((err) => {
          log(`Direct fetch continue network error: ${err}`, "orange");
        });
        return true;
      } catch (e) {
        log(`Direct fetch continue failed: ${e}`, "red");
      }
    }

    return false;
  }

  async function handleStreamEnded(capture, parser) {
    if (parser.isIncomplete && capture.continueCount < 10) {
      capture.continueCount = (capture.continueCount || 0) + 1;
      log(`Stream paused with INCOMPLETE status (tokens: ${parser.accumulatedTokenUsage}). Auto-continuing (step ${capture.continueCount})...`, "#facc15");
      updateBadge("busy", `Bridge: Continuing (${capture.continueCount})...`);

      if (typeof capture.resetTimeout === "function") {
        capture.resetTimeout();
      }

      _pendingCapture = capture;

      const sessionId = parser.sessionId || getCurrentSessionId();
      const messageId = parser.messageId;

      const triggered = await triggerContinue(sessionId, messageId);
      if (!triggered) {
        log("Could not auto-trigger continue. Finalizing partial response.", "orange");
        _pendingCapture = null;
        const result = parser.getResult();
        capture.resolve(result);
      }
    } else {
      _pendingCapture = null;
      const result = parser.getResult();
      log(`Stream finished (${result.text.length} chars, session tokens: ${result.accumulatedTokens})` + (result.reasoning ? `, reasoning: ${result.reasoning.length} chars` : ""), "#10b981");
      capture.resolve(result);
    }
  }

  const origOpen = XMLHttpRequest.prototype.open;
  const origSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (method, url, ...args) {
    this._url = typeof url === "string" ? url : (url && url.toString ? url.toString() : "");
    this._method = method;
    return origOpen.apply(this, [method, url, ...args]);
  };

  XMLHttpRequest.prototype.send = function (body) {
    if (this._url && isCompletionUrl(this._url)) {
      log(`Intercepted XHR completion request: ${this._url}`, "#a855f7");

      if (_pendingCapture) {
        const capture = _pendingCapture;

        if (body) {
          try {
            const parsed = typeof body === "string" ? JSON.parse(body) : body;
            if (parsed?.chat_session_id) capture.parser.sessionId = parsed.chat_session_id;
          } catch (e) { }
        }

        let processedLength = 0;
        let isDone = false;

        const processChunks = () => {
          try {
            const raw = this.responseText || "";
            if (raw.length > processedLength) {
              const chunk = raw.slice(processedLength);
              processedLength = raw.length;
              if (capture.parser.feed(chunk)) {
                if (!isDone) {
                  isDone = true;
                  handleStreamEnded(capture, capture.parser);
                }
              }
            }
          } catch (e) { }
        };

        this.addEventListener("progress", processChunks);

        this.addEventListener("readystatechange", () => {
          processChunks();
          if (this.readyState === 4) {
            if (!isDone) {
              isDone = true;
              if (this.status >= 200 && this.status < 300) {
                handleStreamEnded(capture, capture.parser);
              } else {
                _pendingCapture = null;
                capture.reject(new Error(`HTTP error ${this.status}`));
              }
            }
          }
        });

        this.addEventListener("error", () => {
          if (!isDone) {
            isDone = true;
            _pendingCapture = null;
            capture.reject(new Error("Network error during completion"));
          }
        });

        this.addEventListener("abort", () => {
          if (!isDone) {
            isDone = true;
            _pendingCapture = null;
            capture.reject(new Error("Request aborted"));
          }
        });
      }
    }
    return origSend.apply(this, [body]);
  };

  const _originalFetch = window.fetch.bind(window);

  window.fetch = async function (url, options) {
    const response = await _originalFetch(url, options);
    const urlStr = typeof url === "string" ? url : (url && url.url) || "";

    if (_pendingCapture && isCompletionUrl(urlStr) && response.body) {
      const capture = _pendingCapture;
      _pendingCapture = null;

      if (!response.ok) {
        capture.reject(new Error(`Fetch error: ${response.status}`));
        return response;
      }

      if (options?.body) {
        try {
          const parsed = typeof options.body === "string" ? JSON.parse(options.body) : options.body;
          if (parsed?.chat_session_id) capture.parser.sessionId = parsed.chat_session_id;
        } catch (e) { }
      }

      const [stream1, stream2] = response.body.tee();
      const headers = new Headers();
      response.headers.forEach((val, key) => {
        if (!["content-encoding", "content-length"].includes(key.toLowerCase())) {
          headers.set(key, val);
        }
      });

      const pageResponse = new Response(stream1, {
        status: response.status,
        statusText: response.statusText,
        headers,
      });

      (async () => {
        const reader = stream2.getReader();
        const decoder = new TextDecoder();
        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            const chunk = decoder.decode(value, { stream: true });
            if (capture.parser.feed(chunk)) break;
          }
        } catch (e) {
          console.warn("[DeepSeek Bridge] Fetch stream error:", e);
        } finally {
          reader.cancel().catch(() => { });
          handleStreamEnded(capture, capture.parser);
        }
      })();

      return pageResponse;
    }

    return response;
  };

  let activeWs = null;
  let reconnectTimer = null;

  function connect() {
    if (activeWs && (activeWs.readyState === WebSocket.OPEN || activeWs.readyState === WebSocket.CONNECTING)) {
      log("WebSocket already connected or connecting.", "lime");
      return;
    }
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }

    const ws = new WebSocket(WS_URL);
    activeWs = ws;

    ws.onopen = () => {
      log("Connected to proxy", "lime");
      updateBadge("connected", "Bridge: Connected (Ready)");
    };

    ws.onclose = () => {
      if (activeWs === ws) activeWs = null;
      updateBadge("disconnected", "Bridge: Disconnected");
      reconnectTimer = setTimeout(connect, 3_000);
    };

    ws.onerror = () => {
      updateBadge("disconnected", "Bridge: Connection Error");
    };

    ws.onmessage = async (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch {
        return;
      }

      if (payload.action === "delete_chat") {
        log("Reset chat command received from client", "#facc15");
        updateBadge("busy", "Bridge: Resetting chat...");
        try {
          await window.deleteCurrentChat();
          updateBadge("connected", "Bridge: Connected");
          ws.send(JSON.stringify({ id: payload.id, success: true }));
        } catch (e) {
          updateBadge("connected", "Bridge: Connected");
          ws.send(JSON.stringify({ id: payload.id, error: String(e) }));
        }
        return;
      }

      const { id, prompt, model, thinkingEnabled } = payload;
      const isReasoner = Boolean(thinkingEnabled) || (model && (model.includes("reasoner") || model.includes("r1")));
      const t0 = Date.now();
      log(`[WS] Incoming prompt (${prompt.length} chars, ID: ${id})...`, "#facc15");
      updateBadge("busy", "Bridge: Submitting...");

      await waitForIdle(8_000);

      const parser = new SSEParser();
      let timeoutId;

      const capture = {
        parser,
        resolve: null,
        reject: null,
        continueCount: 0,
        resetTimeout: () => {
          clearTimeout(timeoutId);
          timeoutId = setTimeout(() => {
            _pendingCapture = null;
            capture.reject(new Error("DeepSeek generation timed out"));
          }, JOB_TIMEOUT_MS);
        },
      };

      const capturePromise = new Promise((resolve, reject) => {
        capture.resolve = resolve;
        capture.reject = reject;
      });

      const timeoutPromise = new Promise((_, reject) => {
        timeoutId = setTimeout(() => {
          _pendingCapture = null;
          reject(new Error("DeepSeek generation timed out"));
        }, JOB_TIMEOUT_MS);
      });

      _pendingCapture = capture;

      try {
        const submitFn = locateSubmit() || window.mySubmit;
        if (!submitFn) {
          throw new Error("Chat input not found. Make sure DeepSeek is loaded.");
        }

        updateBadge("busy", "Bridge: Generating stream...");

        submitFn(prompt, {
          thinkingEnabled: Boolean(isReasoner),
          searchEnabled: false,
          uploadFileSupported: true,
          interruptAndSendEnabled: true,
          source: undefined,
        }).catch((err) => {
          if (_pendingCapture) {
            const { reject } = _pendingCapture;
            _pendingCapture = null;
            reject(err);
          }
        });

        const result = await Promise.race([capturePromise, timeoutPromise]);
        clearTimeout(timeoutId);
        const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
        updateBadge("connected", `Bridge: Connected (Done in ${elapsed}s)`);
        log(`[WS] Completed prompt ${id} in ${elapsed}s`, "#10b981");
        ws.send(JSON.stringify({ id, ...result }));
      } catch (err) {
        clearTimeout(timeoutId);
        _pendingCapture = null;
        updateBadge("connected", "Bridge: Ready");
        log(`[WS] Request ${id} failed: ${err.message}`, "#ef4444");
        ws.send(JSON.stringify({ id, error: String(err) }));
      }
    };
  }

  window.__deepseekBridge = {
    get wsState() {
      if (!activeWs) return "NONE";
      return ["CONNECTING", "OPEN", "CLOSING", "CLOSED"][activeWs.readyState] || "UNKNOWN";
    },
    get isBusy() {
      const stopBtn = document.querySelector("button[aria-label*='Stop']");
      return Boolean(stopBtn);
    },
    get currentSessionId() {
      return getCurrentSessionId();
    },
    get submitFn() {
      return window.mySubmit || locateSubmit();
    },
    locateSubmit: () => locateSubmit(),
    reconnect: () => connect(),
    reset: () => window.deleteCurrentChat(),
  };

  badge.addEventListener("click", () => {
    log("Reconnecting to proxy...", "#facc15");
    connect();
  });

  connect();
})();
