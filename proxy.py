#!/usr/bin/env python3
"""OpenAI-compatible local API proxy for DeepSeek Web."""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from typing import Any, Optional

from aiohttp import web

# Configration

HOST = "127.0.0.1"
PORT = 1337

RESET_COMMANDS = {
    "/clear",
    "/reset",
    "/new",
    "/clearchat",
    "/resetchat",
    "/deletecurrentchat",
    "!clear",
    "!reset",
    "!new",
    "!clearchat",
    "!resetchat",
    "!deletecurrentchat",
    "?clear",
    "?reset",
    "?new",
    "?clearchat",
    "?resetchat",
    "?deletecurrentchat",
}

JOB_TIMEOUT = 600
MAX_PROMPT_CHARS = 24000
HISTORY_TAIL = 12
ARG_CHUNK_SIZE = 24
AUTO_RESET_THRESHOLD = (
    150000  # auto-reset chat session if server tokens exceed this (0 to disable)
)

MODELS = [
    {
        "id": "deepseek-chat",
        "object": "model",
        "created": 0,
        "owned_by": "deepseek-web",
    },
    {
        "id": "deepseek-reasoner",
        "object": "model",
        "created": 0,
        "owned_by": "deepseek-web",
    },
]

active_ws: Optional[web.WebSocketResponse] = None
pending_jobs: dict[str, asyncio.Future] = {}
_job_lock: Optional[asyncio.Lock] = None


def job_lock() -> asyncio.Lock:
    """Lazily create the lock so import works without a running loop."""
    global _job_lock
    if _job_lock is None:
        _job_lock = asyncio.Lock()
    return _job_lock


TOOL_PROTOCOL_TEMPLATE = """You are an AI assistant exposed through an OpenAI-compatible API with tool support.

You have access to the following tools:
{tool_list}

TOOL INSTRUCTIONS:
1. When you need to take an action (e.g. run a command, search files, read/write a file), output a tool call using this EXACT XML format:
<tool_call>
<name>TOOL_NAME</name>
<arguments>{{"argument_name": "argument value"}}</arguments>
</tool_call>

2. <arguments> must be a valid JSON object.
3. Use ONLY tool names from the available tools list above.
4. When you DO NOT need to call a tool (e.g. answering questions, greetings, explanations, math calculations, or providing the final answer after reviewing tool results), respond DIRECTLY in normal conversational text / markdown. When providing code, scripts, or terminal commands, ALWAYS format them inside markdown code fences with the language identifier (e.g. ```python ... ```). Do NOT use any <tool_call> tags.
5. NEVER use bash/echo/Write-Output just to speak to the user. Speak directly in your response text.
"""

CLINE_XML_PROTOCOL = """# Tool Use Formatting

You are an AI assistant integrated into Cline (a VS Code coding agent).

You MUST respond using ONLY the following XML tool format. Do NOT write plain prose answers.

## attempt_completion
When you are ready to give your final answer or complete the task, use:

<attempt_completion>
<result>
Your final answer or task summary goes here.
</result>
</attempt_completion>

## ask_followup_question
When you need more information from the user, use:

<ask_followup_question>
<question>Your question goes here.</question>
</ask_followup_question>

## Rules
1. Every response MUST be exactly one tool call block.
2. Do not wrap the XML in markdown code fences.
3. Do not include any text outside the XML block.
4. For simple factual questions, respond with attempt_completion containing the answer.
"""

