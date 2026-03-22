# PDF 论文翻译助手

一个基于 FastAPI + Poe AI 的 PDF 论文翻译服务，支持逐章翻译、会话管理和断点续翻。

## Demo

[https://translate.icydev.cn](https://translate.icydev.cn)

## 页面

![搜索](static/search.jpg)

![列表](static/conversations2.jpg)

![文章页面](static/translate.jpg)

## 功能特性

- 🔄 **断点续翻**：支持分章节翻译，可设置自动继续次数，系统会自动连续翻译指定章节数
- 🎯 **自定义追问**：在翻译会话中弹出对话窗口向 bot 追问，可选择是否保存到历史记录
- 💾 **会话管理**：自动保存所有翻译历史，支持查看完整会话记录（若用户选择不保存，自定义对话仍展示但不写入数据库）
- 🔍 **论文搜索**：支持按论文标题搜索，分为严格匹配和模糊匹配，按相关性排序
- 🖼️ **论文插图提取**：上传 PDF 时自动提取带 `Fig.` / `Figure` caption 的图片，转成 WebP 二进制存入数据库，并在前端论文页展示
- 📊 **论文表格提取**：自动识别带 `Table` caption 的表格区域，转成 WebP 二进制存入数据库，并在前端以可折叠区块展示
- 🗄️ **数据持久化**：使用 SQLite 数据库存储会话、消息和文件记录
- 🔒 **只读模式**：支持设置只读模式，禁用上传、继续翻译和自定义对话功能，仅允许浏览已翻译的论文

## 技术栈

- **后端框架**：FastAPI
- **配置管理**：pydantic (`BaseSettings`)
- **数据库**：SQLite + SQLModel
- **AI 服务**：Poe API
  (模型名称可通过 `POE_MODEL` 配置，默认 GPT-5.2-Instant)
- **PDF 处理**：通过 Poe 模型，无需本地库
- **图片/表格提取**：PyMuPDF + Pillow（提取论文图片、表格并转成 WebP）
- **前端**：静态HTML页面

## 部署指南

### 配置

项目使用 `pydantic` 的 `BaseSettings` 读取来自环境变量或 `.env` 文件的配置。可在根目录创建一个 `.env` 文件来自定义参数。

示例 `.env`：
```
DATABASE_URL=postgresql://user:pass@host:port/dbname
POE_MODEL=GPT-5.2-Instant
TITLE_PROMPT=请查看附加的 PDF 文档，提取论文标题。
INITIAL_PROMPT=翻译这篇论文，每次翻译一章……
READ_ONLY=false
```

设置完成后，`app.py` 无需修改即可使用这些配置。


### 前置要求

- Python 3.9+
- pip 或 conda

### 安装依赖

```bash
pip install fastapi uvicorn sqlmodel pydantic-settings fastapi-poe python-multipart gunicorn pypdf pymupdf pillow
```

### 环境配置

1. 获取 Poe API Key：
   - 访问 [Poe官网](https://poe.com/)
   - 创建账户并获取 API Key

2. 本地目录结构确认：
   ```
   translate/
   ├── app.py
   ├── static/
   │   └── index.html
   └── translations.db    # 自动创建
   ```

### 启动服务

```bash
uvicorn app:app --reload
```

服务将在 `http://127.0.0.1:8000` 启动

如果需要部署，建议使用 `gunicorn`，并配置反向代理（注意文件上传存在延迟，推荐服务器带宽在 5 M 以上）。

```bash
gunicorn -k uvicorn.workers.UvicornWorker app:app -w 4 -b 127.0.0.1:8000
```

## 使用方法

### 通过 Web 界面

1. 打开浏览器访问 `http://127.0.0.1:8000`，点击“翻译新论文”按钮
2. **翻译设置**：
   - 点击配置按钮，输入 Poe API Key，设置使用的模型
3. **翻译新论文**：
   - 等待 AI 翻译摘要
   - 调整自动继续次数，点击"继续"按钮翻译下一章节，系统会自动按次数连续翻译
   - 在翻译过程中可点击"自定义对话"按钮向 bot 提问，可选择实时显示或保存到历史
4. **浏览论文库**：
   - 查看所有已翻译的论文列表
   - 支持瀑布流和列表两种视图
   - 每篇论文显示标题、时间、摘要和 PDF 下载链接
5. **搜索论文**：
   - 在搜索框中输入关键词搜索论文标题
   - 搜索结果分为"严格匹配"和"模糊匹配"两部分
   - 每类搜索最多显示 5 篇论文，按相关性排序
   - 点击"清除"按钮返回完整论文列表

## 数据存储

### 数据库表结构

- **Conversation**：会话记录（ID、标题、文件名、状态、创建时间）
- **Message**：消息记录（ID、会话ID、角色、内容、创建时间）
- **FileRecord**：文件记录（ID、会话ID、文件路径、Poe CDN 信息）
- **PaperFigure**：论文插图记录（会话ID、页码、序号、caption、WebP 二进制、尺寸）
- **PaperTable**：论文表格记录（会话ID、页码、序号、caption、WebP 二进制、尺寸）

### 文件位置

- **数据库**：`translations.db`
- **插图/表格资源**：与元数据一起保存在数据库中，不依赖 `static/` 同步

## 工作流程

```
1. 用户上传 PDF
   ↓
2. 上传到 Poe CDN（获得可复用 URL）
   ↓
3. 发送初始翻译请求（包含 PDF attachment）
   ↓
4. 保存会话和第一段回复
   ↓
5. 用户点击"继续"
   ↓
6. 按照次数，重用 Poe CDN URL，直到完成
```

## 常见问题

**Q: API Key 在哪里获取？**
A: 访问 [Poe官网](https://poe.com/) 注册账户，在设置中获取 API Key。

**Q: 如何修改翻译提示词？**
A: 在 `.env` 中调整 `INITIAL_PROMPT` 或直接修改 `config.py` 中 `initial_prompt` 默认值。无需编辑 `app.py`。

**Q: 如何启用只读模式？**
A: 在 `.env` 文件中设置 `READ_ONLY=true`，重启服务后网站将进入只读模式，只允许浏览已翻译的论文，不允许上传、继续翻译或进行自定义对话。
