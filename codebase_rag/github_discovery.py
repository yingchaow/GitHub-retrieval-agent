from __future__ import annotations

from .common import *
from .llm import call_chat_completion, get_llm_config


def looks_like_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def fallback_github_query_rewrite(query: str) -> str:
    replacements = {
        "跨模态": "cross modal multimodal",
        "哈希检索": "hashing retrieval hash retrieval",
        "跨模态哈希检索": "cross modal hashing retrieval multimodal hash retrieval",
        "图文检索": "image text retrieval",
        "代码库架构": "codebase architecture",
        "知识库": "knowledge base",
        "向量数据库": "vector database",
        "多模态": "multimodal",
        "检索": "retrieval",
        "推荐系统": "recommendation system",
        "时间序列": "time series",
    }
    rewritten = query
    for source, target in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        rewritten = rewritten.replace(source, f" {target} ")
    rewritten = re.sub(r"[\u4e00-\u9fff]+", " ", rewritten)
    rewritten = re.sub(r"\s+", " ", rewritten).strip()
    return rewritten or query


def rewrite_github_search_query(query: str) -> dict[str, str]:
    clean_query = query.strip()
    if not clean_query:
        raise ValueError("请输入搜索关键词")
    fallback = fallback_github_query_rewrite(clean_query) if looks_like_chinese(clean_query) else clean_query
    if not looks_like_chinese(clean_query) and len(clean_query.split()) <= 6:
        return {"original": clean_query, "rewritten": clean_query, "method": "raw"}
    if not get_llm_config()["api_key"]:
        return {"original": clean_query, "rewritten": fallback, "method": "fallback"}
    prompt = f"""把用户输入改写成适合 GitHub repository search 的英文关键词。

用户输入：{clean_query}

要求：
- 只输出关键词，不要输出解释
- 不要包含 language、stars、fork、archived 这类 GitHub 限定符
- 优先使用开源项目 README/仓库名常见英文表达
- 保留核心算法、任务、数据模态和框架关键词
- 尽量短，3 到 6 个英文词最合适

示例：
跨模态哈希检索 -> cross modal hashing
企业知识库问答 -> enterprise knowledge base question answering RAG
"""
    try:
        rewritten = call_chat_completion(
            "你是 GitHub 搜索查询改写器。只输出一行英文关键词。",
            prompt,
            temperature=0.0,
        )
    except Exception:
        rewritten = ""
    rewritten = re.sub(r"[`\"'，。；：]", " ", rewritten).strip()
    rewritten = re.sub(r"\s+", " ", rewritten)
    if not rewritten:
        return {"original": clean_query, "rewritten": fallback, "method": "fallback"}
    return {"original": clean_query, "rewritten": rewritten, "method": "llm"}


def github_query_variants(rewritten_query: str) -> list[str]:
    normalized = re.sub(r"[-_/]+", " ", rewritten_query.lower())
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", normalized)
        if token not in {"and", "or", "the", "for", "with", "retrieval", "search"}
    ]
    variants = [rewritten_query]
    if {"cross", "modal", "hashing"}.issubset(tokens):
        variants.extend(["cross modal hashing", "deep cross modal hashing"])
    if "multimodal" in tokens and ("hashing" in tokens or "hash" in tokens):
        variants.append("multimodal hashing")
    if len(tokens) >= 3:
        variants.append(" ".join(tokens[:3]))
    if len(tokens) >= 4:
        variants.append(" ".join(tokens[:4]))
    unique: list[str] = []
    for variant in variants:
        compact = re.sub(r"\s+", " ", variant).strip()
        if compact and compact not in unique:
            unique.append(compact)
    return unique[:5]


