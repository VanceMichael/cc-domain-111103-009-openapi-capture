"""本地响应重放：按计划读取本地录制的响应，校验 schema，异常即隔离。

只读取本地文件，不发起网络请求。响应违背 schema 或分页链接越界时，
隔离对应操作：保留已采集页，既不伪装成空页，也不清空已有批次结果。
"""
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.schema import validate
from app.serialize import check_next_url

MAX_PAGES = 100


def _norm(url):
    parts = urlsplit(url)
    pairs = sorted(parse_qsl(parts.query, keep_blank_values=True))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), ""))


class ResponseBook:
    """本地录制的响应表，按 (方法, 规范化 URL) 查找。"""

    def __init__(self, entries):
        self._entries = {}
        for e in entries:
            self._entries[(e.get("method", "GET").upper(), _norm(e["url"]))] = e.get("body")

    @classmethod
    def from_file(cls, path, label):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if label not in data:
            raise KeyError(f"响应录制中不存在标签: {label}")
        return cls(data[label])

    def lookup(self, method, url):
        return self._entries.get((method.upper(), _norm(url)))


def _pointer(body, pointer):
    node = body
    for seg in pointer.lstrip("/").split("/") if pointer else []:
        if not isinstance(node, dict) or seg not in node:
            return None
        node = node[seg]
    return node


def _set_query_param(url, name, value):
    parts = urlsplit(url)
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != name]
    pairs.append((name, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), ""))


def batch_id(plan, operation_id):
    raw = f"{plan.document_digest}|{plan.mapping_version}|{operation_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _quarantine(result, reason, detail):
    result["status"] = "quarantined"
    result["quarantine"] = {"reason": reason, "detail": detail}


def run_operation(plan, op, book):
    result = {
        "batch_id": batch_id(plan, op.operation_id),
        "plan": plan.provenance(),
        "operation": op.operation_id,
        "requests": [],
        "pages": [],
        "status": "completed",
        "quarantine": None,
    }
    url = op.url
    for _ in range(MAX_PAGES):
        body = book.lookup("GET", url)
        if body is None:
            if not result["pages"]:
                _quarantine(result, "response_missing", f"本地录制中缺少响应: GET {url}")
            break
        result["requests"].append({"method": "GET", "url": url})
        if op.response_schema is not None:
            violations = validate(body, op.response_schema, plan.resolver, plan.document_path)
            if violations:
                _quarantine(result, "schema_violation", violations)
                break
        result["pages"].append(body)
        page = op.pagination or {}
        kind = page.get("kind")
        if not kind:
            break
        nxt = _pointer(body, page.get("pointer", ""))
        if nxt is None:
            break
        if kind == "cursor-param":
            if not isinstance(nxt, (str, int)) or isinstance(nxt, bool):
                _quarantine(result, "next_link_invalid", "游标取值不是标量")
                break
            url = _set_query_param(url, page["param"], str(nxt))
        elif kind == "next-url":
            if not isinstance(nxt, str):
                _quarantine(result, "next_link_invalid", "分页链接不是字符串")
                break
            reason = check_next_url(nxt, plan.allowed_origins, plan.base_paths)
            if reason:
                _quarantine(result, "next_link_rejected", {"url": nxt, "reason": reason})
                break
            url = nxt
        else:
            _quarantine(result, "next_link_invalid", f"未知分页方式: {kind}")
            break
    else:
        _quarantine(result, "pagination_limit", f"超过 {MAX_PAGES} 页上限")
    return result


def write_batch(plan, result, out_dir):
    """落盘批次结果。已完成的批次文件不会被隔离结果覆盖。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"batch-{result['operation']}-{result['batch_id']}.json"
    if result["status"] == "quarantined" and path.exists():
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prior = {}
        if prior.get("status") == "completed":
            path = out / f"quarantine-{result['operation']}-{result['batch_id']}.json"
    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def run_replay(plan, book, out_dir):
    """对计划中的每个操作独立重放；单个操作被隔离不影响其他操作。"""
    results = []
    for op in plan.operations:
        result = run_operation(plan, op, book)
        result["output_file"] = str(write_batch(plan, result, out_dir))
        results.append(result)
    return results
