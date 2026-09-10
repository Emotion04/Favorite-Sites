#!/usr/bin/env python3
"""Edge 收藏夹知识库 - 共享分类模块
供 extract.py 和 server.py 共同使用
"""

from collections import Counter
from datetime import datetime
from urllib.parse import urlparse
from html.parser import HTMLParser
import hashlib
import re

# ========== 分类规则 ==========
# 基于域名和标题关键词的智能分类（rule-based，非 AI）
CATEGORY_RULES = {
    "编程开发": {
        "domains": [
            "github.com", "gitlab.com", "gitee.com", "stackoverflow.com",
            "stackexchange.com", "npmjs.com", "pypi.org", "crates.io",
            "codepen.io", "jsfiddle.net", "codesandbox.io", "replit.com",
            "leetcode.cn", "leetcode.com", "nowcoder.com", "lintcode.com",
            "luogu.com.cn", "hexo.io", "usaco.org", "screeps.com",
            "csdn.net", "cnblogs.com", "juejin.cn", "segmentfault.com",
            "51cto.com", "infoq.cn", "oschina.net", "v2ex.com",
            "stackoverflow.org.cn", "qiita.com",
            "keil.com", "arm.com", "st.com.cn",
            "ncccu.org.cn", "typing.io", "typingclub.com",
            "dushu.com", "zhidao.baidu.com",
        ],
        "keywords": [
            "代码", "编程", "开发", "算法", "数据结构", "设计模式",
            "git", "github", "开源", "源码", "框架", "库", "sdk",
            "api", "rest", "graphql", "docker", "kubernetes", "devops",
            "前端", "后端", "全栈", "web", "react", "vue", "angular",
            "node", "deno", "bun", "java", "python", "rust", "go",
            "cpp", "c++", "c语言", "typescript", "javascript",
            "编译", "debug", "调试", "ide", "vscode", "jetbrains",
            "qt", "mingw", "msys2", "gcc", "cmake", "makefile",
            "linux", "shell", "bash", "powershell", "terminal",
            "嵌入式", "单片机", "stm32", "arm", "keil", "mdk",
            "洛谷", "算法竞赛", "信息学", "acm", "noi", "usaco",
            "hexo", "博客", "搭建", "github pages",
            "程序员", "程序猿", "软件工程师",
        ],
    },
    "AI与机器学习": {
        "domains": [
            "huggingface.co", "openai.com", "anthropic.com", "deepmind.com",
            "pytorch.org", "tensorflow.org", "kaggle.com", "modelscope.cn",
            "aistudio.baidu.com", "jupyter.org", "colab.research.google.com",
        ],
        "keywords": [
            "ai", "人工智能", "机器学习", "深度学习", "神经网络",
            "nlp", "cv", "计算机视觉", "transformer", "llm", "大模型",
            "chatgpt", "gpt", "claude", "llama", "stable diffusion",
            "训练", "推理", "微调", "fine-tune", "prompt", "embedding",
            "向量", "rag", "agent", "智能体", "copilot", "cursor",
            "机器学习", "数据挖掘", "自然语言", "图像识别",
            "generative", "生成式", "aigc", "大语言模型",
        ],
    },
    "学习教程": {
        "domains": [
            "icourse163.org", "xuetangx.com", "chaoxing.com",
            "youtube.com", "coursera.org", "edx.org",
            "udemy.com", "pluralsight.com", "khanacademy.org",
            "w3school.com.cn", "runoob.com", "yiibai.com",
            "imooc.com", "shiyanlou.com", "educoder.net",
            "cs50.dev", "tonycrane.cc", "bowling233.top",
        ],
        "keywords": [
            "教程", "教学", "课程", "入门", "学习", "笔记", "总结",
            "讲解", "详解", "实战", "项目", "案例", "示例", "demo",
            "mooc", "慕课", "视频", "公开课", "讲座", "课件",
            "习题", "练习", "考试", "认证", "证书",
            "计算机组成", "计算机网络", "操作系统", "数据库",
            "编译原理", "体系结构", "微机", "嵌入式",
            "辅学", "学长", "cs人", "图灵", "竺院",
            "刷题", "题单", "面试", "面经", "笔试题",
        ],
    },
    "工具与效率": {
        "domains": [
            "tool.lu", "tool.oschina.net", "json.cn", "sojson.com",
            "bejson.com", "regex101.com", "processon.com", "draw.io",
            "excalidraw.com", "figma.com", "canva.com",
            "translate.google.com", "fanyi.baidu.com", "deepl.com",
            "cn.bing.com", "bing.com", "google.com", "baidu.com",
            "protonvpn.com", "proton.me", "mullvad.net",
            "apps.microsoft.com",
        ],
        "keywords": [
            "工具", "在线", "转换", "生成", "格式化", "压缩", "解析",
            "下载", "解析器", "爬虫", "抓取", "采集",
            "效率", "快捷", "实用", "神器", "推荐", "合集",
            "油猴", "tampermonkey", "插件", "扩展", "脚本",
            "自动化", "批量", "处理",
            "vpn", "代理", "翻墙", "梯子", "科学上网",
            "搜索引擎", "搜索",
        ],
    },
    "云服务与部署": {
        "domains": [
            "console.cloud.tencent.com", "console.huaweicloud.com",
            "console.aliyun.com", "console.aws.amazon.com",
            "cloud.baidu.com", "vercel.com", "netlify.com",
            "heroku.com", "railway.app", "render.com", "fly.io",
            "dash.cloudflare.com", "cloudflare.com",
            "buy.cloud.tencent.com", "aliyun.com",
        ],
        "keywords": [
            "云", "服务器", "vps", "域名", "备案", "部署", "上线",
            "cdn", "dns", "ssl", "https", "证书", "nginx", "apache",
            "监控", "运维", "日志", "备份", "恢复",
            "轻量", "ecs", "oss", "cos", "对象存储",
            "cloudflare",
        ],
    },
    "社区论坛": {
        "domains": [
            "zhihu.com", "zhuanlan.zhihu.com", "weixin.qq.com",
            "tieba.baidu.com", "douban.com", "weibo.com",
            "coolapk.com", "chongbuluo.com",
            "reddit.com", "hackernews.com", "producthunt.com",
            "linux.do", "nodeseek.com", "hostloc.com",
            "sspai.com", "jianshu.com", "geekpark.net",
            "news.ycombinator.com", "medium.com",
            "baijiahao.baidu.com", "m.thepaper.cn",
        ],
        "keywords": [
            "论坛", "社区", "问答", "讨论", "分享", "交流",
            "帖子", "主题", "回复", "评论", "热帖", "精华",
            "知乎", "贴吧", "豆瓣", "微博", "v2ex",
            "酷安", "吾爱破解", "52破解",
            "少数派", "简书", "果壳", "小众",
        ],
    },
    "文档与参考": {
        "domains": [
            "developer.mozilla.org", "docs.microsoft.com",
            "learn.microsoft.com", "docs.python.org", "nodejs.org",
            "developer.huawei.com", "developer.android.com",
            "developer.apple.com", "cloud.tencent.com/document",
        ],
        "keywords": [
            "文档", "手册", "参考", "api", "标准", "规范", "协议",
            "rfc", "官方", "指南", "文档", "cheatsheet", "速查",
            "spec", "specification", "reference", "guide",
        ],
    },
    "论文与学术": {
        "domains": [
            "arxiv.org", "scholar.google.com", "cnki.net",
            "wanfangdata.com", "ieee.org", "acm.org",
            "sci-hub", "paperwithcode.com", "semanticscholar.org",
        ],
        "keywords": [
            "论文", "文献", "学术", "研究", "paper", "journal",
            "conference", "preprint", "arxiv", "survey", "综述",
            "毕设", "毕业设计", "毕业论文",
        ],
    },
    "求职招聘": {
        "domains": [
            "zhaopin.com", "51job.com", "liepin.com", "boss.com",
            "lagou.com", "jobui.com", "linkedin.com", "indeed.com",
        ],
        "keywords": [
            "招聘", "求职", "面试", "简历", "实习", "校招", "社招",
            "offer", "薪资", "面经", "笔试", "内推", "春招", "秋招",
        ],
    },
    "资源聚合": {
        "domains": [
            "pan.baidu.com", "aliyundrive.com", "lanzou", "123pan",
            "alipan.com", "ctfile.com",
        ],
        "keywords": [
            "资源", "合集", "下载", "网盘", "分享", "免费",
            "电子书", "pdf", "epub", "书籍", "图书", "阅读",
            "模板", "素材", "源文件", "字体", "图标",
            "破解", "汉化", "绿色版", "便携版", "激活",
        ],
    },
    "视频与媒体": {
        "domains": [
            "bilibili.com", "youku.com", "iqiyi.com",
            "v.qq.com", "douyin.com", "tiktok.com",
        ],
        "keywords": [
            "视频", "电影", "电视剧", "动漫", "番剧", "纪录片",
            "直播", "主播", "up主", "vlog", "短视频",
            "音频", "音乐", "播客", "podcast",
        ],
    },
    "电子商务": {
        "domains": [
            "taobao.com", "tmall.com", "jd.com", "pinduoduo.com",
            "1688.com", "aliexpress.com", "amazon.com",
            "smzdm.com", "xueqiu.com",
        ],
        "keywords": [
            "购买", "商城", "价格", "优惠", "打折", "促销",
            "淘宝", "京东", "拼多多", "聚水潭", "电商",
            "股票", "基金", "投资", "理财",
        ],
    },
}

