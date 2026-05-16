from __future__ import annotations

from .common import *
from .indexing import Chunk
from .llm import get_embedding_config


def get_qdrant_config() -> dict[str, Any]:
    return {
        "url": os.environ.get("QDRANT_URL", "").strip().rstrip("/"),
        "api_key": os.environ.get("QDRANT_API_KEY", "").strip(),
        "collection": os.environ.get("QDRANT_COLLECTION", "codebase_rag").strip(),
    }


def qdrant_is_ready() -> bool:
    qdrant = get_qdrant_config()
    embedding = get_embedding_config()
    return bool(qdrant["url"] and qdrant["api_key"] and embedding["api_key"])


def get_embedding_max_chars() -> int:
    return max(256, int(os.environ.get("EMBEDDING_MAX_CHARS", "1800") or 1800))


def normalize_embedding_input(text: str) -> str:
    max_chars = get_embedding_max_chars()
    normalized = str(text).strip()
    if not normalized:
        return "(empty)"
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars].rstrip()


def embedding_text(chunk: Chunk | dict[str, Any]) -> str:
    if isinstance(chunk, Chunk):
        path = chunk.path
        start = chunk.start_line
        end = chunk.end_line
        content = chunk.content
    else:
        path = str(chunk.get("path", ""))
        start = int(chunk.get("start_line", 0))
        end = int(chunk.get("end_line", 0))
        content = str(chunk.get("content", ""))
    header = f"文件：{path}\n行号：{start}-{end}\n代码：\n"
    remaining = max(1, get_embedding_max_chars() - len(header))
    return normalize_embedding_input(f"{header}{content[:remaining]}")


