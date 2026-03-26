# 单 PDF Agent 流水线 V2（Skills 目录化 + 批量入库）

## 概述
- 所有能力拆分为仓库内 `skills/<skill-name>/` 独立 skill 子目录。
- 每个 skill 都包含 `SKILL.md`、`agents/openai.yaml`、`scripts/run.py`。
- 数据库写入只在 `persist-pipeline-bundle-skill` 执行，调用后端 `POST /agent/pipeline/commit`。
- 前端无需调整，仍通过现有 `/conversation/{id}`、`/conversations`、`/assets/*` 渲染。

## Skills 列表
- `pdf-ingest-skill`
- `session-bootstrap-skill`
- `translate-full-paper-skill`
- `extract-figures-skill`
- `extract-tables-skill`
- `extract-tags-skill`
- `refresh-metadata-skill`
- `compose-pipeline-bundle-skill`
- `persist-pipeline-bundle-skill`
- `single-pdf-pipeline-agent`

## 标准脚本接口
所有 `scripts/run.py` 统一参数：
- `--input-json <path>`
- `--output-json <path>`

返回约定：
- 成功：`{"ok": true, ...}`
- 失败：`{"ok": false, "error": {"code": "...", "message": "..."}}`

## 后端提交接口
- 路由：`POST /agent/pipeline/commit`
- Header：`x-agent-token: <AGENT_INGEST_TOKEN>`
- Body：`PipelineBundlePayload`
- 行为：单事务批量落库，失败回滚，不产生半成品数据。

## 关键环境变量
- `AGENT_INGEST_TOKEN`：agent 提交时的鉴权 token。

## 推荐执行顺序
1. `pdf-ingest-skill`
2. `session-bootstrap-skill`
3. `translate-full-paper-skill`
4. 并行执行：`extract-figures-skill`、`extract-tables-skill`、`extract-tags-skill`、`refresh-metadata-skill(可选)`
5. `compose-pipeline-bundle-skill`
6. `persist-pipeline-bundle-skill`

或直接使用：
- `single-pdf-pipeline-agent`
