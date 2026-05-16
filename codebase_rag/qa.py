from __future__ import annotations

from .common import *
from .context_compression import compress_contexts
from .indexing import get_index
from .llm import call_llm_answer, get_llm_config
from .qdrant_store import qdrant_is_ready, qdrant_search


def get_retrieval_config() -> dict[str, Any]:
    return {
        "hybrid": os.environ.get("HYBRID_RETRIEVAL", "1").strip().lower() not in {"0", "false", "no", "off"},
        "vector_limit": max(1, int(os.environ.get("HYBRID_VECTOR_LIMIT", "6") or 6)),
        "local_limit": max(1, int(os.environ.get("HYBRID_LOCAL_LIMIT", "8") or 8)),
        "rrf_k": max(1, int(os.environ.get("HYBRID_RRF_K", "60") or 60)),
    }


def context_key(item: dict[str, Any]) -> str:
    chunk_id = str(item.get("id") or "")
    if chunk_id:
        return chunk_id
    return f"{item.get('path', '')}:{item.get('start_line', 0)}-{item.get('end_line', 0)}"


def add_context_source(item: dict[str, Any], source: str) -> dict[str, Any]:
    result = dict(item)
    result["retrieval_sources"] = [source]
    result[f"{source}_score"] = item.get("score", 0)
    return result


def merge_context_results(
    vector_contexts: list[dict[str, Any]],
    local_contexts: list[dict[str, Any]],
    limit: int,
    rrf_k: int,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}

    def add(items: list[dict[str, Any]], source: str, weight: float) -> None:
        for rank, item in enumerate(items, start=1):
            key = context_key(item)
            score = weight / (rrf_k + rank)
            if key not in merged:
                merged[key] = add_context_source(item, source)
                merged[key]["fusion_score"] = 0.0
            elif source not in merged[key]["retrieval_sources"]:
                merged[key]["retrieval_sources"].append(source)
                merged[key][f"{source}_score"] = item.get("score", 0)
            merged[key]["fusion_score"] += score

    add(vector_contexts, "qdrant", 1.15)
    add(local_contexts, "local", 1.0)

    results = sorted(
        merged.values(),
        key=lambda item: (float(item.get("fusion_score", 0)), float(item.get("score", 0))),
        reverse=True,
    )[:limit]
    for item in results:
        item["score"] = round(float(item.get("fusion_score", item.get("score", 0))), 4)
    return results


def retrieve_raw_contexts(repo_id: str, question: str, limit: int = 8) -> dict[str, Any]:
    if not repo_id:
        raise ValueError("请选择一个已索引仓库")
    if not question.strip():
        raise ValueError("请输入问题")
    index = get_index(repo_id)
    config = get_retrieval_config()
    backend = "local"
    warnings: list[str] = []
    vector_contexts: list[dict[str, Any]] = []
    local_contexts: list[dict[str, Any]] = []
    if qdrant_is_ready():
        try:
            vector_contexts = qdrant_search(repo_id, question, limit=config["vector_limit"] if config["hybrid"] else limit)
            backend = "qdrant"
        except Exception as exc:
            warnings.append(f"Qdrant 检索失败，已回退本地知识库：{exc}")
    if config["hybrid"] or not vector_contexts:
        local_contexts = index.search(question, limit=config["local_limit"] if config["hybrid"] else limit)

    if vector_contexts and local_contexts and config["hybrid"]:
        contexts = merge_context_results(vector_contexts, local_contexts, limit, config["rrf_k"])
        backend = "hybrid"
    elif vector_contexts:
        contexts = [add_context_source(item, "qdrant") for item in vector_contexts[:limit]]
        backend = "qdrant"
    else:
        contexts = [add_context_source(item, "local") for item in local_contexts[:limit]]
        backend = "local" if backend != "qdrant" else "hybrid-fallback"

    return {
        "repo_id": repo_id,
        "full_name": index.full_name,
        "contexts": contexts,
        "retrieval_backend": backend,
        "retrieval": {
            "hybrid": config["hybrid"],
            "vector_count": len(vector_contexts),
            "local_count": len(local_contexts),
            "merged_count": len(contexts),
        },
        "warnings": warnings,
    }


def answer_question(repo_id: str, question: str) -> dict[str, Any]:
    retrieved = retrieve_contexts(repo_id, question)
    answer = call_llm_answer(question, retrieved["full_name"], retrieved["contexts"])
    return {
        **retrieved,
        "answer": answer,
    }


def retrieve_contexts(repo_id: str, question: str) -> dict[str, Any]:
    retrieved = retrieve_raw_contexts(repo_id, question, limit=8)
    contexts, compression = compress_contexts(retrieved["contexts"], question)
    return {
        **retrieved,
        "repo_url": f"https://github.com/{retrieved['full_name']}",
        "contexts": contexts,
        "compression": compression,
        "mode": "llm" if get_llm_config()["api_key"] else "extractive",
    }