def create_embeddings(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    config = get_embedding_config()
    if not config["api_key"]:
        raise RuntimeError("缺少 embedding API key")
    payload: dict[str, Any] = {
        "model": config["model"],
        "input": [normalize_embedding_input(text) for text in texts],
        "encoding_format": "float",
    }
    if config["dimensions"] > 0:
        payload["dimensions"] = config["dimensions"]
    data = request_json_api(
        f"{config['base_url']}/embeddings",
        method="POST",
        body=payload,
        headers={"Authorization": f"Bearer {config['api_key']}"},
        timeout=90,
    )
    rows = data.get("data") or []
    rows.sort(key=lambda item: item.get("index", 0))
    return [row["embedding"] for row in rows]


def qdrant_request(path: str, method: str = "GET", body: Any = None) -> Any:
    config = get_qdrant_config()
    if not config["url"] or not config["api_key"]:
        raise RuntimeError("缺少 QDRANT_URL 或 QDRANT_API_KEY")
    return request_json_api(
        f"{config['url']}{path}",
        method=method,
        body=body,
        headers={"api-key": config["api_key"]},
        timeout=90,
    )


def ensure_qdrant_collection(vector_size: int | None = None) -> None:
    qdrant = get_qdrant_config()
    embedding = get_embedding_config()
    expected_size = int(vector_size or embedding["dimensions"])
    try:
        collection = qdrant_request(f"/collections/{urllib.parse.quote(qdrant['collection'])}")
        vectors = collection.get("result", {}).get("config", {}).get("params", {}).get("vectors")
        existing_size = None
        if isinstance(vectors, dict):
            existing_size = vectors.get("size")
        if existing_size and int(existing_size) != expected_size:
            raise RuntimeError(
                f"Qdrant collection '{qdrant['collection']}' 向量维度是 {existing_size}，"
                f"但当前 embedding 实际返回维度是 {expected_size}。"
                "请换一个 QDRANT_COLLECTION，或改用与该 collection 维度一致的 embedding 模型。"
            )
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
        qdrant_request(
            f"/collections/{urllib.parse.quote(qdrant['collection'])}",
            method="PUT",
            body={
                "vectors": {
                    "size": expected_size,
                    "distance": "Cosine",
                }
            },
        )
    ensure_qdrant_payload_indexes(qdrant["collection"])


def ensure_qdrant_payload_indexes(collection_name: str) -> None:
    encoded = urllib.parse.quote(collection_name)
    for field_name in ("repo_id", "full_name", "path"):
        try:
            qdrant_request(
                f"/collections/{encoded}/index?wait=true",
                method="PUT",
                body={
                    "field_name": field_name,
                    "field_schema": "keyword",
                },
            )
        except RuntimeError as exc:
            message = str(exc).lower()
            if "already exists" in message or "already has" in message:
                continue
            raise


def point_id(repo_id: str, chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{repo_id}:{chunk_id}"))


def get_vector_index_config() -> dict[str, Any]:
    return {
        "mode": os.environ.get("VECTOR_INDEX_MODE", "selective").strip().lower(),
        "max_chunks": max(1, int(os.environ.get("VECTOR_MAX_CHUNKS", "120") or 120)),
    }


def chunk_vector_priority(chunk: Chunk) -> tuple[int, int, str]:
    path = chunk.path.lower()
    name = path.rsplit("/", 1)[-1]
    if name in {"readme.md", "readme", "pyproject.toml", "package.json", "requirements.txt", "go.mod", "cargo.toml"}:
        return (0, chunk.start_line, path)
    if name in {"main.py", "app.py", "server.py", "index.js", "index.ts", "main.go"}:
        return (1, chunk.start_line, path)
    if any(part in path for part in ("/api/", "/routes/", "/router", "/service", "/core", "/model", "/config", "/db")):
        return (2, chunk.start_line, path)
    if re.search(r"\b(class|def|function|async|handler|controller|service|router)\b", chunk.content.lower()):
        return (3, chunk.start_line, path)
    return (8, chunk.start_line, path)


def select_vector_chunks(chunks: list[Chunk]) -> tuple[list[Chunk], dict[str, Any]]:
    config = get_vector_index_config()
    if config["mode"] in {"off", "none", "disabled"}:
        return [], {"mode": config["mode"], "selected_chunks": 0, "total_chunks": len(chunks)}
    if config["mode"] in {"all", "full"}:
        selected = chunks[: config["max_chunks"]]
    else:
        selected = [
            chunk
            for chunk in sorted(chunks, key=chunk_vector_priority)
            if chunk_vector_priority(chunk)[0] < 8
        ][: config["max_chunks"]]
        if not selected:
            selected = sorted(chunks, key=chunk_vector_priority)[: config["max_chunks"]]
    return selected, {
        "mode": config["mode"],
        "selected_chunks": len(selected),
        "total_chunks": len(chunks),
        "max_chunks": config["max_chunks"],
    }


def qdrant_upsert_index(index: CodeIndex) -> dict[str, Any]:
    qdrant = get_qdrant_config()
    batch_size = int(os.environ.get("QDRANT_BATCH_SIZE", "24") or 24)
    vector_chunks, selection = select_vector_chunks(index.chunks)
    if not vector_chunks:
        return {
            "backend": "qdrant",
            "enabled": False,
            "collection": qdrant["collection"],
            "synced_points": 0,
            "selection": selection,
        }
    synced = 0
    for offset in range(0, len(vector_chunks), batch_size):
        batch = vector_chunks[offset : offset + batch_size]
        vectors = create_embeddings([embedding_text(chunk) for chunk in batch])
        if not vectors:
            continue
        if synced == 0:
            ensure_qdrant_collection(len(vectors[0]))
        points = []
        for chunk, vector in zip(batch, vectors):
            points.append(
                {
                    "id": point_id(index.repo_id, chunk.id),
                    "vector": vector,
                    "payload": {
                        "repo_id": index.repo_id,
                        "full_name": index.full_name,
                        "chunk_id": chunk.id,
                        "path": chunk.path,
                        "start_line": chunk.start_line,
                        "end_line": chunk.end_line,
                        "content": chunk.content,
                    },
                }
            )
        qdrant_request(
            f"/collections/{urllib.parse.quote(qdrant['collection'])}/points?wait=true",
            method="PUT",
            body={"points": points},
        )
        synced += len(points)
    return {
        "backend": "qdrant",
        "enabled": True,
        "collection": qdrant["collection"],
        "synced_points": synced,
        "selection": selection,
    }


def qdrant_search(repo_id: str, question: str, limit: int = 8) -> list[dict[str, Any]]:
    qdrant = get_qdrant_config()
    vector = create_embeddings([question])[0]
    ensure_qdrant_collection(len(vector))
    data = qdrant_request(
        f"/collections/{urllib.parse.quote(qdrant['collection'])}/points/query",
        method="POST",
        body={
            "query": vector,
            "limit": limit,
            "with_payload": True,
            "filter": {"must": [{"key": "repo_id", "match": {"value": repo_id}}]},
        },
    )
    points = data.get("result", {}).get("points")
    if points is None:
        points = data.get("result", [])
    results = []
    for item in points:
        payload = item.get("payload") or {}
        results.append(
            {
                "score": round(float(item.get("score", 0)), 4),
                "id": payload.get("chunk_id", ""),
                "path": payload.get("path", ""),
                "start_line": payload.get("start_line", 0),
                "end_line": payload.get("end_line", 0),
                "content": payload.get("content", ""),
            }
        )
    return results


def qdrant_delete_repo(repo_id: str) -> dict[str, Any]:
    qdrant = get_qdrant_config()
    if not qdrant["url"] or not qdrant["api_key"]:
        return {"enabled": False, "deleted": False}
    data = qdrant_request(
        f"/collections/{urllib.parse.quote(qdrant['collection'])}/points/delete?wait=true",
        method="POST",
        body={
            "filter": {
                "must": [
                    {"key": "repo_id", "match": {"value": repo_id}},
                ]
            }
        },
    )
    return {
        "enabled": True,
        "deleted": True,
        "collection": qdrant["collection"],
        "result": data.get("result", {}),
    }