CLINE_XML_MARKERS = (
    "<attempt_completion>",
    "<ask_followup_question>",
    "# Tool Use",
    "TOOL USE",
)


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    global active_ws
    ws = web.WebSocketResponse(heartbeat=30, max_msg_size=16 * 1024 * 1024)
    await ws.prepare(request)

    active_ws = ws
    print("\033[92m[Bridge]\033[0m DeepSeek browser tab connected!")

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    print(
                        f"\033[91m[Bridge]\033[0m Bad JSON from browser: {msg.data[:200]!r}"
                    )
                    continue
                req_id = data.get("id")
                future = pending_jobs.get(req_id) if req_id else None
                if future and not future.done():
                    future.set_result(data)
            elif msg.type in (web.WSMsgType.CLOSED, web.WSMsgType.ERROR):
                break
    finally:
        if active_ws == ws:
            active_ws = None
        for fut in list(pending_jobs.values()):
            if not fut.done():
                fut.set_result({"error": "Browser tab disconnected mid-request."})
        print("\033[93m[Bridge]\033[0m DeepSeek browser tab disconnected.")

    return ws


def _message_text(message: dict) -> str:
    """Flatten an OpenAI message's content (and any tool_calls) into text."""
    parts = []
    content = message.get("content", "")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif "text" in block:
                    parts.append(str(block["text"]))
    elif content:
        parts.append(str(content))
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            fn = tc.get("function", tc) if isinstance(tc, dict) else {}
            name = fn.get("name", "")
            args = fn.get("arguments", "")
            if isinstance(args, dict):
                args = json.dumps(args, ensure_ascii=False)
            if name:
                parts.append(
                    f"<tool_call>\n<name>{name}</name>\n<arguments>{args}</arguments>\n</tool_call>"
                )

    return "\n".join(p for p in parts if p)


def _truncate_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    keep = limit // 2
    return text[:keep] + "\n\n[... truncated ...]\n\n" + text[-keep:]


def _build_tool_list(tools: list[dict]) -> str:
    lines = []
    for tool in tools:
        fn = tool.get("function", tool) if isinstance(tool, dict) else {}
        name = fn.get("name", "unknown")
        desc = (fn.get("description") or "").strip().replace("\n", " ")
        params = fn.get("parameters") or {}
        lines.append(f"- {name}: {desc}")
        lines.append(f"  parameters: {json.dumps(params, ensure_ascii=False)}")
    return "\n".join(lines)


def _detect_cline_xml(messages: list[dict]) -> bool:
    for message in messages:
        if message.get("role") == "system":
            text = _message_text(message)
            if any(marker in text for marker in CLINE_XML_MARKERS):
                return True
    return False


def build_prompt(
    messages: list[dict],
    tools: Optional[list[dict]],
    tool_choice: Any = None,
) -> tuple[str, str]:
    use_tools = bool(tools) and tool_choice != "none"
    cline_xml = (not use_tools) and _detect_cline_xml(messages)

    if use_tools:
        mode = "native_tools"
    elif cline_xml:
        mode = "cline_xml"
    else:
        mode = "text"

    system_parts: list[str] = []
    conversation: list[str] = []

    for message in messages:
        role = (message.get("role") or "user").lower()
        text = _message_text(message)

        if role == "system":
            system_parts.append(text)
        elif role == "user":
            conversation.append(f"User:\n{text}")
        elif role == "assistant":
            if text:
                conversation.append(f"Assistant:\n{text}")
        elif role == "tool":
            conversation.append(f"Tool result:\n{text}")
    if len(conversation) > HISTORY_TAIL:
        conversation = conversation[-HISTORY_TAIL:]

    sections: list[str] = []

    if mode == "native_tools":
        sections.append(
            TOOL_PROTOCOL_TEMPLATE.format(tool_list=_build_tool_list(tools or []))
        )
    elif mode == "cline_xml":
        sections.append(CLINE_XML_PROTOCOL)

    if system_parts:
        sections.append("# System Context\n" + "\n\n".join(system_parts))

    if conversation:
        sections.append("# Conversation\n" + "\n\n".join(conversation))

    prompt = "\n\n".join(sections).strip()
    prompt = _truncate_middle(prompt, MAX_PROMPT_CHARS)
    return prompt, mode


