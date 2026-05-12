# Maintenance Scripts

这些脚本直接读写 `DATABASE_URL` 指向的数据库。默认只 dry-run；只有显式传 `--write` 才会落库。对生产库操作前先确认 `.env`，必要时先做数据库备份。

## SQLite 导出库导入

从另一台机器导出的 SQLite 库导入翻译 session：

```bash
python scripts/import_sqlite_export.py --source /path/to/translations.db
```

正式导入：

```bash
python scripts/import_sqlite_export.py --source /path/to/translations.db --write
```

只导入指定会话：

```bash
python scripts/import_sqlite_export.py \
  --source /path/to/translations.db \
  --conversation-id <conversation_id>
```

替换目标库中已存在的同一批 session：

```bash
python scripts/import_sqlite_export.py \
  --source /path/to/translations.db \
  --conversation-id <conversation_id> \
  --replace-existing \
  --write
```

导入行为：

- 默认按 `conversation_id`、`filerecord.fingerprint`、`paper_id` 查重，命中则跳过。
- `--replace-existing` 会先删除匹配 session 的 `message`、`filerecord`、图表、标签、Semantic Scholar 结果和相关 `asyncjob`，再重新导入。
- 默认跳过缺少 file/message/Semantic Scholar 的 incomplete session。
- 不导入源库 `asyncjob` 历史。
- 自增主键重新分配；业务 id、时间戳、图表二进制和 JSON payload 保留。
- 文本字段中的 NUL 字符会被移除并在摘要中计数，避免 PostgreSQL 拒绝写入。
- `content_type` 原样保留，不强制改成 `application/pdf`。

## 公式文本 artifact 修复

扫描可见 bot 翻译消息中的公式脏 token：

```bash
python scripts/backfill_formula_text_artifacts.py --output -
```

只看一个会话或消息：

```bash
python scripts/backfill_formula_text_artifacts.py --conversation-id <conversation_id> --output -
python scripts/backfill_formula_text_artifacts.py --message-id <message_id> --output -
```

正式修复：

```bash
python scripts/backfill_formula_text_artifacts.py --conversation-id <conversation_id> --write
```

修复范围：

- `<|v0xK|x|v0xK|>` 转成下标形态，例如 `L_{x}`。
- `/u1D443`、`/u706D` 这类泄露 token 解码成真实 Unicode 字符。
- `/summationdisplay.*`、`/summationtext.*` 转成 `∑`。
- `/barex` 转成 `|`，`/bardblex` 转成 `‖`，移除 `.alt` 后缀。

脚本会输出 JSONL 审计报告。每行包含 `action`：

- `keep`：无需更新。
- `update`：修复后坏 token 数减少，`--write` 时会更新。
- `skip_ambiguous`：内容有变化但坏 token 数没有减少，不自动写入。

## 推荐流程

```bash
python scripts/import_sqlite_export.py --source /path/to/translations.db --replace-existing
python scripts/import_sqlite_export.py --source /path/to/translations.db --replace-existing --write
python scripts/backfill_formula_text_artifacts.py --output -
python scripts/backfill_formula_text_artifacts.py --write
python -m unittest tests.test_import_sqlite_export tests.test_formula_text_backfill
```
