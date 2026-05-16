from __future__ import annotations

from .common import *
from .github_discovery import search_github_repositories, screen_repository
from .indexing import build_index, delete_index, list_indexes
from .llm import build_extractive_answer, build_answer_prompt, call_llm_answer, get_embedding_config, get_llm_config, stream_chat_completion
from .qa import answer_question, retrieve_contexts
from .qdrant_store import get_qdrant_config, qdrant_is_ready


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "CodebaseRAGDemo/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/api/search":
                params = urllib.parse.parse_qs(parsed.query)
                query = params.get("query", [""])[0]
                language = params.get("language", [""])[0]
                min_stars = int(params.get("min_stars", ["0"])[0] or 0)
                limit = int(params.get("limit", ["10"])[0] or 10)
                json_response(self, 200, search_github_repositories(query, language, min_stars, limit))
                return
            if parsed.path == "/api/repos":
                json_response(self, 200, {"items": list_indexes()})
                return
            if parsed.path == "/api/status":
                memory = load_memory()
                json_response(
                    self,
                    200,
                    {
                        "llm": bool(get_llm_config()["api_key"]),
                        "qdrant": qdrant_is_ready(),
                        "qdrant_configured": bool(get_qdrant_config()["url"]),
                        "embedding": bool(get_embedding_config()["api_key"]),
                        "memory": {
                            "searches": len(memory.get("searches", {})),
                            "repo_screens": len(memory.get("repo_screens", {})),
                        },
                    },
                )
                return
            if parsed.path == "/api/memory":
                memory = load_memory()
                json_response(
                    self,
                    200,
                    {
                        "search_count": len(memory.get("searches", {})),
                        "repo_screen_count": len(memory.get("repo_screens", {})),
                    },
                )
                return
            self.serve_static(parsed.path)
        except Exception as exc:
            self.handle_exception(exc)

    def do_HEAD(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            self.serve_static(parsed.path, head_only=True)
        except Exception as exc:
            self.handle_exception(exc)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            body = read_json_body(self)
            if parsed.path == "/api/ingest":
                full_name = str(body.get("full_name", "")).strip()
                topic = str(body.get("topic", "")).strip()
                if not full_name or "/" not in full_name:
                    raise ValueError("full_name 格式应为 owner/repo")
                json_response(self, 200, build_index(full_name, topic))
                return
            if parsed.path == "/api/repos/delete":
                repo_id = str(body.get("repo_id", "")).strip()
                confirm = bool(body.get("confirm"))
                if not confirm:
                    raise ValueError("删除仓库索引前需要确认")
                json_response(self, 200, delete_index(repo_id))
                return
            if parsed.path == "/api/screen":
                full_name = str(body.get("full_name", "")).strip()
                goal = str(body.get("goal", "")).strip()
                json_response(self, 200, screen_repository(full_name, goal))
                return
            if parsed.path == "/api/ask":
                repo_id = str(body.get("repo_id", "")).strip()
                question = str(body.get("question", "")).strip()
                json_response(self, 200, answer_question(repo_id, question))
                return
            if parsed.path == "/api/ask_stream":
                repo_id = str(body.get("repo_id", "")).strip()
                question = str(body.get("question", "")).strip()
                self.stream_answer(repo_id, question)
                return
            json_response(self, 404, {"error": "Not found"})
        except Exception as exc:
            self.handle_exception(exc)

    def send_sse(self, event: str, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False)
        self.wfile.write(f"event: {event}\n".encode("utf-8"))
        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
        self.wfile.flush()

    def stream_answer(self, repo_id: str, question: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            retrieved = retrieve_contexts(repo_id, question)
            self.send_sse("meta", retrieved)
            if not get_llm_config()["api_key"]:
                answer = build_extractive_answer(question, retrieved["contexts"])
                for line in answer.splitlines(True):
                    self.send_sse("delta", {"text": line})
                self.send_sse("done", {"ok": True})
                return
            system_prompt, user_prompt = build_answer_prompt(question, retrieved["full_name"], retrieved["contexts"])
            wrote = False
            for text in stream_chat_completion(system_prompt, user_prompt):
                wrote = True
                self.send_sse("delta", {"text": text})
            if not wrote:
                fallback = call_llm_answer(question, retrieved["full_name"], retrieved["contexts"])
                self.send_sse("delta", {"text": fallback})
            self.send_sse("done", {"ok": True})
        except Exception as exc:
            self.send_sse("error", {"error": str(exc)})
            self.send_sse("done", {"ok": False})

    def serve_static(self, path: str, head_only: bool = False) -> None:
        if path == "/":
            path = "/index.html"
        target = (STATIC_DIR / path.lstrip("/")).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.exists() or not target.is_file():
            json_response(self, 404, {"error": "Not found"})
            return
        content_type = "text/plain; charset=utf-8"
        if target.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif target.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif target.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def handle_exception(self, exc: Exception) -> None:
        status = 400
        message = str(exc)
        if isinstance(exc, urllib.error.HTTPError):
            status = exc.code
            try:
                detail = exc.read().decode("utf-8")
                parsed = json.loads(detail)
                message = parsed.get("message", detail)
            except Exception:
                message = exc.reason
        elif isinstance(exc, urllib.error.URLError):
            status = 502
            message = f"网络请求失败：{exc.reason}"
        elif not isinstance(exc, ValueError):
            status = 500
        json_response(self, status, {"error": message})


def main() -> None:
    load_env()
    ensure_dirs()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), DemoHandler)
    print(f"Codebase RAG demo running at http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