_TOOL_CALL_BLOCK_RE = re.compile(
    r"<tool_call>(.*?)(?:</tool_call>|</[|\uFF5C]{2}DSML[|\uFF5C]{2}\s*(?:calls|invoke|parameter)>|(?=<tool_call>)|$)",
    re.DOTALL | re.IGNORECASE,
)
_TAG_NAME_RE = re.compile(r"<name>(.*?)</name>", re.DOTALL | re.IGNORECASE)
_TAG_ARGS_RE = re.compile(
    r"<arguments>(.*?)(?:</arguments>|</[|\uFF5C]{2}DSML[|\uFF5C]{2}\s*(?:parameter|invoke|calls)>|$)",
    re.DOTALL | re.IGNORECASE,
)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\s*|\s*```")
_DSML_INVOKE_RE = re.compile(
    r"<[|\uFF5C]{2}DSML[|\uFF5C]{2}\s+invoke\s+name=[\"']([^\"']+)[\"']\s*>(.*?)(?:</[|\uFF5C]{2}DSML[|\uFF5C]{2}\s+invoke>|$)",
    re.DOTALL | re.IGNORECASE,
)
_DSML_PARAM_RE = re.compile(
    r"<[|\uFF5C]{2}DSML[|\uFF5C]{2}\s+parameter\s+name=[\"']([^\"']+)[\"'](?:\s+[^>]*)?>(.*?)(?:</[|\uFF5C]{2}DSML[|\uFF5C]{2}\s+parameter>|$)",
    re.DOTALL | re.IGNORECASE,
)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


def _ensure_code_fenced(text: str) -> str:
    """If the model's text response is purely code but lacks markdown fences, wrap it."""
    trimmed = text.strip()
    if "```" in trimmed or not trimmed:
        return text

    code_indicators = (
        (
            "python",
            (
                "with open(",
                "import ",
                "from ",
                "def ",
                "class ",
                "print(",
                "if __name__",
            ),
        ),
        (
            "powershell",
            (
                "Get-",
                "Set-",
                "New-Item",
                "Remove-Item",
                "Write-Output",
                "Test-Path",
            ),
        ),
        (
            "bash",
            (
                "#!/bin/bash",
                "#!/bin/sh",
                "sudo ",
                "curl ",
                "npm ",
                "git ",
                "pip ",
                "docker ",
            ),
        ),
        (
            "javascript",
            (
                "const ",
                "let ",
                "var ",
                "function(",
                "function ",
                "console.log(",
                "export ",
            ),
        ),
    )

    first_line = trimmed.split("\n", 1)[0].strip()
    for lang, markers in code_indicators:
        if any(first_line.startswith(m) for m in markers):
            return f"```{lang}\n{trimmed}\n```"

    lines = trimmed.split("\n")
    if len(lines) >= 2:
        if any(
            l.strip().startswith(
                ("import ", "from ", "def ", "class ", "with open(", "with ")
            )
            for l in lines[:3]
        ):
            return f"```python\n{trimmed}\n```"

    return text


def _loads_lenient(raw: str) -> Optional[Any]:
    """Parse JSON, tolerating trailing commas and unescaped newlines."""
    if raw is None:
        return None
    candidate = raw.strip()
    if not candidate:
        return None

    def _escape_newlines_in_strings(text: str) -> str:
        """Re-escape literal newlines that sit inside JSON string values."""
        result: list[str] = []
        in_string = False
        i = 0
        while i < len(text):
            c = text[i]
            if c == "\\" and in_string:
                # Keep escape + next char verbatim (handles \\, \", etc.).
                result.append(c)
                i += 1
                if i < len(text):
                    result.append(text[i])
                    i += 1
                continue
            if c == '"':
                in_string = not in_string
                result.append(c)
            elif in_string and c == "\n":
                result.append("\\n")
            elif in_string and c == "\r":
                if i + 1 < len(text) and text[i + 1] == "\n":
                    i += 1  # consume the paired \n; emit a single \n
                result.append("\\n")
            else:
                result.append(c)
            i += 1
        return "".join(result)

    no_trail = re.sub(r",\s*([}\]])", r"\1", candidate)
    escaped = _escape_newlines_in_strings(candidate)
    escaped_no_trail = re.sub(r",\s*([}\]])", r"\1", escaped)

    for attempt in (candidate, no_trail, escaped, escaped_no_trail):
        try:
            return json.loads(attempt)
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _find_json_in(text: str) -> Optional[dict]:
    for match in _JSON_OBJECT_RE.finditer(text):
        parsed = _loads_lenient(match.group(0))
        if isinstance(parsed, dict):
            return parsed
    return None


