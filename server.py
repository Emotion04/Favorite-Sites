#!/usr/bin/env python3
"""EdgeFav Server - 静态文件服务 + REST API
启动: python server.py [--port 8000]
"""

import json
import os
import re
import sys
import tempfile
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, unquote

from classifier import (
    flatten_bookmarks, classify_bookmark, extract_tags,
    extract_domain, deduplicate, compute_stats, incremental_merge,
    parse_bookmarks_html,
)
from scheme_ai import (
    list_schemes, load_scheme, run_classification_stream, scheme_stats,
    facet_counts, has_api_key,
)
from providers import PROVIDERS, PROVIDER_IDS, get_provider
import hashlib
import threading

# 固定工作目录为 server.py 所在目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)

KB_DIR = os.path.join(SCRIPT_DIR, "kb")
os.makedirs(KB_DIR, exist_ok=True)


def atomic_write_json(filepath, data):
    """原子写入 JSON"""
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(filepath), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, filepath)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def send_json(handler, data, status=200):
    """发送 JSON 响应"""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", len(body))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def read_body(handler):
    """读取请求 body"""
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return None
    return json.loads(handler.rfile.read(length))


class EdgeFavHandler(SimpleHTTPRequestHandler):
    """自定义请求处理器"""

    def log_message(self, format, *args):
        sys.stderr.write(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}\n")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/kbs":
            return self.handle_list_kbs()

        if path == "/api/schemes":
            return self.handle_list_schemes()

        if path == "/api/ai/status":
            return send_json(self, {
                "providers": {
                    pid: {"label": get_provider(pid)["label"], "has_key": has_api_key(pid)}
                    for pid in PROVIDER_IDS
                },
                "active": os.environ.get("EDGEFAV_AI_PROVIDER", "deepseek"),
            })

        if path == "/api/providers":
            return send_json(self, {
                "providers": [
                    {"id": pid, "label": p["label"], "default_model": p["default_model"],
                     "has_key": has_api_key(pid)}
                    for pid, p in PROVIDERS.items()
                ],
                "active": os.environ.get("EDGEFAV_AI_PROVIDER", "deepseek"),
            })

        m = re.match(r"^/api/schemes/([^/]+)$", path)
        if m:
            return self.handle_get_scheme(unquote(m.group(1)))

        m = re.match(r"^/api/kb/([^/]+)/data$", path)
        if m:
            return self.handle_get_kb(unquote(m.group(1)))

        m = re.match(r"^/api/kb/([^/]+)/scheme-stats$", path)
        if m:
            return self.handle_scheme_stats(unquote(m.group(1)), parsed)

        m = re.match(r"^/api/kb/([^/]+)/facets$", path)
        if m:
            return self.handle_facets(unquote(m.group(1)), parsed)

        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        m = re.match(r"^/api/kb/([^/]+)/create$", path)
        if m:
            return self.handle_create_kb(unquote(m.group(1)))

        m = re.match(r"^/api/kb/([^/]+)/import$", path)
        if m:
            return self.handle_import_kb(unquote(m.group(1)))

        m = re.match(r"^/api/kb/([^/]+)/rename$", path)
        if m:
            return self.handle_rename_kb(unquote(m.group(1)))

        m = re.match(r"^/api/kb/([^/]+)/delete$", path)
        if m:
            return self.handle_delete_kb(unquote(m.group(1)))

        m = re.match(r"^/api/kb/([^/]+)/classify$", path)
        if m:
            return self.handle_classify(unquote(m.group(1)))

        send_json(self, {"error": "未知的 API 端点"}, 404)

    # ========== GET 处理器 ==========

    def handle_list_kbs(self):
        kbs = []
        if os.path.isdir(KB_DIR):
            for fname in sorted(os.listdir(KB_DIR)):
                if fname.endswith(".json") and not fname.startswith("_"):
                    fpath = os.path.join(KB_DIR, fname)
                    name = fname[:-5]
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        count = len(data.get("bookmarks", []))
                        cats = len(set(b.get("category", "") for b in data.get("bookmarks", [])))
                        mtime = datetime.fromtimestamp(os.path.getmtime(fpath)).isoformat()
                        kbs.append({"name": name, "bookmark_count": count, "category_count": cats, "last_modified": mtime})
                    except Exception:
                        kbs.append({"name": name, "bookmark_count": 0, "category_count": 0, "last_modified": None})

        if not any(k["name"] == "default" for k in kbs):
            kbs.insert(0, {"name": "default", "bookmark_count": 0, "category_count": 0, "last_modified": None})

        send_json(self, {"kbs": kbs})

    def handle_get_kb(self, name):
        filepath = os.path.join(KB_DIR, f"{name}.json")
        if not os.path.exists(filepath):
            send_json(self, {"error": f"知识库 '{name}' 不存在"}, 404)
            return
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        send_json(self, data)

    # ========== POST 处理器 ==========

    def handle_create_kb(self, name):
        if not re.match(r"^[\w一-鿿_-]+$", name):
            send_json(self, {"error": "知识库名称只能包含中文、字母、数字、下划线和连字符"}, 400)
            return

        filepath = os.path.join(KB_DIR, f"{name}.json")
        if os.path.exists(filepath):
            send_json(self, {"error": f"知识库 '{name}' 已存在"}, 409)
            return

        empty_kb = {
            "bookmarks": [], "folders": [],
            "stats": {"total_raw": 0, "total_dedup": 0, "duplicates": 0, "total_folders": 0, "unique_domains": 0},
            "generated": datetime.now().isoformat(),
        }
        atomic_write_json(filepath, empty_kb)
        send_json(self, {"status": "ok", "name": name, "message": f"知识库 '{name}' 已创建"}, 201)

    def handle_import_kb(self, name):
        body = read_body(self)
        if not body:
            send_json(self, {"error": "请求 body 为空"}, 400)
            return

        mode = body.get("mode", "new")
        raw_bookmarks = body.get("bookmarks", [])

        if not raw_bookmarks:
            send_json(self, {"error": "未提供书签数据"}, 400)
            return

        new_bookmarks = []
        new_folders = []

        if isinstance(raw_bookmarks, str) and "<!DOCTYPE NETSCAPE" in raw_bookmarks[:400]:
            # Edge 导出的 HTML 书签格式
            bms, fds = parse_bookmarks_html(raw_bookmarks)
            new_bookmarks.extend(bms)
            new_folders.extend(fds)
        elif isinstance(raw_bookmarks, dict) and "roots" in raw_bookmarks:
            for root_name, root_data in raw_bookmarks["roots"].items():
                bms, fds = flatten_bookmarks(root_data, root_name)
                new_bookmarks.extend(bms)
                new_folders.extend(fds)
        elif isinstance(raw_bookmarks, list):
            for bm in raw_bookmarks:
                if isinstance(bm, dict) and "url" in bm:
                    folder = bm.get("folder", "导入")
                    url = bm.get("url", "")
                    title = bm.get("title", "")
                    domain = bm.get("domain", "") or extract_domain(url)
                    category = bm.get("category") or classify_bookmark(url, title, folder)
                    tags = bm.get("tags") or extract_tags(title, url, domain, folder)
                    uid = hashlib.md5(f"{url}{title}".encode()).hexdigest()[:12]

                    new_bookmarks.append({
                        "id": bm.get("id", uid),
                        "title": title, "url": url, "domain": domain,
                        "folder": folder, "category": category, "tags": tags,
                        "date_added": bm.get("date_added") or datetime.now().strftime("%Y-%m-%d"),
                        "date_last_used": bm.get("date_last_used"),
                        "visit_count": bm.get("visit_count", 0),
                    })
        else:
            send_json(self, {"error": "无法识别的书签数据格式"}, 400)
            return

        if mode == "new":
            deduped = deduplicate(new_bookmarks)
            stats = compute_stats(deduped, new_folders)
            stats["total_raw"] = len(new_bookmarks)
            stats["duplicates"] = len(new_bookmarks) - len(deduped)
            kb_data = {"bookmarks": deduped, "folders": new_folders, "stats": stats, "generated": datetime.now().isoformat()}
            imported, skipped = len(deduped), len(new_bookmarks) - len(deduped)
            imported_ids = [b.get("id") for b in deduped if b.get("id")]

        elif mode == "merge":
            filepath = os.path.join(KB_DIR, f"{name}.json")
            if not os.path.exists(filepath):
                send_json(self, {"error": f"目标知识库 '{name}' 不存在，请先创建"}, 404)
                return

            with open(filepath, "r", encoding="utf-8") as f:
                existing = json.load(f)

            existing_bms = existing.get("bookmarks", [])
            new_deduped = deduplicate(new_bookmarks)
            to_import, to_skip = incremental_merge(existing_bms, new_deduped)

            imported, skipped = len(to_import), len(to_skip) + (len(new_bookmarks) - len(new_deduped))
            imported_ids = [b.get("id") for b in to_import if b.get("id")]

            for bm in to_import:
                if not bm.get("category") or bm["category"] == "其他":
                    bm["category"] = classify_bookmark(bm.get("url", ""), bm.get("title", ""), bm.get("folder", ""))
                if not bm.get("tags"):
                    bm["tags"] = extract_tags(bm.get("title", ""), bm.get("url", ""), bm.get("domain", ""), bm.get("folder", ""))

            combined = existing_bms + to_import
            existing_folders = existing.get("folders", [])
            existing_paths = {f["path"] for f in existing_folders}
            for fd in new_folders:
                if fd["path"] not in existing_paths:
                    existing_folders.append(fd)

            stats = compute_stats(combined, existing_folders)
            stats["duplicates"] = skipped
            kb_data = {"bookmarks": combined, "folders": existing_folders, "stats": stats, "generated": datetime.now().isoformat()}

        else:
            send_json(self, {"error": f"无效的导入模式: {mode}（应为 new 或 merge）"}, 400)
            return

        filepath = os.path.join(KB_DIR, f"{name}.json")
        atomic_write_json(filepath, kb_data)

        send_json(self, {
            "status": "ok",
            "imported": imported,
            "skipped": skipped,
            "total": imported + skipped,
            "kb_total": len(kb_data["bookmarks"]),
            "imported_ids": imported_ids,
        })

    def handle_rename_kb(self, name):
        """重命名 KB"""
        body = read_body(self)
        if not body:
            send_json(self, {"error": "请求 body 为空"}, 400)
            return
        new_name = body.get("new_name", "").strip()
        if not new_name:
            send_json(self, {"error": "新名称不能为空"}, 400)
            return
        if not re.match(r"^[\w一-鿿_-]+$", new_name):
            send_json(self, {"error": "名称只能包含中文、字母、数字、下划线和连字符"}, 400)
            return

        old_path = os.path.join(KB_DIR, f"{name}.json")
        new_path = os.path.join(KB_DIR, f"{new_name}.json")
        if not os.path.exists(old_path):
            send_json(self, {"error": f"知识库 '{name}' 不存在"}, 404)
            return
        if os.path.exists(new_path):
            send_json(self, {"error": f"知识库 '{new_name}' 已存在"}, 409)
            return

        os.rename(old_path, new_path)
        send_json(self, {"status": "ok", "old_name": name, "new_name": new_name})

    def handle_delete_kb(self, name):
        """删除 KB"""
        if name == "default":
            send_json(self, {"error": "不能删除默认知识库"}, 400)
            return
        filepath = os.path.join(KB_DIR, f"{name}.json")
        if not os.path.exists(filepath):
            send_json(self, {"error": f"知识库 '{name}' 不存在"}, 404)
            return
        os.remove(filepath)
        send_json(self, {"status": "ok", "name": name, "message": f"知识库 '{name}' 已删除"})

    # ========== Scheme + AI ==========

    def handle_list_schemes(self):
        send_json(self, {"schemes": list_schemes(), "has_api_key": has_api_key()})

    def handle_get_scheme(self, scheme_id):
        try:
            data = load_scheme(scheme_id)
            send_json(self, data)
        except FileNotFoundError as e:
            send_json(self, {"error": str(e)}, 404)

    def handle_scheme_stats(self, kb_name, parsed):
        from urllib.parse import parse_qs
        qs = parse_qs(parsed.query)
        scheme_id = (qs.get("scheme") or ["default"])[0]
        filepath = os.path.join(KB_DIR, f"{kb_name}.json")
        if not os.path.exists(filepath):
            send_json(self, {"error": f"知识库 '{kb_name}' 不存在"}, 404)
            return
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        send_json(self, scheme_stats(data.get("bookmarks") or [], scheme_id))

    def handle_facets(self, kb_name, parsed):
        from urllib.parse import parse_qs
        qs = parse_qs(parsed.query)
        scheme_id = (qs.get("scheme") or ["default"])[0]
        dimension = (qs.get("dimension") or [None])[0]
        filepath = os.path.join(KB_DIR, f"{kb_name}.json")
        if not os.path.exists(filepath):
            send_json(self, {"error": f"知识库 '{kb_name}' 不存在"}, 404)
            return
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        bms = data.get("bookmarks") or []
        try:
            scheme = load_scheme(scheme_id)
        except FileNotFoundError as e:
            send_json(self, {"error": str(e)}, 404)
            return
        dims = scheme.get("dimensions") or {}
        if dimension:
            if dimension not in dims:
                send_json(self, {"error": f"维度 '{dimension}' 不在方案中"}, 400)
                return
            send_json(self, {"scheme_id": scheme_id, "dimension": dimension, "counts": facet_counts(bms, scheme_id, dimension)})
            return
        all_counts = {k: facet_counts(bms, scheme_id, k) for k in dims}
        send_json(self, {"scheme_id": scheme_id, "facets": all_counts, "stats": scheme_stats(bms, scheme_id)})

    def handle_classify(self, kb_name):
        """用户手动触发 AI 分类（SSE 流式进度）
        body: { scheme_id, mode, ids?, provider_id?, api_key?, model? }
        返回 SSE 事件流，或 JSON（通过 ?sse=0 跳过流）
        """
        parsed = urlparse(self.path)
        from urllib.parse import parse_qs
        qs = parse_qs(parsed.query)
        use_sse = qs.get("sse", ["1"])[0] != "0"

        body = read_body(self) or {}
        scheme_id = (body.get("scheme_id") or "default").strip()
        mode = (body.get("mode") or "unclassified").strip()
        ids = body.get("ids")
        provider_id = (body.get("provider_id") or os.environ.get("EDGEFAV_AI_PROVIDER", "deepseek")).strip()
        api_key = body.get("api_key") or None
        model = body.get("model") or None

        if mode not in ("all", "unclassified", "ids"):
            return send_json(self, {"error": "mode 必须是 all / unclassified / ids"}, 400)
        if mode == "ids" and not ids:
            return send_json(self, {"error": "mode=ids 时需要提供 ids 列表"}, 400)

        try:
            load_scheme(scheme_id)
        except FileNotFoundError as e:
            return send_json(self, {"error": str(e)}, 404)

        filepath = os.path.join(KB_DIR, f"{kb_name}.json")
        if not os.path.exists(filepath):
            return send_json(self, {"error": f"知识库 '{kb_name}' 不存在"}, 404)

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        bookmarks = data.get("bookmarks") or []

        if not use_sse:
            # 传统阻塞模式
            import scheme_ai
            try:
                result = scheme_ai.run_classification_stream(
                    bookmarks, scheme_id, provider_id=provider_id,
                    api_key=api_key, model=model, mode=mode, ids=ids,
                )
            except Exception as e:
                return send_json(self, {"error": str(e)}, 500)
            data["bookmarks"] = bookmarks
            data["generated"] = datetime.now().isoformat()
            atomic_write_json(filepath, data)
            result["status"] = "ok"
            return send_json(self, result)

        # ── SSE 流式模式 ──
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        lock = threading.Lock()

        def emit(event: str, payload: dict):
            with lock:
                line = f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                try:
                    self.wfile.write(line.encode("utf-8"))
                    self.wfile.flush()
                except Exception:
                    pass

        try:
            run_classification_stream(
                bookmarks, scheme_id,
                provider_id=provider_id, api_key=api_key, model=model,
                mode=mode, ids=ids,
                on_progress=emit,
            )
        except Exception as e:
            emit("error", {"error": str(e)})
        finally:
            # 写入
            data["bookmarks"] = bookmarks
            data["generated"] = datetime.now().isoformat()
            atomic_write_json(filepath, data)
            emit("saved", {"message": "已保存"})
            emit("end", {})


def main():
    port = 8000
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
        elif a.isdigit():
            port = int(a)

    default_kb = os.path.join(KB_DIR, "default.json")
    if not os.path.exists(default_kb):
        print("⚠️  kb/default.json 不存在，请先运行: python extract.py")
        empty = {
            "bookmarks": [], "folders": [],
            "stats": {"total_raw": 0, "total_dedup": 0, "duplicates": 0, "total_folders": 0, "unique_domains": 0},
            "generated": datetime.now().isoformat(),
        }
        atomic_write_json(default_kb, empty)
        print("   已创建空的 kb/default.json")

    server = HTTPServer(("0.0.0.0", port), EdgeFavHandler)
    print(f"\n🚀 EdgeFav Server 已启动")
    print(f"   工作目录: {SCRIPT_DIR}")
    print(f"   📖 前端:    http://localhost:{port}")
    print(f"   📋 KB列表:  http://localhost:{port}/api/kbs")
    print(f"   📐 方案:    http://localhost:{port}/api/schemes")
    print(f"   🤖 AI Key:  {'已配置' if has_api_key() else '未配置（设置 ANTHROPIC_API_KEY）'}")
    print(f"\n   按 Ctrl+C 停止\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 服务器已停止")


if __name__ == "__main__":
    main()
