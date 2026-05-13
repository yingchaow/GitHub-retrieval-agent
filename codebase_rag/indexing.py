from __future__ import annotations

from .common import *


INDEX_CACHE: dict[str, CodeIndex] = {}


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


def chunk_file(repo_path: Path, file_path: Path, chunk_lines: int = 90, overlap: int = 18) -> list[Chunk]:
    rel_path = str(file_path.relative_to(repo_path))
    text = read_text_lossy(file_path)
    if not text.strip():
        return []
    lines = text.splitlines()
    chunks: list[Chunk] = []
    start = 0
    while start < len(lines):
        end = min(len(lines), start + chunk_lines)
        content = "\n".join(lines[start:end]).strip()
        if content:
            chunk_id = f"{rel_path}:{start + 1}-{end}"
            chunks.append(
                Chunk(
                    id=chunk_id,
                    path=rel_path,
                    start_line=start + 1,
                    end_line=end,
                    content=content,
                    token_count=len(tokenize(content)),
                )
            )
        if end == len(lines):
            break
        start = max(end - overlap, start + 1)
    return chunks


def build_index(full_name: str) -> dict[str, Any]:
    from .qdrant import get_qdrant_config, qdrant_is_ready, qdrant_upsert_index
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
        repos.append(
            {
                "repo_id": payload.get("repo_id"),
                "full_name": payload.get("full_name"),
                "chunk_count": len(payload.get("chunks", [])),
                "created_at": payload.get("created_at"),
            }
        )
    return repos