def parse_tool_calls(raw: str, tool_names: list[str]) -> list[dict]:
    if not raw:
        return []

    calls: list[dict] = []

    for block in _TOOL_CALL_BLOCK_RE.findall(raw):
        block = block.strip()
        if not block:
            continue
        name_match = _TAG_NAME_RE.search(block)
        args_match = _TAG_ARGS_RE.search(block)

        if name_match:
            name = name_match.group(1).strip()
            args_raw = args_match.group(1).strip() if args_match else ""
            arguments = _loads_lenient(args_raw) if args_raw else None
            if not isinstance(arguments, dict):
                arguments = _find_json_in(block) or (
                    _find_json_in(args_raw) if args_raw else None
                )
            if not isinstance(arguments, dict):
                arguments = {"result": _strip_fences(args_raw)} if args_raw else {}
            calls.append({"name": name, "arguments": arguments})
            continue
        payload = _find_json_in(block)
        if isinstance(payload, dict):
            name = payload.get("name") or payload.get("tool") or payload.get("function")
            args = (
                payload.get("arguments")
                or payload.get("parameters")
                or payload.get("args")
            )
            if isinstance(args, str):
                args = _loads_lenient(args)
            if name:
                calls.append(
                    {
                        "name": str(name),
                        "arguments": (
                            args if isinstance(args, dict) else {"result": args}
                        ),
                    }
                )

    if calls:
        return _validate_calls(calls, tool_names)

    # 2. DeepSeek Native DSML format:
    # <｜｜DSML｜｜ calls>
    # <｜｜DSML｜｜ invoke name="TOOL_NAME">
    # <｜｜DSML｜｜ parameter name="PARAM" string="true">VALUE</｜｜DSML｜｜ parameter>
    # </｜｜DSML｜｜ invoke>
    # </｜｜DSML｜｜ calls>
    for m in _DSML_INVOKE_RE.finditer(raw):
        name = m.group(1).strip()
        body = m.group(2)
        args = {}
        for pm in _DSML_PARAM_RE.finditer(body):
            pname = pm.group(1).strip()
            pval = pm.group(2).strip()
            if pval.lower() == "true":
                args[pname] = True
            elif pval.lower() == "false":
                args[pname] = False
            elif pval.lower() == "null":
                args[pname] = None
            elif pval.isdigit():
                args[pname] = int(pval)
            else:
                try:
                    args[pname] = json.loads(pval)
                except Exception:
                    args[pname] = pval
        calls.append({"name": name, "arguments": args})

    if calls:
        return _validate_calls(calls, tool_names)
    payload = _find_json_in(_strip_fences(raw))
    if isinstance(payload, dict):
        name = payload.get("name") or payload.get("tool") or payload.get("function")
        args = (
            payload.get("arguments") or payload.get("parameters") or payload.get("args")
        )
        if isinstance(args, str):
            args = _loads_lenient(args)
        if name:
            return _validate_calls(
                [
                    {
                        "name": str(name),
                        "arguments": (
                            args if isinstance(args, dict) else {"result": args}
                        ),
                    }
                ],
                tool_names,
            )

    return []


def _validate_calls(calls: list[dict], tool_names: list[str]) -> list[dict]:
    valid = []
    for call in calls:
        name = call["name"]
        if tool_names and name not in tool_names:
            tail = name.split(".")[-1]
            if tail in tool_names:
                name = tail
            else:
                continue
        valid.append({"name": name, "arguments": call["arguments"]})
    return valid


