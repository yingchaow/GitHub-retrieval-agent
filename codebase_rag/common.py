from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / ".rag_demo"
REPOS_DIR = DATA_DIR / "repos"
INDEX_DIR = DATA_DIR / "indexes"
MEMORY_FILE = DATA_DIR / "memory.json"
DEFAULT_LEARNING_GOAL = "找到值得学习的开源项目，优先选择代码清晰、结构完整、README 有说明、适合阅读源码和做代码库架构问答的仓库。"

ARCHITECTURE_PATH_HINTS = {
    "app",
    "api",
    "router",
    "routes",
    "server",
    "main",
    "core",
    "service",
    "services",
    "controller",
    "controllers",
    "handler",
    "handlers",
    "model",
    "models",
    "schema",
    "schemas",
    "database",
    "db",
    "config",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "go.mod",
    "cargo.toml",
    "pom.xml",
}

CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".go",
    ".rs",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".cs",
    ".php",
    ".rb",
    ".swift",
    ".kt",
    ".kts",
    ".scala",
    ".sh",
    ".sql",
    ".html",
    ".css",
    ".scss",
    ".md",
    ".mdx",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".xml",
    ".ini",
    ".cfg",
    ".txt",
    ".dockerfile",
}

SKIP_DIRS = {
    ".git",
    ".github",
    ".idea",
    ".vscode",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "target",
    ".next",
    ".turbo",
    "coverage",
    "vendor",
    ".venv",
    "venv",
    "env",
}

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "from",
    "have",
    "has",
    "are",
    "was",
    "were",
    "you",
    "your",
    "about",
    "into",
    "where",
    "what",
    "when",
    "how",
    "why",
    "which",
    "there",
    "their",
    "它",
    "这个",
    "哪里",
    "怎么",
    "什么",
    "哪些",
    "如何",
    "项目",
    "代码",
}

def load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def ensure_dirs() -> None:
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    if not MEMORY_FILE.exists():
        save_memory({"searches": {}, "repo_screens": {}})


def load_memory() -> dict[str, Any]:
    if not MEMORY_FILE.exists():
        return {"searches": {}, "repo_screens": {}}
    try:
        data = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"searches": {}, "repo_screens": {}}
    data.setdefault("searches", {})
    data.setdefault("repo_screens", {})
    return data


def save_memory(memory: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")


def memory_key(*parts: Any) -> str:
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def slugify_repo(full_name: str) -> str:
    return full_name.replace("/", "__").replace(" ", "_")


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,}|[\u4e00-\u9fff]{2,}", text.lower())
    expanded: list[str] = []
    for token in tokens:
        if token in STOPWORDS:
            continue
        expanded.append(token)
        if "_" in token:
            expanded.extend(part for part in token.split("_") if len(part) > 1)
        camel_parts = re.findall(r"[a-z]+|[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)", token)
        expanded.extend(part.lower() for part in camel_parts if len(part) > 1)
    return expanded


def request_json(url: str, params: dict[str, Any] | None = None, method: str = "GET", body: Any = None) -> Any:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "codebase-rag-demo",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def request_bytes(url: str) -> bytes:
    headers = {"User-Agent": "codebase-rag-demo"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=90) as response:
        return response.read()


def request_json_api(
    url: str,
    method: str = "GET",
    body: Any = None,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
) -> Any:
    data = None
    final_headers = {"Content-Type": "application/json"}
    if headers:
        final_headers.update(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=final_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        message = detail
        try:
            parsed = json.loads(detail)
            message = parsed.get("status", {}).get("error") or parsed.get("message") or detail
        except json.JSONDecodeError:
            pass
        raise RuntimeError(f"HTTP {exc.code} {exc.reason}: {message}") from exc
    return json.loads(raw) if raw else {}


def extract_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