# 域名 -> 分类映射（快速查找，最后一个匹配的分类生效）
DOMAIN_CATEGORY_MAP = {}
for cat, rules in CATEGORY_RULES.items():
    for domain in rules["domains"]:
        DOMAIN_CATEGORY_MAP[domain] = cat


def timestamp_to_str(ts):
    """将 Chrome 时间戳转换为可读日期（Chrome 从 1601-01-01 的微秒数）"""
    if not ts or ts == "0" or ts == 0:
        return None
    try:
        ts = int(ts)
        chrome_epoch_offset = 11644473600 * 1_000_000
        if ts > chrome_epoch_offset:
            unix_ts = (ts - chrome_epoch_offset) / 1_000_000
            return datetime.fromtimestamp(unix_ts).strftime("%Y-%m-%d")
    except (ValueError, OSError):
        pass
    return None


def extract_domain(url):
    """提取域名，处理特殊情况"""
    if not url:
        return ""
    if not url.startswith("http"):
        return url.split(":")[0] if ":" in url else url
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def classify_bookmark(url, title, folder_path=""):
    """智能分类：域名规则 > 关键词打分 > 启发式 > 兜底"""
    domain = extract_domain(url)

    # 1. 域名精确匹配
    for rule_domain, cat in DOMAIN_CATEGORY_MAP.items():
        if rule_domain in domain:
            return cat

    # 2. 标题关键词匹配
    text = (title + " " + folder_path).lower()
    scores = Counter()
    for cat, rules in CATEGORY_RULES.items():
        for kw in rules.get("keywords", []):
            if kw.lower() in text:
                scores[cat] += 1

    if scores:
        return scores.most_common(1)[0][0]

    # 3. 启发式兜底
    if domain.endswith(".edu.cn") or "edu" in domain:
        return "学习教程"
    if "blog" in domain or "blog" in title.lower():
        return "编程开发"
    if any(d in domain for d in ["gov.cn", "gov"]):
        return "文档与参考"

    return "其他"