def coerce_arguments(arguments: dict, tool_schema: Optional[dict]) -> dict:
    """Best-effort alignment of model output with the tool's JSON schema."""
    if not isinstance(arguments, dict):
        return {"result": str(arguments)}

    if not tool_schema:
        return arguments

    properties = tool_schema.get("properties") or {}
    required = tool_schema.get("required") or []
    if all(key in arguments for key in required):
        return arguments
    if len(required) == 1:
        key = required[0]
        if key not in arguments and arguments:
            if len(arguments) == 1:
                arguments = {key: next(iter(arguments.values()))}
            else:
                text_keys = ("result", "text", "content", "response", "answer", "value")
                for candidate in text_keys:
                    if candidate in arguments:
                        arguments = {key: arguments[candidate]}
                        break
    if properties and not tool_schema.get("additionalProperties", True):
        arguments = {k: v for k, v in arguments.items() if k in properties}

    return arguments


def chunk(
    completion_id: str,
    created: int,
    model: str,
    delta: dict,
    finish_reason: Optional[str] = None,
) -> dict:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }


def usage_payload(
    prompt: str, completion: str, accumulated_tokens: int = 0, initial_tokens: int = 0
) -> dict:
    if accumulated_tokens > 0:
        total = accumulated_tokens
        completion_tokens = (
            max(1, accumulated_tokens - initial_tokens)
            if initial_tokens > 0
            else max(1, len(completion) // 4)
        )
        prompt_tokens = max(1, total - completion_tokens)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total,
        }
    return {
        "prompt_tokens": max(1, len(prompt) // 4),
        "completion_tokens": max(1, len(completion) // 4),
        "total_tokens": max(1, (len(prompt) + len(completion)) // 4),
    }


async def _trigger_browser_reset():
    await asyncio.sleep(0.5)
    async with job_lock():
        if active_ws and not active_ws.closed:
            try:
                await active_ws.send_str(
                    json.dumps(
                        {
                            "action": "delete_chat",
                            "id": f"auto-reset-{int(time.time())}",
                        }
                    )
                )
            except Exception:
                pass


async def sse_write(response: web.StreamResponse, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False)
    await response.write(f"data: {data}\n\n".encode("utf-8"))


def iter_text_fragments(text: str, size: int = ARG_CHUNK_SIZE):
    for index in range(0, len(text), size):
        yield text[index : index + size]


_last_job_time: float = 0.0
MIN_JOB_INTERVAL: float = 1.5  # seconds between submissions to prevent rate limiting


async def run_browser_job(prompt: str, thinking: bool) -> dict:
    """Send the prompt to the browser and await its reply."""
    global _last_job_time
    async with job_lock():
        if not active_ws or active_ws.closed:
            return {"error": "DeepSeek browser tab is not connected."}
        now = time.time()
        elapsed = now - _last_job_time
        if elapsed < MIN_JOB_INTERVAL:
            await asyncio.sleep(MIN_JOB_INTERVAL - elapsed)

        req_id = f"{int(time.time())}-{uuid.uuid4().hex[:6]}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        pending_jobs[req_id] = future

        try:
            await active_ws.send_str(
                json.dumps(
                    {
                        "id": req_id,
                        "prompt": prompt,
                        "thinkingEnabled": bool(thinking),
                    }
                )
            )
            res = await asyncio.wait_for(future, timeout=JOB_TIMEOUT)
            _last_job_time = time.time()
            return res
        except asyncio.TimeoutError:
            _last_job_time = time.time()
            return {"error": "DeepSeek generation timed out."}
        finally:
            pending_jobs.pop(req_id, None)


CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
}


def error_response(message: str, status: int, err_type: str = "invalid_request_error"):
    return web.json_response(
        {"error": {"message": message, "type": err_type, "code": None}},
        status=status,
        headers=CORS_HEADERS,
    )


async def handle_models(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=CORS_HEADERS)
    return web.json_response({"object": "list", "data": MODELS}, headers=CORS_HEADERS)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "ok",
            "browser_connected": bool(active_ws and not active_ws.closed),
            "pending_jobs": len(pending_jobs),
        },
        headers=CORS_HEADERS,
    )


