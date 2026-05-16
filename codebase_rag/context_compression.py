from __future__ import annotations

from .common import *


def get_context_compression_config() -> dict[str, Any]:
    enabled = os.environ.get("CONTEXT_COMPRESSION", "1").strip().lower() not in {"0", "false", "no", "off"}
    return {
        "enabled": enabled,
        "max_contexts": max(1, int(os.environ.get("CONTEXT_MAX_CONTEXTS", "6") or 6)),
        "threshold_chars": max(1_000, int(os.environ.get("CONTEXT_COMPRESSION_THRESHOLD_CHARS", "8000") or 8000)),
        "max_chars": max(1_000, int(os.environ.get("CONTEXT_MAX_CHARS", "12000") or 12000)),
        "snippet_lines": max(8, int(os.environ.get("CONTEXT_SNIPPET_LINES", "36") or 36)),
        "window_lines": max(0, int(os.environ.get("CONTEXT_WINDOW_LINES", "2") or 2)),
    }


CODE_SIGNAL_PATTERNS = [
    r"\bclass\b",
    r"\bdef\b",
    r"\bfunction\b",
    r"\basync\b",
    r"\breturn\b",
    r"\bimport\b",
    r"\bfrom\b",
    r"\bexport\b",
    r"\broute\b",
    r"\brouter\b",
    r"\bhandler\b",
    r"\bservice\b",
    r"\bcontroller\b",
    r"\bmodel\b",
    r"\bschema\b",
    r"\bconfig\b",
    r"\bdatabase\b",
    r"\bquery\b",
    r"\bclient\b",
]


def line_score(line: str, query_terms: set[str], path_terms: set[str]) -> float:
    stripped = line.strip()
    if not stripped:
        return 0.0
    lower = stripped.lower()
    score = 0.0
    for term in query_terms:
        if term in lower:
            score += 4.0
    for term in path_terms:
        if term in lower:
            score += 1.5
    for pattern in CODE_SIGNAL_PATTERNS:
        if re.search(pattern, lower):
            score += 0.8
    if stripped.startswith(("#", "//", "/*", "*", '"""', "'''")):
        score += 0.4
    if len(stripped) > 240:
        score *= 0.75
    return score


def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    ranges.sort()
    merged = [ranges[0]]
    for start, end in ranges[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def compress_content(content: str, question: str, path: str, start_line: int, max_lines: int, window_lines: int) -> tuple[str, bool, int, int]:
    lines = content.splitlines()
    original_line_count = len(lines)
    if original_line_count <= max_lines:
        return content, False, original_line_count, original_line_count

    query_terms = set(tokenize(question))
    path_terms = set(tokenize(path))
    scored = [
        (line_score(line, query_terms, path_terms), idx)
        for idx, line in enumerate(lines)
    ]
    scored = [(score, idx) for score, idx in scored if score > 0]
    scored.sort(reverse=True)

    selected: set[int] = set()
    if not scored:
        selected.update(range(min(max_lines, original_line_count)))
    else:
        for _, idx in scored:
            range_start = max(0, idx - window_lines)
            range_end = min(original_line_count, idx + window_lines + 1)
            selected.update(range(range_start, range_end))
            if len(selected) >= max_lines:
                break

    if len(selected) > max_lines:
        keep = sorted(selected)[:max_lines]
        selected = set(keep)

    ranges = merge_ranges([(idx, idx) for idx in selected])
    output: list[str] = []
    previous_end = -1
    for range_start, range_end in ranges:
        if previous_end >= 0 and range_start > previous_end + 1:
            output.append("...")
        for idx in range(range_start, range_end + 1):
            output.append(f"{start_line + idx}: {lines[idx]}")
        previous_end = range_end
    if previous_end < original_line_count - 1:
        output.append("...")
    return "\n".join(output), True, original_line_count, len(selected)


def trim_to_budget(text: str, remaining_chars: int) -> str:
    if len(text) <= remaining_chars:
        return text
    if remaining_chars <= 32:
        return ""
    trimmed = text[: remaining_chars - 16].rstrip()
    return f"{trimmed}\n..."


def compress_contexts(contexts: list[dict[str, Any]], question: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = get_context_compression_config()
    selected = contexts[: config["max_contexts"]]
    original_chars = sum(len(str(item.get("content", ""))) for item in selected)
    if not config["enabled"]:
        return selected, {
            "enabled": False,
            "scope": "current_turn",
            "input_contexts": len(contexts),
            "output_contexts": len(selected),
            "original_chars": original_chars,
            "compressed_chars": original_chars,
            "ratio": 1.0,
        }

    if original_chars < config["threshold_chars"]:
        return selected, {
            "enabled": True,
            "scope": "current_turn",
            "skipped": True,
            "reason": "below_threshold",
            "input_contexts": len(contexts),
            "output_contexts": len(selected),
            "compressed_contexts": 0,
            "original_chars": original_chars,
            "compressed_chars": original_chars,
            "ratio": 1.0,
            "threshold_chars": config["threshold_chars"],
            "max_chars": config["max_chars"],
        }

    compressed_contexts: list[dict[str, Any]] = []
    used_chars = 0
    compressed_chars = 0
    compressed_items = 0

    for item in selected:
        original = str(item.get("content", ""))
        content, was_compressed, original_lines, compressed_lines = compress_content(
            original,
            question,
            str(item.get("path", "")),
            int(item.get("start_line", 0) or 0),
            config["snippet_lines"],
            config["window_lines"],
        )
        remaining = config["max_chars"] - used_chars
        if remaining <= 0:
            break
        content = trim_to_budget(content, remaining)
        if not content:
            break
        used_chars += len(content)
        compressed_chars += len(content)
        compressed_item = dict(item)
        compressed_item.update(
            {
                "content": content,
                "compressed": bool(was_compressed or len(content) < len(original)),
                "original_chars": len(original),
                "compressed_chars": len(content),
                "original_line_count": original_lines,
                "compressed_line_count": compressed_lines,
            }
        )
        if compressed_item["compressed"]:
            compressed_items += 1
        compressed_contexts.append(compressed_item)

    ratio = round(compressed_chars / max(1, original_chars), 3)
    return compressed_contexts, {
        "enabled": True,
        "scope": "current_turn",
        "input_contexts": len(contexts),
        "output_contexts": len(compressed_contexts),
        "compressed_contexts": compressed_items,
        "original_chars": original_chars,
        "compressed_chars": compressed_chars,
        "ratio": ratio,
        "threshold_chars": config["threshold_chars"],
        "max_chars": config["max_chars"],
    }
