# API 文档

本文档汇总当前版本的 HTTP 接口。这里的描述以 `backend/modules/*.py` 和 `backend/platform/task_runtime.py` 的实际实现为准。

## 总览

- 页面入口由 `backend/modules/system.py` 提供
- ingest 和 continue 通过异步任务执行
- 任务状态统一通过 `/tasks/{task_id}` 查询
- 所有写接口都会受 `READ_ONLY` 保护
- ingest / continue 需要表单字段 `api_key`
- Agent 批量入库需要请求头 `x-agent-token`

## 页面与系统

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/` | 论文浏览页 |
| `GET` | `/chat` | 新论文上传页 |
| `GET` | `/chat/{path:path}` | 论文翻译展示页 |
| `GET` | `/config` | 返回 `read_only` 和 `default_poe_model` |

`GET /config` 返回示例：

```json
{
  "read_only": false,
  "default_poe_model": "GPT-5.2-Instant"
}
```

## 异步任务

### `POST /tasks/ingest-pdf`

上传 PDF，创建 ingest 任务，立即返回 `task_id`。

表单字段：

- `file`：PDF 文件，必填
- `api_key`：Poe API key，必填
- `poe_model`：翻译模型，默认 `POE_MODEL`
- `title_model`：标题提取模型，默认 `POE_MODEL`
- `tag_model`：标签提取模型，默认 `POE_MODEL`
- `extract_tags`：是否在 ingest 时顺手提标签，默认 `false`

返回示例：

```json
{
  "task_id": "9b3f...",
  "status": "queued"
}
```

关键行为：

- 只负责入队，不同步返回翻译结果
- 上传文件必须是 `.pdf`
- 会按 PDF SHA-256 指纹去重
- 若命中已有会话，最终任务结果会直接返回旧会话

### `POST /translations/{conversation_id}/continue`

为指定会话创建继续翻译任务。

表单字段：

- `api_key`：Poe API key，必填
- `poe_model`：续翻模型，默认 `POE_MODEL`
- `action`：当前仅支持 `continue`
- `target_scope`：`body` 或 `appendix`，默认 `body`

返回示例：

```json
{
  "task_id": "f47c...",
  "status": "queued"
}
```

关键行为：

- 只负责入队，客户端继续轮询 `/tasks/{task_id}`
- 同一会话若已有进行中的续翻任务，会返回 `409`
- 当前 scope 没有剩余 unit，也会返回 `409`
- 若会话存在尚未确认的 `translation_glossary`，会返回 `409`

### `PUT /translations/{conversation_id}/glossary`

确认术语词表选择结果。

请求体示例：

```json
{
  "entries": [
    {
      "term": "mesh face",
      "candidates": ["三角面片", "网格面"],
      "selected": "三角面片"
    }
  ]
}
```

返回字段：

- `conversation_id`
- `translation_plan`
- `translation_status`
- `translation_glossary`

关键行为：

- 只允许在尚未产生任何成功翻译 unit 时确认或调整术语
- 提交内容必须与后端当前 draft glossary 的 term/candidate 集合一致，否则返回 `409`
- 成功后会把 glossary 状态更新为 `confirmed`

### `GET /tasks/{task_id}`

查询任务状态。

返回字段：

- `task_id`
- `task_type`
- `status`
- `progress`
- `conversation_id`
- `conversation_title`
- `created_at`
- `started_at`
- `finished_at`
- `updated_at`
- `result`
- `error_message`

`status` 当前可能值：

- `queued`
- `running`
- `succeeded`
- `failed`

## 会话与搜索

### `GET /conversations/{conversation_id}`

返回单个会话详情，包含：

- 基本信息：`id`、`title`、`created_at`
- 消息列表：`messages`
- 原 PDF 地址：`pdf_url`
- 图表：`figures`、`tables`
- 标签：`tags`
- 语义元数据：`venue_abbr`、`ccf_category`、`ccf_type`、`citation_count`、`venue`、`year`、`semantic_updated_at`

消息中的规范化 payload 可能包含：

- `translation_plan`
- `translation_status`
- `translation_glossary`

### `GET /conversations`

分页返回会话列表。

查询参数：

- `limit`
- `offset`
- `tag_code`
- `ccf_category`
- `venue_filter`
- `year`

返回字段：

- `conversations`
- `has_more`
- `total`

过滤语义：

- `tag_code` 是 AND 关系，表示会话必须同时包含所有传入标签
- `ccf_category` 支持 `A`、`B`、`C`、`None`

### `GET /search`

按标题搜索会话。

查询参数：

- `q`
- `search_type=all|exact|fuzzy`
- `tag_code`
- `ccf_category`
- `venue_filter`
- `year`

返回字段：

- `exact_matches`
- `fuzzy_matches`
- `total_conversations`

### `GET /search/filters`

返回搜索面板所需的过滤器统计，包括：

- `total_conversations`
- `ccf_categories`
- `venues`
- `years`

### `GET /tags/library`

返回标签树及各标签使用计数。

## 元数据与资产

### `POST /metadata/{conversation_id}/refresh`

重新提取标签并刷新 Semantic Scholar 元数据。

表单字段：

- `api_key`：Poe API key，必填
- `tag_model`：标签模型，默认 `POE_MODEL`

返回中会包含：

- `tags`
- `venue_abbr`
- `ccf_category`
- `ccf_type`
- `citation_count`
- `venue`
- `year`
- `semantic_updated_at`

### `PUT /metadata/{conversation_id}/tags`

手动覆盖标签。

表单字段：

- `tag_code`：可重复提交多个标签编码

返回字段：

- `tags`

### `GET /assets/figures/{figure_id}`

返回插图二进制内容。

### `GET /assets/tables/{table_id}`

返回表格二进制内容。

### `POST /assets/{conversation_id}/reprocess`

按 caption 方向重提图表。

兼容两种调用方式：

- 旧兼容字段：`asset_type` + `caption_direction`
- 新字段：`figure_caption_direction` / `table_caption_direction`

合法方向值：

- `above`
- `below`

至少要传一个方向字段，否则返回 `400`。

## Agent 批量入库

### `POST /pipeline/commits`

接收 Agent 处理好的 bundle 并一次性写入数据库。

请求头：

- `x-agent-token`：必须等于 `AGENT_INGEST_TOKEN`

JSON payload 核心字段：

- `conversation_id`
- `title`
- `file_record`
- `messages`
- `figures`
- `tables`
- `tags`
- `meta`
- `errors`

`file_record` 至少需要：

- `filename`
- `fingerprint`
- `poe_url`

关键行为：

- 仍然按 `fingerprint` 去重
- 若已有有效会话，返回 `exists=true`
- bot 消息会走和在线流程一致的 payload 预处理

## 写接口与只读模式

以下接口都会受 `READ_ONLY` 保护：

- `POST /tasks/ingest-pdf`
- `POST /translations/{conversation_id}/continue`
- `POST /metadata/{conversation_id}/refresh`
- `PUT /metadata/{conversation_id}/tags`
- `POST /assets/{conversation_id}/reprocess`
- `POST /pipeline/commits`

当 `READ_ONLY=true` 时，这些接口会返回 `403`。

## 常见返回码

- `400`：参数缺失或非法
- `401`：`x-agent-token` 不正确
- `403`：系统处于只读模式
- `404`：会话、任务或资产不存在
- `409`：当前会话状态不允许继续推进
- `502`：重提图表时下载原 PDF 失败
- `503`：`AGENT_INGEST_TOKEN` 未配置，无法使用 `/pipeline/commits`