def _is_cline_request(
    request: web.Request,
    messages: Optional[list[dict]] = None,
) -> bool:
    # Cline SSE parser fails on tool_calls chunks, so force non-streaming.
    ua = request.headers.get("User-Agent", "")
    if "cline" in ua.lower():
        return True
    if request.headers.get("X-Cline-Version"):
        return True
    if messages and _detect_cline_xml(messages):
        return True
    return False


async def handle_chat_completions(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=CORS_HEADERS)

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return error_response("Request body must be valid JSON.", 400)

    messages = body.get("messages") or []
    if not isinstance(messages, list) or not messages:
        return error_response("'messages' must be a non-empty array.", 400)

    model = body.get("model") or "deepseek-chat"
    stream = bool(body.get("stream", False))

    last_user_text = ""
    for m in reversed(messages):
        if (m.get("role") or "").lower() == "user":
            last_user_text = _message_text(m).strip()
            break

    if last_user_text.lower() in RESET_COMMANDS:
        print(f"\033[93m[Command]\033[0m Reset chat triggered: {last_user_text}")
        async with job_lock():
            if active_ws and not active_ws.closed:
                try:
                    await active_ws.send_str(
                        json.dumps(
                            {
                                "action": "delete_chat",
                                "id": f"reset-{int(time.time())}",
                            }
                        )
                    )
                except Exception:
                    pass

        reply_text = "Chat session cleared. Started a fresh conversation."
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())

        if not stream:
            return web.json_response(
                {
                    "id": completion_id,
                    "object": "chat.completion",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": reply_text},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": usage_payload(last_user_text, reply_text),
                },
                headers=CORS_HEADERS,
            )

        response = web.StreamResponse(
            status=200,
            headers={
                **CORS_HEADERS,
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
            },
        )
        await response.prepare(request)
        await sse_write(
            response,
            chunk(
                completion_id,
                created,
                model,
                {"role": "assistant", "content": reply_text},
            ),
        )
        await sse_write(
            response,
            chunk(completion_id, created, model, {}, "stop"),
        )
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    tools = body.get("tools")
    tool_choice = body.get("tool_choice")
    stream_options = body.get("stream_options") or {}
    include_usage = bool(stream_options.get("include_usage"))
    is_reasoner = "reasoner" in model.lower() or "r1" in model.lower()

    # Cline's SSE parser cannot handle tool_calls delta chunks — force non-streaming.
    # Detection is done AFTER messages is parsed so we can check the system prompt.
    is_cline = _is_cline_request(request, messages)
    if is_cline and stream:
        stream = False
        print(
            "\033[93m[Request]\033[0m Cline detected — downgrading to non-streaming mode"
        )

    tool_names: list[str] = []
    tool_schemas: dict[str, dict] = {}
    if isinstance(tools, list):
        for tool in tools:
            fn = tool.get("function", tool) if isinstance(tool, dict) else {}
            name = fn.get("name")
            if name:
                tool_names.append(name)
                tool_schemas[name] = fn.get("parameters") or {}

    prompt, mode = build_prompt(
        messages, tools if isinstance(tools, list) else None, tool_choice
    )

    print(
        f"\033[96m[Request]\033[0m model={model} stream={stream} "
        f"mode={mode} tools={len(tool_names)} reasoner={is_reasoner} cline={is_cline}"
    )

    result = await run_browser_job(prompt, is_reasoner)

    if "error" in result:
        return error_response(result["error"], 502, "server_error")

    raw_text = (result.get("text") or "").strip()
    reasoning = (result.get("reasoning") or "").strip()

    print(f"\033[93m[Debug]\033[0m raw_text repr (first 300): {repr(raw_text[:300])}")

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    tool_calls: list[dict] = []
    content: str = ""

    if mode == "native_tools":
        parsed = parse_tool_calls(raw_text, tool_names)
        print(
            f"\033[93m[Debug]\033[0m parse_tool_calls: found={len(parsed)} "
            f"names={[c['name'] for c in parsed]} valid_names={tool_names}"
        )
        if ("<tool_call>" in raw_text or "DSML" in raw_text) and not parsed:
            print(
                f"\033[91m[Debug-ParseError]\033[0m tool tag found but failed to parse: {repr(raw_text[:600])}"
            )
        elif not parsed:
            print(
                f"\033[94m[Debug]\033[0m No tool call — delivering natural text response"
            )

        if parsed:
            for index, call in enumerate(parsed):
                arguments = coerce_arguments(
                    call["arguments"], tool_schemas.get(call["name"])
                )
                tool_calls.append(
                    {
                        "id": f"call_{uuid.uuid4().hex[:24]}",
                        "index": index,
                        "name": call["name"],
                        "arguments": arguments,
                    }
                )
        elif "attempt_completion" in tool_names:
            tool_calls.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:24]}",
                    "index": 0,
                    "name": "attempt_completion",
                    "arguments": {
                        "result": _ensure_code_fenced(raw_text) or "(empty response)"
                    },
                }
            )
        else:
            content = _ensure_code_fenced(raw_text)
    elif mode == "cline_xml":
        content = normalize_cline_xml(raw_text)
    else:
        content = _ensure_code_fenced(raw_text)

    print(
        f"\033[92m[Success]\033[0m mode={mode} "
        f"text_len={len(raw_text)} tool_calls={len(tool_calls)} content_len={len(content)}"
    )

    accumulated_tokens = int(result.get("accumulatedTokens") or 0)
    initial_tokens = int(result.get("initialTokens") or 0)
    usage = usage_payload(prompt, raw_text, accumulated_tokens, initial_tokens)

    if AUTO_RESET_THRESHOLD > 0 and accumulated_tokens >= AUTO_RESET_THRESHOLD:
        print(
            f"\033[93m[Session]\033[0m Reached {accumulated_tokens} / {AUTO_RESET_THRESHOLD} tokens. Auto-resetting chat session for next turn..."
        )
        asyncio.create_task(_trigger_browser_reset())

    if not stream:
        if tool_calls:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": json.dumps(
                                call["arguments"], ensure_ascii=False
                            ),
                        },
                    }
                    for call in tool_calls
                ],
            }
            finish_reason = "tool_calls"
        else:
            message = {"role": "assistant", "content": content}
            if reasoning:
                message["reasoning_content"] = reasoning
            finish_reason = "stop"

        return web.json_response(
            {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": usage,
            },
            headers=CORS_HEADERS,
        )

    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            **CORS_HEADERS,
            "Content-Type": "text/event-stream; charset=utf-8",
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    await response.prepare(request)

    if tool_calls:
        header = chunk(
            completion_id,
            created,
            model,
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "index": call["index"],
                        "id": call["id"],
                        "type": "function",
                        "function": {"name": call["name"], "arguments": ""},
                    }
                    for call in tool_calls
                ],
            },
        )
        print(
            f"\033[93m[Debug-Stream]\033[0m tool_calls header: {json.dumps(header, ensure_ascii=False)}"
        )
        await sse_write(response, header)

        for call in tool_calls:
            serialized = json.dumps(call["arguments"], ensure_ascii=False)
            frag_count = 0
            for fragment in iter_text_fragments(serialized):
                frag_count += 1
                arg_chunk = chunk(
                    completion_id,
                    created,
                    model,
                    {
                        "tool_calls": [
                            {
                                "index": call["index"],
                                "function": {"arguments": fragment},
                            }
                        ]
                    },
                )
                await sse_write(response, arg_chunk)
            print(
                f"\033[93m[Debug-Stream]\033[0m tool_call[{call['index']}] "
                f"name={call['name']!r} args_len={len(serialized)} frags={frag_count}"
            )

        final = chunk(completion_id, created, model, {}, "tool_calls")
        print(
            f"\033[93m[Debug-Stream]\033[0m tool_calls final: {json.dumps(final, ensure_ascii=False)}"
        )
        await sse_write(response, final)
    else:
        first = True
        frag_count = 0

        # Stream reasoning_content fragments first if thinking output is present (DeepSeek-R1)
        if reasoning:
            for fragment in iter_text_fragments(reasoning, size=64):
                delta = {"reasoning_content": fragment}
                if first:
                    delta["role"] = "assistant"
                    first = False
                await sse_write(response, chunk(completion_id, created, model, delta))

        for fragment in iter_text_fragments(content, size=64):
            frag_count += 1
            delta = {"content": fragment}
            if first:
                delta["role"] = "assistant"
                first = False
            await sse_write(response, chunk(completion_id, created, model, delta))

        print(
            f"\033[93m[Debug-Stream]\033[0m text frags={frag_count} content_len={len(content)}"
        )

        if first:
            await sse_write(
                response,
                chunk(
                    completion_id, created, model, {"role": "assistant", "content": ""}
                ),
            )

        await sse_write(response, chunk(completion_id, created, model, {}, "stop"))

    if include_usage:
        await sse_write(
            response,
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [],
                "usage": usage,
            },
        )

    await response.write(b"data: [DONE]\n\n")
    await response.write_eof()
    return response


