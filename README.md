# GitHub Codebase RAG Demo

一个面向开发者的 GitHub 代码库架构问答 demo。

它可以：

- 根据关键词搜索 GitHub 仓库，中文/自然语言会先用模型改写成 GitHub 友好的英文检索词
- 自动用 README、文件树和模型做轻量初筛，只返回值得学习的目标项目
- 把搜索结果和仓库初筛结果写入 `.rag_demo/memory.json`，避免相同方向重复检索和重复调用模型
- 下载并索引仓库源码
- 对代码文件做 chunk 切分和 BM25 检索
- 默认保存到本地知识库，可选同步到 Qdrant Cloud
- 基于检索片段回答架构问题
- 如果配置了百炼/通义或 OpenAI 兼容 API，使用模型生成答案；否则返回本地抽取式结果

## 快速开始

```bash
cp .env.example .env
python3 app.py
```

然后打开：

```text
http://127.0.0.1:8000
```

## Docker 部署

先准备配置：

```bash
cp .env.example .env
```

然后启动：

```bash
docker compose up --build
```

打开：

```text
http://127.0.0.1:8000
```

后台运行：

```bash
docker compose up -d --build
```

停止：

```bash
docker compose down
```

索引、下载的仓库缓存和搜索记忆库会保存在 Docker volume `rag_demo_data` 中，对应容器内的 `/app/.rag_demo`。

如果要清空这些数据：

```bash
docker compose down -v
```

## API Key

`.env` 里可以配置：

```bash
GITHUB_TOKEN=你的 GitHub token
DASHSCOPE_API_KEY=你的百炼 API key
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen-plus
```

`GITHUB_TOKEN` 不是必需的，但未认证的 GitHub Search API 速率限制更低。

LLM API key 也不是必需的。没有它时，demo 会使用本地 BM25 检索，并展示最相关的代码片段。

### 使用阿里云百炼 / 通义千问

如果你用的是百炼平台的 DashScope API key，`.env` 可以这样写：

```bash
DASHSCOPE_API_KEY=你的百炼API_KEY
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen-plus
```

如果你使用的是新加坡或美国地域，把 `OPENAI_BASE_URL` 换成对应地域：

```bash
# 新加坡
OPENAI_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1

# 美国（弗吉尼亚）
OPENAI_BASE_URL=https://dashscope-us.aliyuncs.com/compatible-mode/v1
```

这个项目使用 OpenAI 兼容的 `chat/completions` 接口，所以百炼、OpenAI 或其他兼容服务都可以通过 `OPENAI_BASE_URL` / `OPENAI_MODEL` 切换。

### 可选：连接 Qdrant Cloud

不配 Qdrant 时，demo 会把索引写到 `.rag_demo/indexes`，并用本地 BM25 检索。

如果你想接云向量库，`.env` 加上：

```bash
QDRANT_URL=https://你的-cluster-url
QDRANT_API_KEY=你的 Qdrant Database API key
QDRANT_COLLECTION=codebase_rag

EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMENSIONS=1024
```

embedding API key 默认会复用 `DASHSCOPE_API_KEY`。如果你想单独配置，也可以写：

```bash
EMBEDDING_API_KEY=你的百炼API_KEY
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

Qdrant 写入使用数据库 REST API：`PUT /collections/{collection}/points`。检索使用 `POST /collections/{collection}/points/query` 并按 `repo_id` 过滤，只查当前仓库的 chunk。

## 建议提问

索引仓库后可以问：

- 这个项目的入口文件在哪里？
- 请求从路由到业务逻辑的大致流程是什么？
- 这个仓库主要分成哪些模块？
- 数据库访问层在哪里？
- 如果我要新增一个 API，应该改哪些文件？

## 项目结构

```text
app.py              # 纯标准库后端、GitHub 客户端、索引器和问答逻辑
static/index.html   # Web demo
static/styles.css   # 页面样式
static/app.js       # 前端交互
.env.example        # API key 和配置示例
Dockerfile          # 容器镜像定义
docker-compose.yml  # 本地 Docker Compose 部署
```
