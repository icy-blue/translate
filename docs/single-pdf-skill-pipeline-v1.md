## 单 PDF 流水线 V1（先全量处理，再一次性入库）

### Summary
- 将当前“边处理边写库”改为“agent 先完成全部技能处理，再通过一个持久化 skill 批量入库”。
- 前端不改动，继续依赖现有 `/conversations`、`/conversation/{id}`、`/assets/*` 渲染。
- 新增一条 agent 专用提交通道与统一数据契约，保证入库原子性与幂等行为可控。

### Implementation Changes
1. **Skill 职责重构（只保留一个写库 skill）**
- `pdf_ingest_skill`、`session_bootstrap_skill`、`translate_full_paper_skill`、`extract_figures_skill`、`extract_tables_skill`、`extract_tags_skill`、`refresh_metadata_skill` 全部改为“仅产出结果，不写数据库”。
- 新增 `persist_pipeline_bundle_skill`，作为唯一有副作用的 skill，负责一次性写入 DB。
- 新增中间对象 `PipelineBundle`（内存态）：`conversation/title/file_record/messages/figures/tables/tags/meta/errors/fingerprint`。

2. **Agent 编排改造（agent-first）**
- `single_pdf_pipeline_agent` 流程固定为：`ingest -> bootstrap -> full_translate -> 并行提取(figures/tables/tags/meta) -> 组装 PipelineBundle -> persist_pipeline_bundle_skill`。
- 只在最后一步触库；前序 skill 失败只记录到 `errors`，由策略决定是否允许提交部分结果。
- 判重分支提前执行：若 `fingerprint` 命中，默认不重写，直接返回 `existing_conversation_id`（可配置 `force_rebuild=false`）。

3. **后端提交接口与类型（供持久化 skill 调用）**
- 新增 agent 专用提交接口（当前放在 [backend/api/routers/conversations.py](/Users/icynew/dev/translate/backend/api/routers/conversations.py) 并由 [backend/main.py](/Users/icynew/dev/translate/backend/main.py) 挂载）：`POST /agent/pipeline/commit`。
- 新增请求/响应模型（当前放在 [backend/schemas/pipeline.py](/Users/icynew/dev/translate/backend/schemas/pipeline.py)）：
  - Request: `PipelineBundleDTO`（完整渲染所需字段，含 `fingerprint` 和可选 `conversation_id`）。
  - Response: `status`, `conversation_id`, `exists`, `committed_parts`, `errors`。
- 鉴权建议：独立 `AGENT_INGEST_TOKEN`（不复用前端 API key）。

4. **数据库批量提交实现（单事务）**
- 在 [backend/persistence/crud.py](/Users/icynew/dev/translate/backend/persistence/crud.py) 新增 `persist_pipeline_bundle(session, bundle)`：
  - 先查重 `FileRecord.fingerprint`。
  - 未命中则在一个事务里依次写 `Conversation`、`FileRecord`、`Message*`、`PaperFigure*`、`PaperTable*`、`PaperTag*`、可选 `PaperSemanticScholarResult`。
  - 全部成功后 `commit` 一次；任何异常 `rollback`，保证前端看不到半成品会话。
- 不调用现有 `create_messages/replace_*`（它们会提前 `commit`），避免破坏原子性。

5. **前端兼容与渲染契约保持不变**
- `render_payload` 最小字段保持：`conversation_id/title/messages/pdf_url/figures/tables/tags/meta`。
- 入库后前端无需新逻辑，直接通过现有读取接口自动渲染。
- `/continue/{conversation_id}` 旧路径保持兼容；本次仅新增 agent 提交路径，不改前端交互。

### Test Plan
1. **全量成功提交**
- agent 完成全部 skill 后调用 commit，断言各表一次性落库且 `/conversation/{id}` 渲染完整。

2. **原子性回滚**
- 人为制造提交中途异常，断言所有目标表无该 `conversation_id` 残留数据。

3. **判重行为**
- 相同 `fingerprint` 连续提交两次，第二次返回 `exists=true` 且不重复写入。

4. **部分失败可提交策略**
- 令图/表/标签某一项失败，若策略允许部分提交，断言会话与消息可见，失败项记录在 `errors`。

5. **前端回归**
- 不改前端代码，验证会话列表、详情、图表区、标签区、数学渲染均正常。

6. **兼容旧流程**
- 现有 `/upload`、`/continue` 流程不受新接口影响，行为与当前一致。

### Assumptions
- 默认策略：`fingerprint` 命中即复用已有会话，不覆盖（`force_rebuild=false`）。
- `persist_pipeline_bundle_skill` 是唯一写库入口，其他 skill 禁止直接写 DB。
- 当前版本不做前端改动，不做数据库 schema 迁移；并发唯一性先用应用层查重保证。
- agent 提交的 `PipelineBundle` 必须包含前端渲染所需最小字段，否则 commit 返回校验错误。
