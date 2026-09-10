#!/usr/bin/env python3
"""Edge 收藏夹知识库 - 解析、分类、导出脚本
用法:
  python extract.py                          # 默认: 导出到 kb/default.json
  python extract.py --kb-name work           # 导出到 kb/work.json
  python extract.py --merge kb/default.json  # 增量合并到已有 KB
"""

import json
import os
import sys
import argparse
from collections import Counter
from datetime import datetime

from classifier import (
    flatten_bookmarks, deduplicate, compute_stats, incremental_merge,
)

BOOKMARKS_PATH = os.path.expandvars(
    r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Bookmarks"
)
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
KB_DIR = os.path.join(OUTPUT_DIR, "kb")


def generate_report(bookmarks, folders, stats):
    """生成分析报告"""
    cat_counts = Counter(b["category"] for b in bookmarks)
    domain_counts = Counter(b["domain"] for b in bookmarks)
    tag_counts = Counter()
    for b in bookmarks:
        for t in b["tags"]:
            tag_counts[t] += 1

    report = f"""# Edge 收藏夹知识库 - 分析报告

> 生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## 📊 概览统计

| 指标 | 数量 |
|------|------|
| 原始书签总数 | {stats['total_raw']} |
| 去重后书签数 | {stats['total_dedup']} |
| 重复书签数 | {stats['duplicates']} |
| 文件夹数 | {stats['total_folders']} |
| 唯一域名数 | {stats['unique_domains']} |

## 📂 分类分布

| 分类 | 数量 |
|------|------|
"""
    for cat, count in cat_counts.most_common():
        report += f"| {cat} | {count} |\n"

    report += f"""
## 🏷️ 热门标签 (Top 20)

| 标签 | 数量 |
|------|------|
"""
    for tag, count in tag_counts.most_common(20):
        report += f"| {tag} | {count} |\n"

    report += f"""
## 🌐 热门域名 (Top 20)

| 域名 | 数量 |
|------|------|
"""
    for domain, count in domain_counts.most_common(20):
        report += f"| {domain} | {count} |\n"

    report += """
## 📁 文件夹结构

```
"""
    for folder in sorted(folders, key=lambda f: (f["depth"], f["path"])):
        indent = "  " * folder["depth"]
        report += f"{indent}├── {folder['name']}/\n"
    report += "```\n"

    return report


def export_markdown(bookmarks, output_dir):
    """导出按分类的 Markdown 文件"""
    by_category = {}
    for bm in bookmarks:
        cat = bm["category"]
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(bm)

    os.makedirs(output_dir, exist_ok=True)

    for cat, items in sorted(by_category.items()):
        cat_file = os.path.join(output_dir, f"{cat}.md")
        with open(cat_file, "w", encoding="utf-8") as f:
            f.write(f"# {cat}\n\n")
            f.write(f"> {len(items)} 个书签\n\n")
            f.write("| # | 标题 | 域名 | 标签 |\n")
            f.write("|---|------|------|------|\n")
            for i, item in enumerate(items, 1):
                tags = ", ".join(item["tags"][:5]) if item["tags"] else "-"
                f.write(f"| {i} | [{item['title']}]({item['url']}) | {item['domain']} | {tags} |\n")

    # 索引
    index_path = os.path.join(output_dir, "INDEX.md")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("# 📚 Edge 收藏夹知识库\n\n")
        f.write(f"> 共 {len(bookmarks)} 个书签，{len(by_category)} 个分类\n\n")
        f.write("## 分类导航\n\n")
        for cat, items in sorted(by_category.items()):
            f.write(f"- **[{cat}]({cat}.md)** ({len(items)} 条)\n")


