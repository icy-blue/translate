# PDF 论文翻译助手

一个基于 FastAPI + Poe 的 PDF 论文翻译服务。当前版本已经从“整篇长对话续翻”切到“先规划 unit，再按 unit 逐段翻译”的任务流，支持异步上传、按正文/附录继续翻译、标签与元数据维护、图表资产提取，以及 Agent 批量入库。

## Demo

[https://translate.icydev.cn](https://translate.icydev.cn)

## 页面预览

![搜索](static/search.jpg)
![列表](static/conversations2.jpg)
![文章页面](static/translate.jpg)

## 当前能力

- 上传 PDF 后创建异步 ingest 任务，返回 `task_id`，客户端轮询 `/tasks/{task_id}` 获取结果。
- 首轮翻译先让模型输出 `translation_plan`，把正文放进 `units`、附录放进 `appendix_units`。
- 后续续翻按 unit 推进，翻译状态以 `translation_status` 持久化到消息 payload 中。
- 同一 PDF 会按 SHA-256 指纹去重，重复上传会直接返回已有会话。
- 自动提取论文标题、图、表；标签提取可在上传时开启，也可后续单独刷新。
- 接入 Semantic Scholar 元数据，并支持按标签、CCF、venue、年份过滤与搜索。
- 支持只读模式，统一拦截所有写接口。
- 支持 Agent 通过 `/pipeline/commits` 一次性提交完整流水线结果。

## 技术栈

- 后端：FastAPI
- 数据层：SQLModel / SQLAlchemy
- 数据库：SQLite 默认，兼容 PostgreSQL
- 模型调用：`fastapi-poe`
- PDF 处理：`pypdf`、`pymupdf`
- 图像处理：`pillow`
- 前端：`static/index.html` 单页应用

## 运行前说明

- Python 3.10+
- `backend/platform/config.py` 中的 `Settings` 是运行时默认值的最终来源。
- `.env.example` 可以直接复制至 `.env` 使用。

## 快速启动

安装依赖：

```bash
pip install -r requirements.txt
```

准备环境变量：

```bash
cp .env.example .env
```

启动服务：

```bash
uvicorn app:app --reload
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。

生产示例：

```bash
gunicorn -k uvicorn.workers.UvicornWorker app:app -w 4 -b 127.0.0.1:8000
```

## 关键环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///translations.db` | 数据库连接串 |
| `POE_MODEL` | `GPT-5.2-Instant` | 上传、标题提取、续翻默认模型 |
| `TITLE_PROMPT` | 内置标题提取提示词 | 从 PDF 提取标题 |
| `INITIAL_PROMPT` | 内置 `translation-plan extractor` 提示词 | 首轮先生成 unit 规划 |
| `CONTINUE_PROMPT` | 内置 unit 翻译提示词 | 只翻当前 unit，并要求输出 `TRANSLATION_STATUS_JSON` |
| `READ_ONLY` | `false` | 是否禁用写操作 |
| `ASYNC_JOB_WORKERS` | `2` | 异步任务 worker 数量 |
| `SEMANTIC_SCHOLAR_API_KEY` / `S2_API_KEY` | 空 | Semantic Scholar API key |
| `AGENT_INGEST_TOKEN` | 空 | Agent 调用 `/pipeline/commits` 时通过 `x-agent-token` 传递 |

说明：

- `api_key` 不是环境变量，而是前端/客户端通过表单字段提交给写接口的 Poe API key。
- 应用启动时会自动执行建表、补资产列、校验 message schema、恢复未完成任务并启动 worker。
- 如果启动时报 `message table schema is inconsistent`，先运行：

```bash
python scripts/maintain_message_kind_schema.py --write
```

## API 文档

详细接口说明已经单独拆到 [API.md](API.md)，README 这里只保留项目概览。

`API.md` 包含：

- 页面与系统路由
- ingest / continue 的异步任务接口
- 会话、搜索、元数据、资产接口
- Agent 批量入库接口
- 关键表单字段、请求头和常见返回约束

## 当前主流程

```text
1. 客户端上传 PDF 到 /tasks/ingest-pdf
2. 服务写入 AsyncJob，后台 worker 开始处理
3. 计算 PDF 指纹；若已存在有效会话则直接返回旧结果
4. 上传原始 PDF 到 Poe，并尝试只用首页提取标题
5. 调用 planner，生成 translation_plan（units / appendix_units）
6. 若 planner 可用，则立刻翻译首个 unit
7. 保存 Conversation / FileRecord / Message / 图 / 表 / 标签 / Semantic Scholar 结果
8. 前端轮询 /tasks/{task_id}，得到会话详情与当前 translation_status
9. 用户调用 /translations/{conversation_id}/continue，继续 body 或 appendix
```

## 数据模型

核心表位于 `backend/platform/models.py`：

- `Conversation`
- `Message`
- `FileRecord`
- `PaperFigure`
- `PaperTable`
- `PaperTag`
- `PaperSemanticScholarResult`
- `AsyncJob`

其中：

- `Message.client_payload_json` 用来持久化 `translation_plan` 和 `translation_status`
- `AsyncJob` 用来承载 ingest / continue 这类后台任务
- 图表二进制直接保存在数据库中

## 目录结构

```text
translate/
├── app.py
├── backend/
│   ├── app/
│   │   ├── dependencies.py
│   │   └── factory.py
│   ├── domain/
│   │   ├── ccf_mapping.py
│   │   ├── message_kinds.py
│   │   ├── message_payloads.py
│   │   ├── message_sections.py
│   │   ├── paper_tags.py
│   │   └── pdf_figures.py
│   ├── modules/
│   │   ├── assets.py
│   │   ├── conversations.py
│   │   ├── ingest.py
│   │   ├── metadata.py
│   │   ├── pipeline.py
│   │   ├── search.py
│   │   ├── system.py
│   │   └── translation.py
│   └── platform/
│       ├── config.py
│       ├── gateways/
│       ├── models.py
│       ├── schema_maintenance.py
│       └── task_runtime.py
├── docs/
├── scripts/
├── skills/
├── static/
└── tests/
```

补充说明：

- `app.py` 只是兼容入口，真正的应用装配在 `backend/app/factory.py`
- 路由按任务拆在 `backend/modules/*.py`
- 领域解析逻辑放在 `backend/domain/`
- 平台层负责配置、模型、schema 维护、任务运行时和外部网关

## 常用脚本

常规维护：

```bash
python scripts/backfill_tags.py --api-key <poe_api_key>
python scripts/backfill_assets.py --limit 50
python scripts/backfill_semantic_scholar.py --api-key <s2_api_key>
python scripts/export_semantic_scholar_csv.py --output data/paper_semantic_scholar_results.csv
python scripts/scrape_ccf_conferences.py
```

历史数据维护：

```bash
python scripts/maintain_message_kind_schema.py --write
python scripts/backfill_message_payload_cleanup.py --write
python scripts/backfill_translation_payload_v2.py --write
```

## 测试

```bash
python -m unittest discover -s tests
```

当前测试主要覆盖：

- 新旧路由切换是否正确
- ingest 去重与首轮翻译计划存储
- continue 按 body / appendix 推进的状态迁移
- `translation_plan` / `translation_status` payload 规范化

## 常见问题

### 为什么上传和续翻不是同步返回？

当前版本统一走 `AsyncJob`，这样可以在上传、调用 Poe、提图表、刷新元数据时保持接口稳定，前端只需要轮询 `/tasks/{task_id}`。

### 只读模式会影响哪些接口？

所有写接口都会被 `check_read_only` 拦截，包括上传、继续翻译、刷新元数据、手动改标签、重提图表和 Agent 批量入库。

### 为什么继续翻译可能返回 409？

常见原因有三种：

- 同一会话已有进行中的 `continue_translation` 任务
- 当前 scope 没有剩余 unit
- 最新消息里缺少可用的 `translation_plan` / `translation_status`
