from __future__ import annotations

from .common import *
from .indexing import get_index
from .llm import build_extractive_answer, build_answer_prompt, call_llm_answer, get_llm_config
from .qdrant_store import qdrant_is_ready, qdrant_search


def answer_question(repo_id: str, question: str) -> dict[str, Any]:
    if not repo_id:
        raise ValueError("请选择一个已索引仓库")
    if not question.strip():
        raise ValueError("请输入问题")
    index = get_index(repo_id)
    backend = "local"
    warnings: list[str] = []
    contexts: list[dict[str, Any]] = []
    if qdrant_is_ready():
        try:
            contexts = qdrant_search(repo_id, question, limit=8)
            backend = "qdrant"
        except Exception as exc:
            warnings.append(f"Qdrant 检索失败，已回退本地知识库：{exc}")
    if not contexts:
        contexts = index.search(question, limit=8)
        backend = "local" if backend != "qdrant" else "hybrid-fallback"
    answer = call_llm_answer(question, index.full_name, contexts)
    return {
        "repo_id": repo_id,
        "full_name": index.full_name,
        "answer": answer,
        "contexts": contexts,
        "mode": "llm" if get_llm_config()["api_key"] else "extractive",
        "retrieval_backend": backend,
        "warnings": warnings,
    }


def retrieve_contexts(repo_id: str, question: str) -> dict[str, Any]:
    if not repo_id:
        raise ValueError("请选择一个已索引仓库")
    if not question.strip():
        raise ValueError("请输入问题")
    index = get_index(repo_id)
    backend = "local"
    warnings: list[str] = []
    contexts: list[dict[str, Any]] = []
    if qdrant_is_ready():
        try:
            contexts = qdrant_search(repo_id, question, limit=8)
            backend = "qdrant"
        except Exception as exc:
            warnings.append(f"Qdrant 检索失败，已回退本地知识库：{exc}")
    if not contexts:
        contexts = index.search(question, limit=8)
        backend = "local" if backend != "qdrant" else "hybrid-fallback"
    return {
        "repo_id": repo_id,
        "full_name": index.full_name,
        "repo_url": f"https://github.com/{index.full_name}",
        "contexts": contexts,
        "retrieval_backend": backend,
        "warnings": warnings,
        "mode": "llm" if get_llm_config()["api_key"] else "extractive",
    }
