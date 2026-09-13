# DeepSeek Web Proxy

OpenAI-compatible local API proxy for DeepSeek Web (`chat.deepseek.com`).

Allows you to use DeepSeek models (**DeepSeek Chat** and **DeepSeek Reasoner / R1**) with any OpenAI-compatible client or SDK.

---

## Disclaimer

This repository is created for **educational and research purposes only**. It demonstrates browser automation, WebSocket bridging, and local reverse proxy architectures. It is not affiliated with, endorsed by, or sponsored by DeepSeek.

---

## Features

- **OpenAI Chat Compatibility**: Exposes `http://127.0.0.1:1337/v1/chat/completions` and `/v1/models`.
- **DeepSeek Reasoner (R1)**: Full support for thinking / reasoning process streamed into standard `reasoning_content` delta chunks.
- **Native Tool Calling**: Automatically translates tool schemas to the model and parses `<tool_call>` outputs into OpenAI function call structures.
- **XML Tool Protocol Support**: Compatibility with agent XML tool formats (`<attempt_completion>`, `<ask_followup_question>`).
- **Real-Time Token Usage Tracking**: Intercepts native token counts (`prompt_tokens`, `completion_tokens`, `total_tokens`, and cache hits).
- **Auto-Continuation**: Automatically continues generation if DeepSeek pauses on incomplete stream steps.
- **Chat Management**: Use `/clear`, `/reset`, `/new`, or `/deletecurrentchat` directly in chat to start a fresh conversation session.
- **Auto-Reset Threshold**: Automatically clears conversation when session token usage exceeds threshold (`--reset-threshold 150000`) to prevent context overflows.
- **Lightweight**: Pure Python (`aiohttp`) + Tampermonkey userscript with zero heavy browser automation dependencies (no Selenium/Playwright).

---

## Quick Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Install Userscript

1. Install Tampermonkey or Violentmonkey in your browser.
2. Create a new userscript and paste the contents of [`deepseek-bridge.user.js`](./deepseek-bridge.user.js).
3. Navigate to [chat.deepseek.com](https://chat.deepseek.com/).
4. You will see a badge at the bottom-right: **Bridge: Connected (Ready)** once the proxy is running.

### 3. Start the Proxy

```bash
python deepseek-proxy.py
```

Options:
- `--host 127.0.0.1`: Listening host (default: `127.0.0.1`).
- `--port 1337`: Listening port (default: `1337`).
- `--reset-threshold 150000`: Auto-reset chat session if total tokens exceed threshold (default: `150000`, `0` to disable).

---

## Client Configration

Use these basic settings in any OpenAI-compatible client:

- **Base URL**: `http://127.0.0.1:1337/v1`
- **API Key**: `nah`
- **Models**:
  - `deepseek-chat` (standard chat)
  - `deepseek-reasoner` (thinking / reasoning enabled)

---

## Python Example

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:1337/v1",
    api_key="nah",
)

response = client.chat.completions.create(
    model="deepseek-chat",
    messages=[{"role": "user", "content": "What's Up"}],
    stream=True,
)

for chunk in response:
    content = chunk.choices[0].delta.content or ""
    print(content, end="", flush=True)
```

---

## Notes & Chat Management

- Keep the browser tab open while using the proxy.
- If the browser badge shows disconnected, click it to reconnect immediately.
- Auto-resets the browser chat when session tokens reach 150k (configurable via `--reset-threshold`) to prevent context overflows.
- To reset manually, send `/clear`, `/reset`, or `/new` directly from your client prompt (or run `window.deleteCurrentChat()` in the browser console).

---

## License

MIT
