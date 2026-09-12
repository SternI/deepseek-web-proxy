# deepseek-web-proxy

Run a local OpenAI-compatible API bridge using your logged-in browser tab on [chat.deepseek.com](https://chat.deepseek.com).

Supports streaming and native tool/function calling.

---

## Disclaimer

This repository is created for **educational and research purposes only**. It demonstrates browser automation, WebSocket bridging, and local reverse proxy architectures. It is not affiliated with, endorsed by, or sponsored by DeepSeek.

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Run the proxy

```bash
python proxy.py
```

Default endpoint: `http://127.0.0.1:1337/v1`

*(Optional flags: `python proxy.py --host 127.0.0.1 --port 1337 --reset-threshold 150000`)*

### 3. Connect your browser

Keep a tab open at [chat.deepseek.com](https://chat.deepseek.com).

- **Option A (Automated):** Install Tampermonkey in your browser, create a new script, paste [`deepseek-bridge.user.js`](./deepseek-bridge.user.js), and save. When you open DeepSeek, a green badge in the bottom-right will show `Bridge: Connected`.
- **Option B (Manual):** Open DevTools (`F12`) on DeepSeek, go to the **Console** tab, paste the contents of [`deepseek-bridge.user.js`](./deepseek-bridge.user.js), and press Enter.

---

## API Configration for Clients

Use these settings in any OpenAI-compatible client (OpenCode, Cline, Continue, Aider, LangChain, OpenAI SDK, etc.):

- **Base URL:** `http://127.0.0.1:1337/v1`
- **API Key:** `nah`
- **Models:**
  - `deepseek-chat` (standard mode)
  - `deepseek-reasoner` (thinking mode enabled)

---

## Notes

- Keep the DeepSeek tab open while using the proxy.
- If the browser badge shows disconnected, click it to reconnect.
- Auto-resets the browser chat when session tokens reach 150k (configurable via `--reset-threshold`) to prevent context overflows.
- To reset manually, type `/clear` or `/reset` directly from your client prompt (or run `deleteCurrentChat()` in the browser console).

---

## License

MIT
