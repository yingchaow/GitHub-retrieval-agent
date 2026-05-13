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
    return f"文件：{path}\n行号：{start}-{end}\n代码：\n{content[:6_000]}"


def create_embeddings(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    config = get_embedding_config()
    if not config["api_key"]:
        raise RuntimeError("缺少 embedding API key")
    payload: dict[str, Any] = {
        "model": config["model"],
        "input": texts,
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


def ensure_qdrant_collection() -> None:
    qdrant = get_qdrant_config()
    embedding = get_embedding_config()
    try:
        collection = qdrant_request(f"/collections/{urllib.parse.quote(qdrant['collection'])}")
        vectors = collection.get("result", {}).get("config", {}).get("params", {}).get("vectors")
        existing_size = None
        if isinstance(vectors, dict):
            existing_size = vectors.get("size")
        if existing_size and int(existing_size) != embedding["dimensions"]:
            raise RuntimeError(
                f"Qdrant collection '{qdrant['collection']}' 向量维度是 {existing_size}，"
                f"但当前 EMBEDDING_DIMENSIONS 是 {embedding['dimensions']}。"
                "请换一个 QDRANT_COLLECTION，或把 EMBEDDING_DIMENSIONS 改成 collection 的维度。"
            )
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise
        qdrant_request(
            f"/collections/{urllib.parse.quote(qdrant['collection'])}",
            method="PUT",
            body={
                "vectors": {
                    "size": embedding["dimensions"],
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


def qdrant_upsert_index(index: CodeIndex) -> dict[str, Any]:
    ensure_qdrant_collection()
    qdrant = get_qdrant_config()
    batch_size = int(os.environ.get("QDRANT_BATCH_SIZE", "24") or 24)
    synced = 0
    for offset in range(0, len(index.chunks), batch_size):
        batch = index.chunks[offset : offset + batch_size]
        vectors = create_embeddings([embedding_text(chunk) for chunk in batch])
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
    }


def qdrant_search(repo_id: str, question: str, limit: int = 8) -> list[dict[str, Any]]:
    qdrant = get_qdrant_config()
    ensure_qdrant_payload_indexes(qdrant["collection"])
    vector = create_embeddings([question])[0]
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
