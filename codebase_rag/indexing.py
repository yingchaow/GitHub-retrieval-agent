from __future__ import annotations

import ast

from .common import *


INDEX_CACHE: dict[str, CodeIndex] = {}


@dataclass
class Chunk:
    id: str
    path: str
    start_line: int
    end_line: int
    content: str
    token_count: int


class CodeIndex:
    def __init__(self, repo_id: str, full_name: str, chunks: list[Chunk]):
        self.repo_id = repo_id
        self.full_name = full_name
        self.chunks = chunks
        self.term_freqs: list[dict[str, int]] = []
        self.doc_freq: dict[str, int] = {}
        self.doc_lengths: list[int] = []
        self.avgdl = 0.0
        self._build()

    def _build(self) -> None:
        for chunk in self.chunks:
            terms = tokenize(f"{chunk.path}\n{chunk.content}")
            freq: dict[str, int] = {}
            for term in terms:
                freq[term] = freq.get(term, 0) + 1
            self.term_freqs.append(freq)
            self.doc_lengths.append(max(1, len(terms)))
            for term in freq:
                self.doc_freq[term] = self.doc_freq.get(term, 0) + 1
        self.avgdl = sum(self.doc_lengths) / max(1, len(self.doc_lengths))

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        query_terms = tokenize(query)
        if not query_terms:
            return []
        n_docs = max(1, len(self.chunks))
        k1 = 1.5
        b = 0.75
        scored: list[tuple[float, int]] = []
        for idx, freq in enumerate(self.term_freqs):
            score = 0.0
            dl = self.doc_lengths[idx]
            for term in query_terms:
                tf = freq.get(term, 0)
                if tf == 0:
                    continue
                df = self.doc_freq.get(term, 0)
                idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
                denom = tf + k1 * (1 - b + b * dl / max(1.0, self.avgdl))
                score += idf * (tf * (k1 + 1)) / denom
            if score > 0:
                scored.append((score, idx))
        scored.sort(reverse=True)
        results = []
        for score, idx in scored[:limit]:
            chunk = self.chunks[idx]
            results.append(
                {
                    "score": round(score, 4),
                    "id": chunk.id,
                    "path": chunk.path,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "content": chunk.content,
                }
            )
        return results

    def save(self) -> None:
        payload = {
            "repo_id": self.repo_id,
            "full_name": self.full_name,
            "chunks": [asdict(chunk) for chunk in self.chunks],
            "created_at": time.time(),
        }
        path = INDEX_DIR / f"{self.repo_id}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, repo_id: str) -> "CodeIndex":
        path = INDEX_DIR / f"{repo_id}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        chunks = [Chunk(**item) for item in payload["chunks"]]
        return cls(payload["repo_id"], payload["full_name"], chunks)


def get_index(repo_id: str) -> CodeIndex:
    if repo_id not in INDEX_CACHE:
        INDEX_CACHE[repo_id] = CodeIndex.load(repo_id)
    return INDEX_CACHE[repo_id]


def build_topic_tags(full_name: str, topic: str = "") -> list[str]:
    values = [topic.strip(), full_name.replace("/", " ")]
    tags: list[str] = []
    for value in values:
        if not value:
            continue
        compact = re.sub(r"\s+", " ", value).strip().lower()
        if compact and compact not in tags:
            tags.append(compact)
        for token in tokenize(value):
            if token not in tags:
                tags.append(token)
    return tags[:16]


def infer_topic_from_memory(full_name: str) -> str:
    memory = load_memory()
    for item in memory.get("searches", {}).values():
        response = item.get("response", {}) if isinstance(item, dict) else {}
        repos = response.get("items", []) if isinstance(response, dict) else []
        if any(repo.get("full_name") == full_name for repo in repos if isinstance(repo, dict)):
            query = str(item.get("query") or "").strip()
            if query:
                return query
    return ""


def ensure_index_topic_metadata(payload: dict[str, Any]) -> bool:
    full_name = str(payload.get("full_name") or "")
    topic = str(payload.get("topic") or "").strip() or infer_topic_from_memory(full_name)
    existing = payload.get("topic_tags")
    if isinstance(existing, list) and existing:
        return False
    payload["topic"] = topic
    payload["topic_tags"] = build_topic_tags(full_name, topic)
    return True


def download_repository(full_name: str) -> Path:
    repo_id = slugify_repo(full_name)
    destination = REPOS_DIR / repo_id
    if destination.exists():
        shutil.rmtree(destination)
    archive_url = f"https://api.github.com/repos/{full_name}/zipball"
    archive = request_bytes(archive_url)
    temp_zip = DATA_DIR / f"{repo_id}.zip"
    temp_zip.write_bytes(archive)
    temp_extract = DATA_DIR / f"{repo_id}_extract"
    if temp_extract.exists():
        shutil.rmtree(temp_extract)
    temp_extract.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(temp_zip) as zip_ref:
        zip_ref.extractall(temp_extract)
    roots = [path for path in temp_extract.iterdir() if path.is_dir()]
    if not roots:
        raise RuntimeError("GitHub 压缩包没有可用内容")
    shutil.move(str(roots[0]), str(destination))
    shutil.rmtree(temp_extract, ignore_errors=True)
    temp_zip.unlink(missing_ok=True)
    return destination