def extract_tags(title, url, domain="", folder_path=""):
    """从标题和 URL 提取标签"""
    tags = set()
    text = title.lower()

    tech_keywords = {
        "python": "Python", "java": "Java", "javascript": "JavaScript",
        "typescript": "TypeScript", "rust": "Rust", "go": "Go",
        "c++": "C++", "cpp": "C++", "c语言": "C", "react": "React",
        "vue": "Vue", "angular": "Angular", "node": "Node.js",
        "docker": "Docker", "kubernetes": "K8s", "linux": "Linux",
        "git": "Git", "sql": "SQL", "mysql": "MySQL", "redis": "Redis",
        "mongodb": "MongoDB", "nginx": "Nginx", "css": "CSS",
        "html": "HTML", "flutter": "Flutter", "swift": "Swift",
        "kotlin": "Kotlin", "spring": "Spring", "django": "Django",
        "flask": "Flask", "pytorch": "PyTorch", "tensorflow": "TensorFlow",
        "opencv": "OpenCV", "hadoop": "Hadoop", "spark": "Spark",
        "unity": "Unity", "unreal": "Unreal", "qt": "Qt",
        "cmake": "CMake", "mingw": "MinGW", "gcc": "GCC",
        "llm": "LLM", "nlp": "NLP", "cv": "CV",
        "transformer": "Transformer", "gpt": "GPT",
    }
    for kw, tag in tech_keywords.items():
        if kw in text:
            tags.add(tag)

    if any(w in text for w in ["教程", "入门", "指南", "tutorial", "guide"]):
        tags.add("教程")
    if any(w in text for w in ["源码", "github", "开源", "open source"]):
        tags.add("开源")
    if any(w in text for w in ["面试", "求职", "面经", "offer"]):
        tags.add("面试")
    if any(w in text for w in ["论文", "paper", "arxiv"]):
        tags.add("论文")
    if any(w in text for w in ["工具", "在线", "tool"]):
        tags.add("工具")
    if any(w in text for w in ["课程", "视频", "bilibili", "b站"]):
        tags.add("视频")
    if any(w in text for w in ["文档", "api", "docs"]):
        tags.add("文档")

    return sorted(tags)[:8]


