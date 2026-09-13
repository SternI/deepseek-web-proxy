# DeepSeek Web Proxy

OpenAI-compatible local API proxy for DeepSeek Web (`chat.deepseek.com`).

Allows you to use DeepSeek models (**DeepSeek Chat** and **DeepSeek Reasoner / R1**) with OpenCode or any OpenAI-compatible client.

---

## Disclaimer

This project is for educational and research purposes only. It is not affiliated with or endorsed by DeepSeek.

---

## Features

- **OpenAI API Compatibility**: Exposes `http://127.0.0.1:1337/v1/chat/completions` and `/v1/models`.
- **DeepSeek Reasoner (R1)**: Streams thinking / reasoning process into `reasoning_content` delta chunks in real-time.
- **Tool Calling**: Translates tool schemas and parses `<tool_call>` outputs into OpenAI function call structures for agent tools (`write`, `edit`, `bash`, `read`).
- **Real-Time Token Tracking**: Reports native token usage (`prompt_tokens`, `completion_tokens`, `total_tokens`, and cache hits).
- **Chat Management**: Send `/clear`, `/reset`, or `/new` in chat to start a clean conversation session.
- **Lightweight**: Pure Python (`aiohttp`) + Tampermonkey script with no heavy automation frameworks.

---

## Quick Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Install Userscript

1. Install [Tampermonkey](https://www.tampermonkey.net/) or Violentmonkey in your browser.
2. Create a new userscript and paste the contents of [`deepseek-bridge.user.js`](./deepseek-bridge.user.js).
3. Open [chat.deepseek.com](https://chat.deepseek.com/) and log in.
4. You will see a badge at the bottom-right: **Bridge: Connected (Ready)** once the proxy is running.

### 3. Start the Proxy

```bash
python deepseek-proxy.py
```

Options:
- `--host 127.0.0.1`: Listening host (default: `127.0.0.1`).
- `--port 1337`: Listening port (default: `1337`).
- `--reset-threshold 150000`: Auto-reset chat session if tokens exceed limit (default: `150000`, `0` to disable).

---

## OpenCode Configration

Add this provider to your OpenCode config (`opencode.jsonc`):

```json
{
  "provider": {
    "deepseek-proxy": {
      "api": "openai",
      "name": "DeepSeek Web Proxy",
      "options": {
        "baseURL": "http://127.0.0.1:1337/v1",
        "apiKey": "nah",
        "timeout": 300000,
        "chunkTimeout": 300000
      },
      "models": {
        "deepseek-chat": {
          "id": "deepseek-chat",
          "name": "DeepSeek Chat (via Web Proxy)",
          "tool_call": true,
          "temperature": true
        },
        "deepseek-reasoner": {
          "id": "deepseek-reasoner",
          "name": "DeepSeek Reasoner (via Web Proxy)",
          "tool_call": true,
          "reasoning": true,
          "temperature": true
        }
      }
    }
  }
}
```

---

## Other Clients (Cline, Cursor, etc.)

- **Base URL**: `http://127.0.0.1:1337/v1`
- **API Key**: `nah`
- **Models**:
  - `deepseek-chat`: Standard chat
  - `deepseek-reasoner`: DeepSeek R1 reasoning

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
    messages=[{"role": "user", "content": "Hello DeepSeek"}],
    stream=True,
)

for chunk in response:
    reasoning = getattr(chunk.choices[0].delta, "reasoning_content", None)
    if reasoning:
        print(reasoning, end="", flush=True)
    content = chunk.choices[0].delta.content or ""
    print(content, end="", flush=True)
```

---

## Notes & Chat Management

- Keep the browser tab open while using the proxy.
- If the badge shows disconnected, click it to reconnect immediately.
- To reset manually, send `/clear`, `/reset`, or `/new` directly from your client prompt (or run `window.deleteCurrentChat()` in the browser console).

---

## License

MIT