def is_code_file(path: Path) -> bool:
    if path.name.lower() in {"dockerfile", "makefile", "gemfile", "rakefile"}:
        return True
    return path.suffix.lower() in CODE_EXTENSIONS


def iter_source_files(repo_path: Path) -> list[Path]:
    files: list[Path] = []
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(repo_path).parts):
            continue
        if not is_code_file(path):
            continue
        try:
            if path.stat().st_size > 220_000:
                continue
        except OSError:
            continue
        files.append(path)

    def priority(path: Path) -> tuple[int, str]:
        rel = str(path.relative_to(repo_path)).lower()
        name = path.name.lower()
        if name in {"readme.md", "readme", "pyproject.toml", "package.json", "requirements.txt", "go.mod", "cargo.toml"}:
            return (0, rel)
        if name in {"main.py", "app.py", "server.py", "index.js", "index.ts", "main.go"}:
            return (1, rel)
        if any(part in rel for part in ("/api/", "/routes/", "/router", "/service", "/core", "/model", "/config", "/db")):
            return (2, rel)
        if path.suffix.lower() in {".py", ".ts", ".tsx", ".js", ".go", ".rs", ".java"}:
            return (3, rel)
        return (4, rel)

    limit = int(os.environ.get("MAX_INDEX_FILES", "260") or 260)
    return sorted(files, key=priority)[: max(50, limit)]


def read_text_lossy(path: Path) -> str:
    raw = path.read_bytes()
    if b"\x00" in raw[:2000]:
        return ""
    return raw.decode("utf-8", errors="replace")


def get_chunk_config() -> dict[str, int]:
    return {
        "default_lines": max(20, int(os.environ.get("DEFAULT_CHUNK_LINES", "90") or 90)),
        "python_lines": max(30, int(os.environ.get("PYTHON_CHUNK_LINES", "120") or 120)),
        "markdown_lines": max(20, int(os.environ.get("MARKDOWN_CHUNK_LINES", "90") or 90)),
        "overlap": max(0, int(os.environ.get("CHUNK_OVERLAP_LINES", "18") or 18)),
    }


def make_chunk(rel_path: str, lines: list[str], start: int, end: int) -> Chunk | None:
    content = "\n".join(lines[start:end]).strip()
    if not content:
        return None
    chunk_id = f"{rel_path}:{start + 1}-{end}"
    return Chunk(
        id=chunk_id,
        path=rel_path,
        start_line=start + 1,
        end_line=end,
        content=content,
        token_count=len(tokenize(content)),
    )


def split_line_window(
    rel_path: str,
    lines: list[str],
    start: int,
    end: int,
    max_lines: int,
    overlap: int,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    cursor = start
    while cursor < end:
        chunk_end = min(end, cursor + max_lines)
        chunk = make_chunk(rel_path, lines, cursor, chunk_end)
        if chunk:
            chunks.append(chunk)
        if chunk_end >= end:
            break
        cursor = max(chunk_end - overlap, cursor + 1)
    return chunks


def chunk_range_with_overlap(
    rel_path: str,
    lines: list[str],
    start: int,
    end: int,
    max_lines: int,
    overlap: int,
) -> list[Chunk]:
    if end - start <= max_lines:
        chunk = make_chunk(rel_path, lines, start, end)
        return [chunk] if chunk else []
    return split_line_window(rel_path, lines, start, end, max_lines, overlap)


def chunk_markdown(rel_path: str, lines: list[str], max_lines: int, overlap: int) -> list[Chunk]:
    heading_starts = [idx for idx, line in enumerate(lines) if re.match(r"^#{1,6}\s+\S", line)]
    if not heading_starts:
        return split_line_window(rel_path, lines, 0, len(lines), max_lines, overlap)
    chunks: list[Chunk] = []
    if heading_starts[0] > 0:
        chunks.extend(chunk_range_with_overlap(rel_path, lines, 0, heading_starts[0], max_lines, overlap))
    for pos, start in enumerate(heading_starts):
        end = heading_starts[pos + 1] if pos + 1 < len(heading_starts) else len(lines)
        chunks.extend(chunk_range_with_overlap(rel_path, lines, start, end, max_lines, overlap))
    return chunks


def chunk_python(rel_path: str, text: str, lines: list[str], max_lines: int, overlap: int) -> list[Chunk]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return split_line_window(rel_path, lines, 0, len(lines), max_lines, overlap)

    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and getattr(node, "end_lineno", None)
    ]
    if not nodes:
        return split_line_window(rel_path, lines, 0, len(lines), max_lines, overlap)

    chunks: list[Chunk] = []
    cursor = 0
    for node in sorted(nodes, key=lambda item: item.lineno):
        start = max(0, node.lineno - 1)
        end = min(len(lines), int(node.end_lineno or node.lineno))
        if cursor < start:
            chunks.extend(chunk_range_with_overlap(rel_path, lines, cursor, start, max_lines, overlap))
        chunks.extend(chunk_range_with_overlap(rel_path, lines, start, end, max_lines, overlap))
        cursor = max(cursor, end)
    if cursor < len(lines):
        chunks.extend(chunk_range_with_overlap(rel_path, lines, cursor, len(lines), max_lines, overlap))
    return chunks