def flatten_bookmarks(node, current_path="", depth=0):
    """展平书签树，返回 (bookmarks, folders) 元组"""
    bookmarks = []
    folders = []

    if not isinstance(node, dict):
        return bookmarks, folders

    node_type = node.get("type", "")
    name = node.get("name", "")

    if node_type == "folder" or "children" in node:
        folder_path = f"{current_path}/{name}" if current_path else name
        folders.append({
            "name": name,
            "path": folder_path,
            "depth": depth,
            "date_added": timestamp_to_str(node.get("date_added")),
        })
        for child in node.get("children", []):
            b, f = flatten_bookmarks(child, folder_path, depth + 1)
            bookmarks.extend(b)
            folders.extend(f)

    elif node_type == "url":
        url = node.get("url", "")
        domain = extract_domain(url)
        date_added = timestamp_to_str(node.get("date_added"))
        date_used = timestamp_to_str(node.get("date_last_used"))
        category = classify_bookmark(url, name, current_path)
        tags = extract_tags(name, url, domain, current_path)
        uid = hashlib.md5(f"{url}{name}".encode()).hexdigest()[:12]

        bookmarks.append({
            "id": uid,
            "title": name,
            "url": url,
            "domain": domain,
            "folder": current_path or "根目录",
            "category": category,
            "tags": tags,
            "date_added": date_added,
            "date_last_used": date_used,
            "visit_count": node.get("visit_count", 0),
        })

    return bookmarks, folders


