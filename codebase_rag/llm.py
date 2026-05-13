from __future__ import annotations

from .common import *


def build_extractive_answer(question: str, contexts: list[dict[str, Any]]) -> str:
    if not contexts:
        return "没有检索到足够相关的代码片段。可以换一个更具体的问题，或者先确认仓库已经完成索引。"
    lines = [
        "未配置 LLM API key，当前使用本地检索模式。下面是最相关的架构线索：",
        "",
    ]
    for idx, item in enumerate(contexts[:4], start=1):
        snippet = item["content"].strip().splitlines()
        preview = "\n".join(snippet[:14])
        lines.append(f"[{idx}] {item['path']}:{item['start_line']}-{item['end_line']}")
        lines.append(preview)
        lines.append("")
    lines.append("配置 DASHSCOPE_API_KEY / OPENAI_API_KEY 后，这里会变成基于这些片段生成的自然语言架构回答。")
    return "\n".join(lines)


def get_llm_config() -> dict[str, str]:
    api_key = (
        os.environ.get("LLM_API_KEY")
        or os.environ.get("DASHSCOPE_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
    configured_model = (os.environ.get("LLM_MODEL") or os.environ.get("OPENAI_MODEL") or "").strip()
    is_dashscope = (
        bool(os.environ.get("DASHSCOPE_API_KEY"))
        or configured_model.startswith("qwen")
        or "dashscope" in (
            os.environ.get("OPENAI_BASE_URL")
            or os.environ.get("LLM_BASE_URL")
            or ""
        )
    )
    base_url = (
        os.environ.get("LLM_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or ("https://dashscope.aliyuncs.com/compatible-mode/v1" if is_dashscope else "https://api.openai.com/v1")
    ).strip()
    model = (
        os.environ.get("LLM_MODEL")
        or os.environ.get("OPENAI_MODEL")
        or ("qwen-plus" if is_dashscope else "gpt-4.1-mini")
    ).strip()
    return {"api_key": api_key, "base_url": base_url.rstrip("/"), "model": model}


def get_embedding_config() -> dict[str, Any]:
    model = os.environ.get("EMBEDDING_MODEL", "text-embedding-v4").strip()
    api_key = (
        os.environ.get("EMBEDDING_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or os.environ.get("DASHSCOPE_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or ""
    ).strip()
    base_url = (
        os.environ.get("EMBEDDING_BASE_URL")
        or os.environ.get("LLM_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or ("https://dashscope.aliyuncs.com/compatible-mode/v1" if model.startswith("text-embedding") else "https://api.openai.com/v1")
    ).strip()
    dimensions = int(os.environ.get("EMBEDDING_DIMENSIONS", "1024") or 1024)
    return {
        "api_key": api_key,
        "base_url": base_url.rstrip("/"),
        "model": model,
        "dimensions": dimensions,
    }


def call_chat_completion(system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
    config = get_llm_config()
    if not config["api_key"]:
        return ""
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "stream": False,
    }
    data = request_json_api(
        f"{config['base_url']}/chat/completions",
        method="POST",
        body=payload,
        headers={"Authorization": f"Bearer {config['api_key']}"},
        timeout=60,
    )
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    return content.strip() if isinstance(content, str) else ""


def stream_chat_completion(system_prompt: str, user_prompt: str, temperature: float = 0.2):
    config = get_llm_config()
    if not config["api_key"]:
        return
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "stream": True,
    }
    req = urllib.request.Request(
        f"{config['base_url']}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line.removeprefix("data:").strip()
            if data == "[DONE]":
                break
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = payload.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if content:
                yield content


def call_llm_answer(question: str, repo_name: str, contexts: list[dict[str, Any]]) -> str:
    config = get_llm_config()
    api_key = config["api_key"]
    if not api_key:
        return build_extractive_answer(question, contexts)
    context_text = "\n\n".join(
        f"[{idx}] {item['path']}:{item['start_line']}-{item['end_line']}\n{item['content']}"
        for idx, item in enumerate(contexts, start=1)
    )
    system_prompt = "你是一个资深软件架构助手。请只基于给定代码片段回答问题。"
    user_prompt = f"""仓库：{repo_name}
问题：{question}

代码片段：
{context_text}

回答要求：
- 先给直接结论
- 说明涉及的关键文件和职责
- 如果证据不足，明确说“不确定”
- 引用片段编号，例如 [1]、[2]
"""
    content = call_chat_completion(system_prompt, user_prompt)
    if content:
        return content
    return build_extractive_answer(question, contexts)


def build_answer_prompt(question: str, repo_name: str, contexts: list[dict[str, Any]]) -> tuple[str, str]:
    context_text = "\n\n".join(
        f"[{idx}] {item['path']}:{item['start_line']}-{item['end_line']}\n{item['content']}"
        for idx, item in enumerate(contexts, start=1)
    )
    system_prompt = "你是一个资深软件架构助手。请只基于给定代码片段回答问题。"
    user_prompt = f"""仓库：{repo_name}
问题：{question}

代码片段：
{context_text}

回答要求：
- 先给直接结论
- 说明涉及的关键文件和职责
- 如果证据不足，明确说“不确定”
- 引用片段编号，例如 [1]、[2]
"""
    return system_prompt, user_prompt