def chunk_file(repo_path: Path, file_path: Path) -> list[Chunk]:
    rel_path = str(file_path.relative_to(repo_path))
    text = read_text_lossy(file_path)
    if not text.strip():
        return []
    lines = text.splitlines()
    config = get_chunk_config()
    suffix = file_path.suffix.lower()
    if suffix == ".py":
        return chunk_python(rel_path, text, lines, config["python_lines"], config["overlap"])
    if suffix in {".md", ".mdx"}:
        return chunk_markdown(rel_path, lines, config["markdown_lines"], config["overlap"])
    return split_line_window(rel_path, lines, 0, len(lines), config["default_lines"], config["overlap"])


def build_index(full_name: str, topic: str = "") -> dict[str, Any]:
    from .qdrant_store import get_qdrant_config, qdrant_is_ready, qdrant_upsert_index
    repo_path = download_repository(full_name)
    repo_id = slugify_repo(full_name)
    source_files = iter_source_files(repo_path)
    chunks: list[Chunk] = []
    for file_path in source_files:
        chunks.extend(chunk_file(repo_path, file_path))
    if not chunks:
        raise RuntimeError("没有找到可索引的代码或文档文件")
    index = CodeIndex(repo_id, full_name, chunks)
    index.save()
    index_path = INDEX_DIR / f"{repo_id}.json"
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    payload["topic"] = topic.strip()
    payload["topic_tags"] = build_topic_tags(full_name, topic)
    index_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    INDEX_CACHE[repo_id] = index
    vector_status = {"backend": "local", "enabled": False}
    warnings: list[str] = []
    qdrant_config = get_qdrant_config()
    if qdrant_config["url"]:
        if qdrant_is_ready():
            try:
                vector_status = qdrant_upsert_index(index)
            except Exception as exc:
                warnings.append(f"Qdrant 同步失败，已保留本地知识库：{exc}")
        else:
            warnings.append("已配置 QDRANT_URL，但缺少 QDRANT_API_KEY 或 embedding API key，暂未同步云向量库")
    return {
        "repo_id": repo_id,
        "full_name": full_name,
        "topic": topic.strip(),
        "topic_tags": build_topic_tags(full_name, topic),
        "file_count": len(source_files),
        "chunk_count": len(chunks),
        "vector": vector_status,
        "warnings": warnings,
    }


def list_indexes() -> list[dict[str, Any]]:
    repos = []
    for path in sorted(INDEX_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if ensure_index_topic_metadata(payload):
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        repos.append(
            {
                "repo_id": payload.get("repo_id"),
                "full_name": payload.get("full_name"),
                "topic": payload.get("topic", ""),
                "topic_tags": payload.get("topic_tags", []),
                "chunk_count": len(payload.get("chunks", [])),
                "created_at": payload.get("created_at"),
            }
        )
    return repos


def delete_index(repo_id: str) -> dict[str, Any]:
    from .qdrant_store import qdrant_delete_repo

    if not repo_id or "/" in repo_id or "\\" in repo_id or repo_id in {".", ".."}:
        raise ValueError("repo_id 不合法")
    index_path = (INDEX_DIR / f"{repo_id}.json").resolve()
    if not str(index_path).startswith(str(INDEX_DIR.resolve())):
        raise ValueError("repo_id 不合法")
    if not index_path.exists():
        raise ValueError("没有找到这个已索引仓库")

    full_name = repo_id
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        full_name = payload.get("full_name") or repo_id
    except json.JSONDecodeError:
        pass

    warnings: list[str] = []
    vector_status = {"enabled": False, "deleted": False}
    try:
        vector_status = qdrant_delete_repo(repo_id)
    except Exception as exc:
        warnings.append(f"Qdrant 删除失败，本地索引已删除：{exc}")

    index_path.unlink(missing_ok=True)
    shutil.rmtree(REPOS_DIR / repo_id, ignore_errors=True)
    INDEX_CACHE.pop(repo_id, None)
    return {
        "repo_id": repo_id,
        "full_name": full_name,
        "deleted": True,
        "vector": vector_status,
        "warnings": warnings,
    }