def main():
    parser = argparse.ArgumentParser(description="Edge 收藏夹知识库 - 解析、分类、导出")
    parser.add_argument("--kb-name", "-n", type=str, default="default",
                        help="知识库名称 (默认: default)")
    parser.add_argument("--merge", "-m", type=str, default=None,
                        help="合并到已有 KB JSON 文件路径（增量模式）")
    parser.add_argument("--input", "-i", type=str, default=BOOKMARKS_PATH,
                        help=f"Edge Bookmarks JSON 文件路径")
    parser.add_argument("--no-report", action="store_true",
                        help="不生成 REPORT.md")
    parser.add_argument("--no-markdown", action="store_true",
                        help="不生成 knowledge-base/ Markdown 文件")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"❌ 书签文件不存在: {args.input}")
        sys.exit(1)

    print("🔍 正在读取 Edge 收藏夹...")
    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)

    roots = data.get("roots", {})

    # 提取
    all_bookmarks = []
    all_folders = []
    for root_name, root_data in roots.items():
        bms, fds = flatten_bookmarks(root_data, root_name)
        all_bookmarks.extend(bms)
        all_folders.extend(fds)

    total_raw = len(all_bookmarks)
    print(f"   ✅ 提取了 {total_raw} 个书签, {len(all_folders)} 个文件夹")

    # 去重
    deduped = deduplicate(all_bookmarks)
    dup_count = total_raw - len(deduped)

    # ===== 增量合并模式 =====
    if args.merge:
        if not os.path.exists(args.merge):
            print(f"❌ 目标 KB 文件不存在: {args.merge}")
            sys.exit(1)

        with open(args.merge, "r", encoding="utf-8") as f:
            existing = json.load(f)

        existing_bms = existing.get("bookmarks", [])
        to_import, to_skip = incremental_merge(existing_bms, deduped)

        combined = existing_bms + to_import
        new_dup = dup_count + len(to_skip)
        stats = compute_stats(combined, all_folders)
        stats["total_raw"] = total_raw + existing["stats"].get("total_raw", len(existing_bms))
        stats["duplicates"] = new_dup

        print(f"   📥 新增: {len(to_import)} 条, 跳过: {len(to_skip)} 条 (重复)")
        print(f"   📦 合并后总数: {len(combined)} 条")

        # 写回原文件
        output_data = {
            "bookmarks": combined,
            "folders": existing.get("folders", []) + [
                f for f in all_folders
                if f["path"] not in {ef["path"] for ef in existing.get("folders", [])}
            ],
            "stats": stats,
            "generated": datetime.now().isoformat(),
        }

        merge_output = args.merge
        with open(merge_output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"   💾 已写入: {merge_output}")

        if not args.no_markdown:
            md_dir = os.path.join(OUTPUT_DIR, "knowledge-base")
            export_markdown(combined, md_dir)
            print(f"   📂 Markdown: {md_dir}/")
    else:
        # ===== 普通导出模式 =====
        stats = compute_stats(deduped, all_folders)
        stats["total_raw"] = total_raw
        stats["duplicates"] = dup_count

        # 导出 JSON 到 kb/
        os.makedirs(KB_DIR, exist_ok=True)
        kb_path = os.path.join(KB_DIR, f"{args.kb_name}.json")
        output_data = {
            "bookmarks": deduped,
            "folders": all_folders,
            "stats": stats,
            "generated": datetime.now().isoformat(),
        }
        with open(kb_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"   📦 KB JSON: {kb_path}")

        # 也生成兼容旧版的 data.js
        js_path = os.path.join(OUTPUT_DIR, "data.js")
        json_str = json.dumps(output_data, ensure_ascii=False, indent=2)
        with open(js_path, "w", encoding="utf-8") as f:
            f.write(f"// Edge 收藏夹知识库数据 - 自动生成\nvar KB_DATA = {json_str};\n")
        print(f"   📦 兼容文件: {js_path}")

        # Markdown
        if not args.no_markdown:
            md_dir = os.path.join(OUTPUT_DIR, "knowledge-base")
            export_markdown(deduped, md_dir)
            print(f"   📂 Markdown: {md_dir}/")

        # Report
        if not args.no_report:
            report = generate_report(deduped, all_folders, stats)
            report_path = os.path.join(OUTPUT_DIR, "REPORT.md")
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"   📝 报告: {report_path}")

    print(f"\n🎉 完成！{len(deduped)} 条知识库条目")


if __name__ == "__main__":
    main()
