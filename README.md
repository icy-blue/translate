# PDF 论文翻译助手

一个基于 FastAPI + Poe API 的论文翻译服务，支持 PDF 上传、分章续翻、会话管理，以及标签/图表/语义检索信息的增量维护。

## Demo

[https://translate.icydev.cn](https://translate.icydev.cn)

## 页面预览

![搜索](static/search.jpg)
![列表](static/conversations2.jpg)
![文章页面](static/translate.jpg)

## 核心能力

- PDF 上传后自动创建翻译会话，并生成首轮翻译结果
- 支持“继续”续翻，按章节逐步推进
- 支持会话内自定义追问（可选择是否写入历史）
- 支持论文标签自动提取与手动改写
- 支持论文图片/表格提取并以二进制资产存储
- 支持标题搜索（精确 + 模糊）与标签/CCF/会议/年份过滤
- 支持只读模式（禁用写操作，仅浏览）

## 技术栈

- 后端：FastAPI
- 数据层：SQLModel + SQLAlchemy
- 数据库：SQLite（默认）/ PostgreSQL
- AI：fastapi-poe（Poe）
- PDF 与资产处理：pypdf + PyMuPDF + Pillow
- 前端：`static/index.html`（React + Ant Design CDN）

后端 Python 模块统一放在 `backend/`，并按 `app / platform / domain / modules` 组织。根目录 `app.py` 保持启动入口（`uvicorn app:app`），真正的应用装配位于 `backend/app/factory.py`。

## 快速启动

### 1) 环境准备

- Python 3.10+
- pip

### 2) 安装依赖

```bash
pip install -r requirements.txt
```

### 3) 配置环境变量

```bash
cp .env.example .env
```

按需修改 `.env`：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///translations.db` | 数据库连接串 |
| `POE_MODEL` | `GPT-5.2-Instant` | 后端默认模型（可被前端请求参数覆盖） |
| `TITLE_PROMPT` | 内置中文提示词 | 标题提取提示词 |
| `INITIAL_PROMPT` | 内置中文提示词 | 首轮翻译提示词 |
| `CONTINUE_PROMPT` | 内置中文提示词 | 无状态续翻提示词模板 |
| `READ_ONLY` | `false` | 是否启用只读模式 |
| `ASYNC_JOB_WORKERS` | `2` | 异步任务 worker 数量（上传/续翻/追问） |
| `AGENT_INGEST_TOKEN` | `-` | Agent 批量提交流水线结果到后端时的鉴权 Token（`x-agent-token`） |

### 4) 启动服务

```bash
uvicorn app:app --reload
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。

## 生产部署示例

```bash
gunicorn -k uvicorn.workers.UvicornWorker app:app -w 4 -b 127.0.0.1:8000
```

## 接口概览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/tasks/ingest-pdf` | 上传 PDF，创建 ingest 任务（立即返回 `task_id`） |
| `GET` | `/tasks/{task_id}` | 查询任务状态与结果（轮询） |
| `POST` | `/translations/{conversation_id}/continue` | 依据最新翻译状态推进下一轮翻译（异步任务） |
| `GET` | `/conversations/{conversation_id}` | 获取会话详情 |
| `GET` | `/conversations` | 分页会话列表（支持过滤） |
| `GET` | `/search` | 标题搜索（精确 + 模糊） |
| `GET` | `/tags/library` | 标签树与使用计数 |
| `POST` | `/metadata/{conversation_id}/refresh` | 刷新标签与 Semantic Scholar 元数据 |
| `PUT` | `/metadata/{conversation_id}/tags` | 手动更新标签 |
| `POST` | `/assets/{conversation_id}/reprocess` | 按 caption 方向重提图/表 |
| `POST` | `/pipeline/commits` | Agent 一次性批量提交处理结果入库（需 `x-agent-token`） |
| `GET` | `/assets/figures/{figure_id}` | 获取图像二进制 |
| `GET` | `/assets/tables/{table_id}` | 获取表格二进制 |
| `GET` | `/search/filters` | 过滤器统计（CCF/venue/year） |
| `GET` | `/config` | 系统配置（只读状态、默认模型） |

说明：

- 写接口受只读模式保护（`READ_ONLY=true` 时返回 403）
- ingest 和翻译推进接口需要提交 `api_key`（表单字段）
- `POST /tasks/ingest-pdf` 与 `POST /translations/{conversation_id}/continue` 只负责入队；客户端需轮询 `/tasks/{task_id}` 获取最终结果
- 同一会话在翻译推进任务未完成时会加锁；重复提交返回 `409`

## 目录结构

```text
translate/
├── app.py
├── backend/
│   ├── app/
│   │   ├── factory.py
│   │   ├── lifespan.py
│   │   └── dependencies.py
│   ├── platform/
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── models.py
│   │   ├── schema_maintenance.py
│   │   ├── task_runtime.py
│   │   └── gateways/
│   ├── domain/
│   │   ├── ccf_mapping.py
│   │   ├── message_kinds.py
│   │   ├── message_payloads.py
│   │   ├── paper_tags.py
│   │   └── pdf_figures.py
│   └── modules/
│       ├── ingest/__init__.py
│       ├── translation/__init__.py
│       ├── conversations/__init__.py
│       ├── metadata/__init__.py
│       ├── assets/__init__.py
│       ├── search/__init__.py
│       ├── pipeline/__init__.py
│       └── system/__init__.py
├── static/
├── scripts/
├── data/
├── requirements.txt
└── translations.db
```

### 后端组织说明

- `backend/app/`：应用装配与 FastAPI 依赖。
- `backend/platform/`：配置、数据库、模型、任务运行时、外部 gateway。
- `backend/domain/`：纯领域规则与消息 payload 解析。
- `backend/modules/<task>/__init__.py`：按任务成组的单文件模块，内部包含该任务的路由、service、repository 与 schema。
- 旧 `core / integrations / persistence / services` 仅保留兼容转发入口，不再承载主实现。

更详细的后端分层约定可见：[docs/backend-structure.md](docs/backend-structure.md)。

## 常用维护脚本

### 1) 回填标签

```bash
python scripts/backfill_tags.py --api-key <your-poe-api-key>
```

### 2) 回填图/表资产

```bash
python scripts/backfill_assets.py --limit 50
```

### 3) 回填 Semantic Scholar + CCF

```bash
python scripts/backfill_semantic_scholar.py --api-key <your-s2-api-key>
```

### 4) 导出语义检索结果 CSV

```bash
python scripts/export_semantic_scholar_csv.py --output data/paper_semantic_scholar_results.csv
```

### 5) 更新 CCF 会议/期刊目录

```bash
python scripts/scrape_ccf_conferences.py
```

## 典型流程

```text
1. 上传 PDF
2. 服务创建异步任务并立即返回 `task_id`
3. worker 上传到 Poe CDN
4. 提取标题并发起首轮翻译
5. 入库 Conversation / Message / FileRecord
6. 可选提取 tags / figures / tables
7. 用户继续翻译或自定义追问（同样走异步任务）
8. 前端轮询 `/tasks/{task_id}` 并渲染结果
```

## 常见问题

### API Key 从哪里来？

在 [Poe](https://poe.com/) 账户中生成 API Key。前端上传/续翻/追问时会以表单字段提交。

### 如何改默认模型或提示词？

在 `.env` 中调整 `POE_MODEL`、`TITLE_PROMPT`、`INITIAL_PROMPT`、`CONTINUE_PROMPT`，重启服务即可。

### 如何进入只读模式？

将 `.env` 的 `READ_ONLY=true`，重启后上传/续翻/追问/标签更新/资产重提会被禁用（403）。