_CLINE_ATTEMPT_RE = re.compile(
    r"<attempt_completion>\s*<result>(.*?)</result>\s*</attempt_completion>",
    re.DOTALL | re.IGNORECASE,
)
_CLINE_ASK_RE = re.compile(
    r"<ask_followup_question>\s*<question>(.*?)</question>\s*</ask_followup_question>",
    re.DOTALL | re.IGNORECASE,
)


def normalize_cline_xml(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return (
            "<attempt_completion><result>(empty response)</result></attempt_completion>"
        )

    match = _CLINE_ATTEMPT_RE.search(text)
    if match:
        inner = match.group(1).strip()
        return (
            f"<attempt_completion>\n<result>\n{inner}\n</result>\n</attempt_completion>"
        )

    match = _CLINE_ASK_RE.search(text)
    if match:
        inner = match.group(1).strip()
        return f"<ask_followup_question>\n<question>\n{inner}\n</question>\n</ask_followup_question>"

    text = _strip_fences(text)
    if "<attempt_completion>" in text or "<ask_followup_question>" in text:
        return text

    return f"<attempt_completion>\n<result>\n{text}\n</result>\n</attempt_completion>"


def make_app() -> web.Application:
    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.router.add_route("POST", "/v1/chat/completions", handle_chat_completions)
    app.router.add_route("OPTIONS", "/v1/chat/completions", handle_chat_completions)
    app.router.add_route("GET", "/v1/models", handle_models)
    app.router.add_route("OPTIONS", "/v1/models", handle_models)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/ws", websocket_handler)
    return app


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="OpenAI-compatible proxy for DeepSeek Web"
    )
    parser.add_argument("--host", default=HOST, help=f"Host to bind (default: {HOST})")
    parser.add_argument(
        "--port", type=int, default=PORT, help=f"Port to bind (default: {PORT})"
    )
    parser.add_argument(
        "--reset-threshold",
        type=int,
        default=AUTO_RESET_THRESHOLD,
        help=f"Auto-reset chat session token threshold (default: {AUTO_RESET_THRESHOLD}, 0 to disable)",
    )
    args = parser.parse_args()

    print(
        f"\033[95m[Boot]\033[0m OpenAI-compatible bridge on http://{args.host}:{args.port}/v1"
    )
    print(f"\033[95m[Boot]\033[0m WebSocket endpoint:  ws://{args.host}:{args.port}/ws")
    AUTO_RESET_THRESHOLD = args.reset_threshold
    web.run_app(make_app(), host=args.host, port=args.port, print=None)
