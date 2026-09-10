# EdgeFav - Edge 收藏夹知识库

将 Edge 浏览器收藏夹整理成可搜索、可分类、多知识库的 Web 工具。支持 **多维 AI 分类方案（Scheme）**。

## 启动

```bash
# 可选：AI 分类需要 API Key（不设置则仅浏览/规则导入）
set ANTHROPIC_API_KEY=sk-...

python server.py --port 8765
# 浏览器打开 http://localhost:8765
```

⚠️ 不要用 `python -m http.server`，它没有 API。

## 项目文件

| 文件 | 作用 |
|------|------|
| `server.py` | HTTP 服务器 + REST API（主入口） |
| `classifier.py` | 规则分类 + JSON/HTML 书签解析（导入兜底） |
| `scheme_ai.py` | 分类方案 + AI 结构化分类（JSON Schema） |
| `schemes/*.json` | 分类方案配置（维度定义） |
| `extract.py` | CLI：读 Edge 书签 → 导出 KB |
| `index.html` | 单页前端 |
| `kb/*.json` | 知识库数据 |

## 关键设计决策

1. **KB = 书签集合；Scheme = 怎么分** — 二者正交，不要用多 KB 模拟多种分法
2. **多维 facet** — 如 `form`（形态）+ `domain`（领域），结果写在 `bookmarks[].classifications[scheme_id]`
3. **AI 仅用户手动触发** — 全量 / 未分类 / 仅新增（ids）；不静默重分
4. **结构化输出** — tool + JSON Schema；**不设置 temperature**
5. **规则引擎** — 导入时仍可写旧 `category` 作兼容；主筛选走当前 Scheme
6. **前端无浏览器弹窗** — 新建/重命名 inline；删除二次确认
7. **XSS** — `escapeHtml()` + 卡片 `data-url` 事件委托

## 书签数据源

- **JSON**: `%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Bookmarks`
- **HTML**: Edge 导出 Netscape Bookmark HTML

## API 端点

```
GET  /api/kbs
GET  /api/kb/<name>/data
POST /api/kb/<name>/create|import|rename|delete
POST /api/kb/<name>/classify   # body: {scheme_id, mode: all|unclassified|ids, ids?}
GET  /api/schemes
GET  /api/schemes/<id>
GET  /api/kb/<name>/facets?scheme=default
GET  /api/ai/status
```

## CLI

```bash
python extract.py
python extract.py --kb-name work
python extract.py --merge kb/default.json
```