def search_github_repositories(query: str, language: str = "", min_stars: int = 0, limit: int = 10) -> dict[str, Any]:
    limit = 3
    candidate_limit = 6
    goal = f"{DEFAULT_LEARNING_GOAL}\n用户关注方向：{query.strip()}"
    cache_key = memory_key("auto-search", query.strip(), language.strip(), min_stars, limit, goal, get_llm_config()["model"])
    memory = load_memory()
    cached = memory.get("searches", {}).get(cache_key)
    if cached:
        response = dict(cached["response"])
        response["memory_hit"] = True
        return response

    rewrite = rewrite_github_search_query(query)
    clean_query = rewrite["rewritten"]
    query_attempts: list[str] = []
    star_thresholds = [min_stars]
    if min_stars > 0:
        star_thresholds.append(0)
    data: dict[str, Any] = {"items": [], "total_count": 0}
    q = ""
    for star_threshold in star_thresholds:
        for variant in github_query_variants(clean_query):
            qualifiers = ["fork:false", "archived:false"]
            if language.strip():
                qualifiers.append(f"language:{language.strip()}")
            if star_threshold > 0:
                qualifiers.append(f"stars:>={star_threshold}")
            q = " ".join([variant, *qualifiers])
            query_attempts.append(q)
            data = request_json(
                "https://api.github.com/search/repositories",
                {
                    "q": q,
                    "sort": "stars",
                    "order": "desc",
                    "per_page": candidate_limit,
                },
            )
            if data.get("items"):
                break
        if data.get("items"):
            break
    raw_items = []
    for item in data.get("items", []):
        raw_items.append(
            {
                "full_name": item.get("full_name"),
                "html_url": item.get("html_url"),
                "description": item.get("description") or "",
                "stars": item.get("stargazers_count", 0),
                "forks": item.get("forks_count", 0),
                "language": item.get("language") or "",
                "updated_at": item.get("updated_at"),
                "default_branch": item.get("default_branch"),
            }
        )
    targets = []
    screened_count = 0
    for item in raw_items[:candidate_limit]:
        full_name = item.get("full_name")
        if not full_name:
            continue
        try:
            screen = screen_repository(full_name, goal, use_memory=True)
        except Exception as exc:
            item["screen_error"] = str(exc)
            continue
        screened_count += 1
        if not screen.get("worth_indexing"):
            continue
        merged = dict(item)
        merged["screen"] = {
            "worth_indexing": screen.get("worth_indexing"),
            "score": screen.get("score", 0),
            "method": screen.get("method", ""),
            "memory_hit": screen.get("memory_hit", False),
            "reasons": screen.get("reasons", []),
            "risk": screen.get("risk", ""),
            "important_paths": screen.get("important_paths", []),
            "suggested_questions": screen.get("suggested_questions", []),
        }
        targets.append(merged)
        if len(targets) >= limit:
            break
    targets.sort(key=lambda item: (item.get("screen", {}).get("score", 0), item.get("stars", 0)), reverse=True)
    response = {
        "query": q,
        "original_query": rewrite["original"],
        "rewritten_query": rewrite["rewritten"],
        "rewrite_method": rewrite["method"],
        "query_attempts": query_attempts,
        "total_count": data.get("total_count", 0),
        "raw_count": len(raw_items),
        "screened_count": screened_count,
        "goal": goal,
        "memory_hit": False,
        "items": targets,
    }
    memory = load_memory()
    memory.setdefault("searches", {})[cache_key] = {
        "cached_at": time.time(),
        "query": query.strip(),
        "language": language.strip(),
        "min_stars": min_stars,
        "limit": limit,
        "response": response,
    }
    save_memory(memory)
    return response


def decode_github_file(payload: dict[str, Any]) -> str:
    content = payload.get("content") or ""
    encoding = payload.get("encoding")
    if encoding == "base64":
        return base64.b64decode(content).decode("utf-8", errors="replace")
    return str(content)


def fetch_repo_profile(full_name: str) -> dict[str, Any]:
    repo = request_json(f"https://api.github.com/repos/{full_name}")
    default_branch = repo.get("default_branch") or "main"
    readme = ""
    try:
        readme_payload = request_json(f"https://api.github.com/repos/{full_name}/readme")
        readme = decode_github_file(readme_payload)
    except urllib.error.HTTPError:
        readme = ""
    tree_paths: list[str] = []
    try:
        branch = urllib.parse.quote(default_branch, safe="")
        tree = request_json(f"https://api.github.com/repos/{full_name}/git/trees/{branch}", {"recursive": "1"})
        tree_paths = [
            item.get("path", "")
            for item in tree.get("tree", [])
            if item.get("type") == "blob" and item.get("path")
        ]
    except urllib.error.HTTPError:
        tree_paths = []
    return {
        "full_name": full_name,
        "description": repo.get("description") or "",
        "language": repo.get("language") or "",
        "stars": repo.get("stargazers_count", 0),
        "forks": repo.get("forks_count", 0),
        "updated_at": repo.get("updated_at"),
        "default_branch": default_branch,
        "readme": readme[:16_000],
        "paths": tree_paths[:5_000],
    }


def is_code_path(path: str) -> bool:
    p = Path(path)
    if p.name.lower() in {"dockerfile", "makefile", "gemfile", "rakefile"}:
        return True
    return p.suffix.lower() in CODE_EXTENSIONS


