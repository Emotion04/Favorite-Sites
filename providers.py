#!/usr/bin/env python3
"""AI 多供应商路由：Anthropic Messages + OpenAI 兼容端点统一调用。
支持：DeepSeek / Qwen / MiMo / Anthropic，优先客户端 key，兜底环境变量。

用法：
  from providers import call_structured, PROVIDER_IDS, get_provider
  result = call_structured("deepseek", system, user, schema, stream=False)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

PROVIDER_IDS = ["deepseek", "qwen", "mimo", "anthropic"]

PROVIDERS: dict[str, dict] = {
    "deepseek": {
        "label": "DeepSeek",
        "url": "https://api.deepseek.com/chat/completions",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "default_model": "deepseek-v4-flash",
        "protocol": "openai",  # OpenAI-compatible chat completions
        "env_key": "DEEPSEEK_API_KEY",
    },
    "qwen": {
        "label": "通义千问",
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "default_model": "qwen3.7-flash",
        "protocol": "openai",
        "env_key": "QWEN_API_KEY",
    },
    "mimo": {
        "label": "小米 MiMo",
        "url": "https://token-plan-cn.xiaomimomo.com/v1/chat/completions",
        "auth_header": "api-key",
        "auth_prefix": "",
        "default_model": "mimo-v2.5-pro",
        "protocol": "openai",
        "env_key": "MIMO_API_KEY",
    },
    "anthropic": {
        "label": "Anthropic",
        "url": "https://api.anthropic.com/v1/messages",
        "auth_header": "x-api-key",
        "auth_prefix": "",
        "default_model": "claude-sonnet-4-6",
        "protocol": "anthropic",
        "env_key": "ANTHROPIC_API_KEY",
    },
}


def get_provider(provider_id: str) -> dict:
    p = PROVIDERS.get(provider_id)
    if not p:
        raise ValueError(f"未知供应商: {provider_id}，可选: {', '.join(PROVIDER_IDS)}")
    return p


def resolve_key(provider_id: str, client_key: str | None = None) -> str:
    """客户端 key > 环境变量"""
    p = get_provider(provider_id)
    key = (client_key or "").strip()
    if key:
        return key
    key = os.environ.get(p["env_key"]) or ""
    return key.strip()


def _call_openai_compatible(provider: dict, api_key: str, model: str, system: str,
                            user: str, schema: dict, stream: bool) -> tuple[dict | None, Any]:
    """OpenAI-compatible 端点（DeepSeek / Qwen / MiMo）

    返回 (parsed_result | None, stream_iterable | None)
    """
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": stream,
    }
    # 结构化输出：function calling（兼容性最好）
    body["tools"] = [{
        "type": "function",
        "function": {
            "name": "classify",
            "description": "返回书签分类结果",
            "parameters": schema,
        },
    }]
    body["tool_choice"] = {"type": "function", "function": {"name": "classify"}}

    req = urllib.request.Request(
        provider["url"],
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            provider["auth_header"]: f"{provider['auth_prefix']}{api_key}",
        },
        method="POST",
    )

    if stream:
        resp = urllib.request.urlopen(req, timeout=180)
        return None, resp
    else:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

    # 解析 tool_calls
    choice = (payload.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    tool_calls = msg.get("tool_calls") or []
    for tc in tool_calls:
        func = tc.get("function") or {}
        if func.get("name") == "classify":
            args_str = func.get("arguments") or "{}"
            return json.loads(args_str) if isinstance(args_str, str) else args_str, None
    raise RuntimeError("模型未返回 function-call 结果")


def _call_anthropic(provider: dict, api_key: str, model: str, system: str,
                    user: str, schema: dict, stream: bool) -> tuple[dict | None, Any]:
    """Anthropic Messages API (tool_use 结构化)"""
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": 4096,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "tools": [{
            "name": "classify",
            "description": "返回书签分类结果",
            "input_schema": schema,
        }],
        "tool_choice": {"type": "tool", "name": "classify"},
    }
    if stream:
        body["stream"] = True

    req = urllib.request.Request(
        provider["url"],
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    if stream:
        resp = urllib.request.urlopen(req, timeout=180)
        return None, resp
    else:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

    for block in payload.get("content") or []:
        if block.get("type") == "tool_use" and block.get("name") == "classify":
            return block.get("input") or {}, None
    raise RuntimeError("模型未返回 tool_use 结果")


def call_structured(provider_id: str, system: str, user: str, schema: dict,
                    model: str | None = None, api_key: str | None = None,
                    stream: bool = False):
    """统一入口

    返回：
      stream=False → dict（结构化结果）
      stream=True  → HTTPResponse（可迭代 SSE chunks）
    """
    p = get_provider(provider_id)
    key = resolve_key(provider_id, api_key)
    if not key:
        raise RuntimeError(
            f"供应商 {p['label']} 未配置 API Key。"
            f"请设置环境变量 {p['env_key']} 或传入 api_key 参数"
        )
    m = model or p["default_model"]

    if p["protocol"] == "openai":
        result, stream_resp = _call_openai_compatible(p, key, m, system, user, schema, stream)
    else:
        result, stream_resp = _call_anthropic(p, key, m, system, user, schema, stream)

    if stream:
        return stream_resp
    return result


def parse_openai_sse_chunk(line: str) -> str | None:
    """从一行 SSE data 中抽取 content delta token（OpenAI 格式）。
    返回 token 字符串，或 None（非 data / [DONE] / 解析失败）。
    """
    if not line.startswith("data: "):
        return None
    payload = line[6:]
    if payload == "[DONE]":
        return None
    try:
        obj = json.loads(payload)
        delta = (obj.get("choices") or [{}])[0].get("delta") or {}
        tc = delta.get("tool_calls") or []
        for t in tc:
            func = t.get("function") or {}
            if func.get("arguments"):
                return func["arguments"]
        content = delta.get("content") or ""
        return content if content else None
    except Exception:
        return None


def parse_anthropic_sse_event(data: str) -> str | None:
    """从 Anthropic SSE event data 中抽取增量文本。"""
    try:
        obj = json.loads(data)
        if obj.get("type") == "content_block_delta":
            delta = obj.get("delta") or {}
            text = delta.get("text") or delta.get("partial_json") or ""
            return text if text else None
        if obj.get("type") == "message_stop":
            return None
    except Exception:
        pass
    return None


def iter_sse_tokens(response, protocol: str = "openai"):
    """迭代 SSE 流，yield 累积进度文本块。

    对 OpenAI 兼容：按行解析 SSE
    对 Anthropic：按事件解析 SSE
    """
    buf = ""
    while True:
        chunk = response.read(4096)
        if not chunk:
            break
        buf += chunk.decode("utf-8", errors="replace")

        if protocol == "openai":
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                token = parse_openai_sse_chunk(line)
                if token:
                    yield token
        else:
            # Anthropic SSE: event: ... \n data: {...}\n\n
            while "\n\n" in buf:
                event, buf = buf.split("\n\n", 1)
                for line in event.split("\n"):
                    if line.startswith("data: "):
                        token = parse_anthropic_sse_event(line[6:])
                        if token:
                            yield token
    # 剩余 buffer
    if buf.strip():
        if protocol == "openai":
            token = parse_openai_sse_chunk(buf.strip())
            if token:
                yield token
        else:
            token = parse_anthropic_sse_event(buf.strip().replace("data: ", "", 1))
            if token:
                yield token
