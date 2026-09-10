#!/usr/bin/env python3
"""分类方案 + AI 结构化分类（多供应商 JSON Schema）
- Anthropic / DeepSeek / Qwen / MiMo 统一路由
- 不设置 temperature
- 仅用户手动触发
- 支持 SSE 流式进度
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Callable

from providers import (
    call_structured, PROVIDER_IDS, get_provider, resolve_key,
    iter_sse_tokens, parse_openai_sse_chunk, parse_anthropic_sse_event,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEMES_DIR = os.path.join(SCRIPT_DIR, "schemes")
os.makedirs(SCHEMES_DIR, exist_ok=True)

BATCH_SIZE = 12


# ── Scheme I/O ──────────────────────────────────────────────

def list_schemes() -> list[dict]:
    schemes = []
    if not os.path.isdir(SCHEMES_DIR):
        return schemes
    for fname in sorted(os.listdir(SCHEMES_DIR)):
        if not fname.endswith(".json") or fname.startswith("_"):
            continue
        path = os.path.join(SCHEMES_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            schemes.append({
                "id": data.get("id", fname[:-5]),
                "name": data.get("name", fname[:-5]),
                "description": data.get("description", ""),
                "dimensions": data.get("dimensions", {}),
            })
        except Exception:
            schemes.append({"id": fname[:-5], "name": fname[:-5], "description": "load error", "dimensions": {}})
    return schemes


def load_scheme(scheme_id: str) -> dict:
    path = os.path.join(SCHEMES_DIR, f"{scheme_id}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"分类方案 '{scheme_id}' 不存在")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── JSON Schema 构建 ────────────────────────────────────────

def build_item_json_schema(scheme: dict) -> dict:
    dims = scheme.get("dimensions") or {}
    props: dict[str, Any] = {"id": {"type": "string", "description": "书签 id，必须与输入一致"}}
    required = ["id"]
    for dim_key, dim in dims.items():
        enums = dim.get("enum") or []
        multi = bool(dim.get("multi"))
        max_items = int(dim.get("max_items") or 3)
        if multi:
            props[dim_key] = {
                "type": "array", "items": {"type": "string", "enum": enums},
                "minItems": 1, "maxItems": max_items,
                "description": dim.get("label", dim_key),
            }
        else:
            props[dim_key] = {"type": "string", "enum": enums, "description": dim.get("label", dim_key)}
        required.append(dim_key)
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def build_batch_json_schema(scheme: dict) -> dict:
    return {
        "type": "object",
        "properties": {"items": {"type": "array", "items": build_item_json_schema(scheme)}},
        "required": ["items"],
        "additionalProperties": False,
    }


# ── 校验 ────────────────────────────────────────────────────

def validate_and_normalize(item: dict, scheme: dict, allowed_ids: set[str] | None = None) -> dict | None:
    if not isinstance(item, dict):
        return None
    bid = str(item.get("id") or "")
    if not bid or (allowed_ids is not None and bid not in allowed_ids):
        return None
    dims = scheme.get("dimensions") or {}
    out: dict[str, Any] = {}
    for dim_key, dim in dims.items():
        enums = set(dim.get("enum") or [])
        multi = bool(dim.get("multi"))
        val = item.get(dim_key)
        if multi:
            if not isinstance(val, list):
                val = [val] if val is not None else []
            cleaned = [v for v in val if v in enums]
            if not cleaned:
                fallback = "其他" if "其他" in enums else (next(iter(enums), None))
                cleaned = [fallback] if fallback else []
            max_items = int(dim.get("max_items") or 3)
            out[dim_key] = cleaned[:max_items]
        else:
            if val not in enums:
                val = "其他" if "其他" in enums else (next(iter(enums), None))
            out[dim_key] = val
    return {"id": bid, "values": out}


# ── AI 调用 ─────────────────────────────────────────────────

def classify_batch_ai(bookmarks: list[dict], scheme: dict,
                      provider_id: str = "deepseek",
                      api_key: str | None = None,
                      model: str | None = None) -> list[dict]:
    """对一批书签做 AI 分类（非流式）"""
    if not bookmarks:
        return []

    schema = build_batch_json_schema(scheme)
    dims = scheme.get("dimensions") or {}
    dim_desc = []
    for k, d in dims.items():
        multi = "多选" if d.get("multi") else "单选"
        dim_desc.append(f"- {k}（{d.get('label', k)}，{multi}）可选值: {', '.join(d.get('enum') or [])}")

    system = (
        "你是书签知识库分类器。只通过 function call 返回结构化结果，不要输出解释。"
        "每条结果的 id 必须与输入 id 完全一致。"
        + (f"\n{scheme.get('prompt_hint', '')}" if scheme.get("prompt_hint") else "")
    )
    lines = ["请为以下书签填写分类字段：\n", "\n".join(dim_desc), "\n书签列表："]
    for b in bookmarks:
        lines.append(
            f"- id={b.get('id')} | title={b.get('title','')} | domain={b.get('domain','')} "
            f"| folder={b.get('folder','')} | url={b.get('url','')[:120]}"
        )
    user = "\n".join(lines)

    raw = call_structured(provider_id, system, user, schema, model=model, api_key=api_key, stream=False)
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []

    allowed = {str(b.get("id")) for b in bookmarks}
    return [n for it in items if (n := validate_and_normalize(it, scheme, allowed))]


# ── 流式进度 ────────────────────────────────────────────────

def run_classification_stream(
    bookmarks: list[dict],
    scheme_id: str,
    provider_id: str = "deepseek",
    api_key: str | None = None,
    model: str | None = None,
    mode: str = "unclassified",
    ids: list[str] | None = None,
    on_progress: Callable[[str, dict], None] | None = None,
) -> dict:
    """执行分类任务，通过 on_progress(event, data) 推送流式事件。

    事件类型：
      - "start": {total, batches}
      - "batch_start": {batch, of}
      - "batch_token": {batch, token}   (流式，仅 OpenAI 兼容供应商)
      - "batch_done": {batch, items, of}
      - "batch_error": {batch, error}
      - "done": {updated, total, errors, stats}
    """
    scheme = load_scheme(scheme_id)
    targets = select_targets(bookmarks, scheme_id, mode, ids)
    total = len(targets)
    if total == 0:
        result = {"updated": 0, "total": 0, "batches": 0, "errors": [],
                  "stats": scheme_stats(bookmarks, scheme_id)}
        if on_progress:
            on_progress("done", result)
        return result

    batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    if on_progress:
        on_progress("start", {"total": total, "batches": batches})

    all_classified: list[dict] = []
    errors: list[str] = []
    provider = get_provider(provider_id)
    protocol = provider["protocol"]

    for bi in range(batches):
        chunk = targets[bi * BATCH_SIZE : (bi + 1) * BATCH_SIZE]
        if on_progress:
            on_progress("batch_start", {"batch": bi + 1, "of": batches, "count": len(chunk)})

        try:
            if protocol == "openai":
                # 尝试流式调用来推 token 事件
                items = _classify_batch_streamed(chunk, scheme, provider_id, api_key, model, bi + 1, on_progress)
            else:
                items = classify_batch_ai(chunk, scheme, provider_id, api_key, model)
                if on_progress:
                    on_progress("batch_done", {"batch": bi + 1, "of": batches, "items": len(items)})
            all_classified.extend(items)
        except Exception as e:
            err_msg = f"batch {bi+1}: {e}"
            errors.append(err_msg)
            if on_progress:
                on_progress("batch_error", {"batch": bi + 1, "error": str(e)})

    updated = apply_classifications(bookmarks, scheme_id, all_classified, source="ai")
    result = {
        "updated": updated, "total": total,
        "batches": batches, "errors": errors,
        "scheme_id": scheme_id, "mode": mode,
        "stats": scheme_stats(bookmarks, scheme_id),
    }
    if on_progress:
        on_progress("done", result)
    return result


def _classify_batch_streamed(bookmarks, scheme, provider_id, api_key, model, batch_no, on_progress):
    """OpenAI 兼容端点：流式调用，实时推送 token 到 on_progress"""
    schema = build_batch_json_schema(scheme)
    dims = scheme.get("dimensions") or {}
    dim_desc = []
    for k, d in dims.items():
        multi = "多选" if d.get("multi") else "单选"
        dim_desc.append(f"- {k}（{d.get('label', k)}，{multi}）可选值: {', '.join(d.get('enum') or [])}")

    system = (
        "你是书签知识库分类器。只通过 function call 返回结构化 JSON 结果，不要输出解释。"
        "每条结果的 id 必须与输入 id 完全一致。"
        + (f"\n{scheme.get('prompt_hint', '')}" if scheme.get("prompt_hint") else "")
    )
    lines = ["请为以下书签填写分类字段：\n", "\n".join(dim_desc), "\n书签列表："]
    for b in bookmarks:
        lines.append(
            f"- id={b.get('id')} | title={b.get('title','')} | domain={b.get('domain','')} "
            f"| folder={b.get('folder','')} | url={b.get('url','')[:120]}"
        )
    user = "\n".join(lines)

    try:
        resp = call_structured(provider_id, system, user, schema,
                               model=model, api_key=api_key, stream=True)
    except Exception as e:
        raise RuntimeError(f"流式调用失败: {e}") from e

    accumulated = ""
    for token in iter_sse_tokens(resp, "openai"):
        accumulated += token
        if on_progress:
            on_progress("batch_token", {"batch": batch_no, "token": token})

    # 最终尝试从累积参数里解析
    try:
        raw = json.loads(accumulated) if accumulated.strip() else {}
    except json.JSONDecodeError:
        # 尝试提取最后一个完整 JSON
        raw = _extract_json_from_partial(accumulated)

    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        items = []

    allowed = {str(b.get("id")) for b in bookmarks}
    results = [n for it in items if (n := validate_and_normalize(it, scheme, allowed))]

    if on_progress:
        on_progress("batch_done", {"batch": batch_no, "items": len(results)})
    return results


def _extract_json_from_partial(text: str) -> dict:
    """从部分流文本中尽力提取 JSON"""
    # 找最外层 {}
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {}


# ── 目标选择 ────────────────────────────────────────────────

def select_targets(bookmarks: list[dict], scheme_id: str, mode: str,
                   ids: list[str] | None = None) -> list[dict]:
    if mode == "ids":
        idset = set(ids or [])
        return [b for b in bookmarks if b.get("id") in idset]
    if mode == "unclassified":
        return [b for b in bookmarks if not (b.get("classifications") or {}).get(scheme_id)]
    return list(bookmarks)


# ── 应用结果 ────────────────────────────────────────────────

def apply_classifications(bookmarks: list[dict], scheme_id: str,
                          classified: list[dict], source: str = "ai") -> int:
    by_id = {c["id"]: c["values"] for c in classified}
    now = datetime.now().isoformat(timespec="seconds")
    n = 0
    for b in bookmarks:
        bid = b.get("id")
        if bid not in by_id:
            continue
        if not isinstance(b.get("classifications"), dict):
            b["classifications"] = {}
        b["classifications"][scheme_id] = {
            "values": by_id[bid],
            "source": source,
            "updated_at": now,
        }
        vals = by_id[bid]
        if "category" in vals and isinstance(vals["category"], str):
            b["category"] = vals["category"]
        n += 1
    return n


# ── 统计 ────────────────────────────────────────────────────

def has_api_key(provider_id: str | None = None, client_key: str | None = None) -> bool:
    if provider_id:
        try:
            return bool(resolve_key(provider_id, client_key))
        except ValueError:
            return False
    # any provider
    for pid in PROVIDER_IDS:
        try:
            if resolve_key(pid, None):
                return True
        except ValueError:
            continue
    return False


def scheme_stats(bookmarks: list[dict], scheme_id: str) -> dict:
    classified = sum(
        1 for b in bookmarks
        if (b.get("classifications") or {}).get(scheme_id)
    )
    total = len(bookmarks)
    return {"scheme_id": scheme_id, "total": total, "classified": classified, "unclassified": total - classified}


def facet_counts(bookmarks: list[dict], scheme_id: str, dimension: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for b in bookmarks:
        cls = (b.get("classifications") or {}).get(scheme_id) or {}
        vals = cls.get("values") or {}
        v = vals.get(dimension)
        if v is None:
            if dimension == "category" and b.get("category"):
                v = b["category"]
            else:
                continue
        if isinstance(v, list):
            for item in v:
                counts[item] = counts.get(item, 0) + 1
        else:
            counts[v] = counts.get(v, 0) + 1
    return counts