def heuristic_screen(profile: dict[str, Any], goal: str) -> dict[str, Any]:
    paths = profile["paths"]
    code_paths = [path for path in paths if is_code_path(path)]
    interesting_paths = [
        path
        for path in code_paths
        if any(hint in path.lower().replace("-", "_") for hint in ARCHITECTURE_PATH_HINTS)
    ][:30]
    goal_terms = set(tokenize(goal))
    searchable_text = "\n".join(
        [
            profile.get("full_name", ""),
            profile.get("description", ""),
            profile.get("language", ""),
            profile.get("readme", "")[:8_000],
            "\n".join(paths[:400]),
        ]
    )
    text_terms = set(tokenize(searchable_text))
    overlap = sorted(goal_terms & text_terms)
    score = 25
    score += min(25, len(overlap) * 5)
    score += 15 if code_paths else -20
    score += min(15, len(interesting_paths))
    score += 10 if profile.get("stars", 0) >= 100 else 0
    score += 10 if profile.get("readme") else 0
    score = max(0, min(100, score))
    return {
        "worth_indexing": score >= 55 and bool(code_paths),
        "score": score,
        "has_code": bool(code_paths),
        "code_file_count": len(code_paths),
        "relevant_terms": overlap[:12],
        "important_paths": interesting_paths[:12],
        "reasons": [
            f"发现 {len(code_paths)} 个可读代码/配置/文档文件",
            f"目标关键词命中：{', '.join(overlap[:8]) if overlap else '较少'}",
            f"架构相关路径：{len(interesting_paths)} 个",
        ],
        "risk": "文件树为空或缺少代码文件" if not code_paths else "",
    }


def screen_repository(full_name: str, goal: str, use_memory: bool = True) -> dict[str, Any]:
    if not full_name or "/" not in full_name:
        raise ValueError("full_name 格式应为 owner/repo")
    goal = goal.strip() or "分析这个仓库是否适合作为代码库架构问答 RAG 的知识源"
    cache_key = memory_key("repo-screen", full_name, goal, get_llm_config()["model"])
    if use_memory:
        memory = load_memory()
        cached = memory.get("repo_screens", {}).get(cache_key)
        if cached:
            result = dict(cached["result"])
            result["memory_hit"] = True
            return result
    profile = fetch_repo_profile(full_name)
    heuristic = heuristic_screen(profile, goal)
    result = {
        "full_name": full_name,
        "goal": goal,
        "method": "heuristic",
        "summary": {
            "description": profile["description"],
            "language": profile["language"],
            "stars": profile["stars"],
            "updated_at": profile["updated_at"],
            "default_branch": profile["default_branch"],
        },
        **heuristic,
    }
    if get_llm_config()["api_key"]:
        sample_paths = "\n".join(profile["paths"][:260])
        readme_excerpt = profile["readme"][:6_000]
        user_prompt = f"""请判断这个 GitHub 仓库是否值得下载并索引用于代码库架构问答。

目标：{goal}
仓库：{full_name}
描述：{profile['description']}
语言：{profile['language']}
Stars：{profile['stars']}

README 摘要：
{readme_excerpt}

文件树样本：
{sample_paths}

只返回 JSON，不要 markdown：
{{
  "worth_indexing": true,
  "score": 0-100,
  "has_code": true,
  "reasons": ["..."],
  "risk": "...",
  "important_paths": ["..."],
  "suggested_questions": ["..."]
}}
"""
        try:
            text = call_chat_completion("你是代码库 RAG 项目的仓库初筛器。输出必须是合法 JSON。", user_prompt)
            parsed = extract_json_object(text)
            if parsed:
                result.update(
                    {
                        "method": "llm",
                        "worth_indexing": bool(parsed.get("worth_indexing", heuristic["worth_indexing"])),
                        "score": int(parsed.get("score", heuristic["score"])),
                        "has_code": bool(parsed.get("has_code", heuristic["has_code"])),
                        "reasons": parsed.get("reasons") or heuristic["reasons"],
                        "risk": parsed.get("risk", heuristic["risk"]),
                        "important_paths": parsed.get("important_paths") or heuristic["important_paths"],
                        "suggested_questions": parsed.get("suggested_questions") or [],
                    }
                )
        except Exception as exc:
            result["llm_warning"] = f"模型初筛失败，已使用规则初筛：{exc}"
    result["memory_hit"] = False
    if use_memory:
        memory = load_memory()
        memory.setdefault("repo_screens", {})[cache_key] = {
            "cached_at": time.time(),
            "full_name": full_name,
            "goal": goal,
            "result": result,
        }
        save_memory(memory)
    return result