def deduplicate(bookmarks):
    """去重：保留最后添加的（按 URL+标题，再按纯 URL）"""
    seen = {}
    for bm in bookmarks:
        key = (bm["url"].rstrip("/"), bm["title"])
        if key not in seen or (
            bm.get("date_added")
            and seen[key].get("date_added")
            and bm["date_added"] > seen[key]["date_added"]
        ):
            seen[key] = bm

    url_seen = {}
    for bm in seen.values():
        url_key = bm["url"].rstrip("/")
        if url_key not in url_seen or (
            bm.get("date_added")
            and url_seen[url_key].get("date_added")
            and bm["date_added"] > url_seen[url_key]["date_added"]
        ):
            url_seen[url_key] = bm

    return sorted(url_seen.values(), key=lambda x: x["category"])


def compute_stats(bookmarks, folders):
    """根据书签和文件夹计算统计信息"""
    return {
        "total_raw": len(bookmarks),
        "total_dedup": len(bookmarks),
        "duplicates": 0,  # caller should set if applicable
        "total_folders": len(folders),
        "unique_domains": len(set(b["domain"] for b in bookmarks)),
    }


def incremental_merge(existing_bookmarks, new_bookmarks):
    """增量合并：返回 (imported, skipped) 两个列表"""
    existing_urls = set()
    for b in existing_bookmarks:
        existing_urls.add(b["url"].rstrip("/"))

    imported = []
    skipped = []
    for bm in new_bookmarks:
        url_key = bm.get("url", "").rstrip("/")
        if url_key in existing_urls:
            skipped.append(bm)
        else:
            imported.append(bm)

    return imported, skipped


# ========== HTML 书签解析器（Netscape Bookmark File Format） ==========

class _BookmarkHTMLParser(HTMLParser):
    """解析 Edge/Chrome 导出的 Netscape 书签 HTML 文件"""

    def __init__(self):
        super().__init__()
        self.bookmarks = []
        self.folders = []
        self._folder_stack = []
        self._current_tag = None
        self._current_attrs = None
        self._current_title = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        self._current_tag = tag
        self._current_attrs = attrs_dict
        self._current_title = []

    def handle_data(self, data):
        if self._current_tag in ("a", "h3"):
            self._current_title.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._current_attrs:
            url = self._current_attrs.get("href", "")
            title = "".join(self._current_title).strip()
            if not url or not title:
                self._current_tag = None
                return
            add_date_ts = self._current_attrs.get("add_date", "")
            folder_path = "/".join(self._folder_stack) if self._folder_stack else ""
            date_added = None
            try:
                if add_date_ts:
                    date_added = datetime.fromtimestamp(int(add_date_ts)).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                pass
            domain = extract_domain(url)
            category = classify_bookmark(url, title, folder_path)
            tags = extract_tags(title, url, domain, folder_path)
            uid = hashlib.md5(f"{url}{title}".encode()).hexdigest()[:12]
            self.bookmarks.append({
                "id": uid, "title": title, "url": url, "domain": domain,
                "folder": folder_path or "根目录", "category": category,
                "tags": tags, "date_added": date_added,
                "date_last_used": None, "visit_count": 0,
            })

        elif tag == "h3":
            folder_name = "".join(self._current_title).strip()
            if folder_name:
                self._folder_stack.append(folder_name)
                folder_path = "/".join(self._folder_stack)
                self.folders.append({
                    "name": folder_name, "path": folder_path,
                    "depth": len(self._folder_stack) - 1, "date_added": None,
                })

        elif tag == "dl":
            if self._folder_stack:
                self._folder_stack.pop()

        self._current_tag = None
        self._current_attrs = None


def parse_bookmarks_html(source):
    """解析 Netscape Bookmark HTML 格式
    source: 文件路径(str) 或 HTML 内容(str)
    返回 (bookmarks, folders) 元组
    """
    parser = _BookmarkHTMLParser()
    if "\n" in source or source.startswith("<!DOCTYPE"):
        parser.feed(source)
    else:
        with open(source, "r", encoding="utf-8") as f:
            parser.feed(f.read())
    parser.folders.sort(key=lambda f: (f["depth"], f["path"]))
    return parser.bookmarks, parser.folders
