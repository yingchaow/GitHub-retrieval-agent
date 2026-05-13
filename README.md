# GitHub Retrieval Agent

A developer-focused RAG demo for discovering, indexing, and asking questions about GitHub codebases.

The app helps you find repositories worth studying, build a lightweight knowledge base from their source code, and ask architecture-oriented questions through a chat interface.

## Features

- Rewrite Chinese or natural-language topics into GitHub-friendly search queries.
- Search GitHub repositories with filters such as stars, language, fork status, and archive status.
- Automatically screen candidate repositories with README, file tree, and optional LLM judgment.
- Keep a memory cache in `.rag_demo/memory.json` to avoid repeated searches and repeated screening calls.
- Download and index repository source files.
- Split code into chunks and retrieve relevant context with local BM25.
- Use a local JSON knowledge base by default.
- Optionally sync indexed chunks to Qdrant Cloud for vector retrieval.
- Answer codebase architecture questions with an OpenAI-compatible chat API.
- Fall back to local extractive answers when no LLM API key is configured.
- Provide a browser UI with repository discovery, indexed repository search, streaming chat answers, citations, and copy actions.

## Quick Start

```bash
cp .env.example .env
python3 app.py
```

Then open:

```text
http://127.0.0.1:8000
```

## Docker Deployment

Create your local environment file first:

```bash
cp .env.example .env
```

Start the app:

```bash
docker compose up --build
```

Open:

```text
http://127.0.0.1:8000
```

Run in the background:

```bash
docker compose up -d --build
```

Stop the app:

```bash
docker compose down
```

Indexed data, downloaded repository cache, and discovery memory are stored in the Docker volume `rag_demo_data`, mounted at `/app/.rag_demo` inside the container.

To remove the stored data:

```bash
docker compose down -v
```

## Configuration

Copy `.env.example` to `.env` and fill in only the values you need.

```bash
GITHUB_TOKEN=your_github_token

DASHSCOPE_API_KEY=your_bailian_or_dashscope_api_key
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen-plus
```

`GITHUB_TOKEN` is optional, but unauthenticated GitHub Search API requests have a much lower rate limit.

The LLM API key is also optional. Without it, the demo still performs local retrieval and returns ranked code excerpts.

### Alibaba Cloud Bailian / Tongyi Qwen

If you use a DashScope API key from Alibaba Cloud Bailian, configure:

```bash
DASHSCOPE_API_KEY=your_bailian_api_key
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen-plus
```

For other regions, change `OPENAI_BASE_URL`:

```bash
# Singapore
OPENAI_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1

# US Virginia
OPENAI_BASE_URL=https://dashscope-us.aliyuncs.com/compatible-mode/v1
```

This project uses the OpenAI-compatible `chat/completions` API, so Bailian, OpenAI, and other compatible providers can be switched through `OPENAI_BASE_URL` and `OPENAI_MODEL`.

OpenAI example:

```bash
OPENAI_API_KEY=your_openai_api_key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4.1-mini
```

Provider-neutral aliases are also supported:

```bash
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://your-compatible-endpoint/v1
LLM_MODEL=your_model
```

## Optional Qdrant Cloud

If Qdrant is not configured, the demo stores indexes under `.rag_demo/indexes` and uses local BM25 retrieval.

To connect Qdrant Cloud, add:

```bash
QDRANT_URL=https://your-cluster-url
QDRANT_API_KEY=your_qdrant_database_api_key
QDRANT_COLLECTION=codebase_rag

EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMENSIONS=1024
```

The embedding API key reuses `DASHSCOPE_API_KEY` by default. You can also configure it separately:

```bash
EMBEDDING_API_KEY=your_embedding_api_key
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

Qdrant writes use the database REST API endpoint `PUT /collections/{collection}/points`. Retrieval uses `POST /collections/{collection}/points/query` and filters by `repo_id`, so each question only searches chunks from the selected repository.

## Suggested Questions

After indexing a repository, try asking:

- Where is the main entry point of this project?
- What is the request flow from routing to business logic?
- What are the major modules in this repository?
- Where is the database access layer?
- If I want to add a new API endpoint, which files should I change?
- What design patterns or architectural choices are worth learning from this codebase?

## Project Structure

```text
app.py                         # Minimal application entry point
codebase_rag/
  common.py                    # Shared paths, config, JSON, HTTP, and tokenization helpers
  github_discovery.py          # GitHub search, query rewriting, repository screening, and memory cache
  indexing.py                  # Repository download, file selection, chunking, local index, and BM25 retrieval
  llm.py                       # OpenAI-compatible chat/stream helpers and prompt construction
  qa.py                        # Retrieval and answer orchestration
  qdrant_store.py              # Qdrant collection setup, payload indexes, embeddings, upsert, and search
  server.py                    # HTTP server, API routes, static files, and SSE streaming
static/
  index.html                   # Web UI
  styles.css                   # UI styles
  app.js                       # Frontend interactions and streaming chat
.env.example                   # Environment variable template
Dockerfile                     # Container image definition
docker-compose.yml             # Local Docker Compose deployment
```

## Notes

- Do not commit `.env`. It is ignored by `.gitignore`.
- Keep real API keys only in your local `.env` or deployment secret manager.
- The `.rag_demo/` directory is generated runtime data and should not be committed.
