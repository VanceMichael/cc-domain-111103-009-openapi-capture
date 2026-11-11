"""解析 OpenAPI $ref：本地指针、跨文件引用与循环检测。

只允许解析本地文件；任何带 scheme 的远程引用一律拒绝，
保证解析过程不访问外网。
"""
import json
from pathlib import Path


class RefError(ValueError):
    """$ref 无法解析或引用了被禁止的目标。"""


def _key(path):
    return str(Path(path).resolve())


class Resolver:
    """按文档缓存解析 $ref，支持跨文件引用。"""

    def __init__(self, root_path):
        self.root_path = Path(root_path)
        self._docs = {}

    def load(self, path=None):
        p = Path(path) if path is not None else self.root_path
        key = _key(p)
        if key not in self._docs:
            try:
                self._docs[key] = json.loads(Path(key).read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise RefError(f"引用文件不存在: {p}") from exc
        return self._docs[key]

    def resolve_ref(self, ref, current):
        """把 ref 解析为 (节点, 所在文档路径)。远程引用直接拒绝。"""
        if "://" in ref:
            raise RefError(f"禁止远程 $ref: {ref}")
        doc_part, _, pointer = ref.partition("#")
        current = Path(current)
        doc_path = current if not doc_part else current.parent / doc_part
        doc = self.load(doc_path)
        node = doc
        if pointer and pointer != "/":
            for seg in pointer.lstrip("/").split("/"):
                seg = seg.replace("~1", "/").replace("~0", "~")
                try:
                    node = node[int(seg)] if isinstance(node, list) else node[seg]
                except (KeyError, IndexError, ValueError) as exc:
                    raise RefError(f"$ref 指向不存在的节点: {ref}") from exc
        return node, str(doc_path)


def _direct_refs(node, out):
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            out.append(ref)
        for key, value in node.items():
            if key != "$ref":
                _direct_refs(value, out)
    elif isinstance(node, list):
        for item in node:
            _direct_refs(item, out)


def find_cycles(resolver):
    """在 components/schemas 层面检测循环引用，返回循环路径（schema 名列表）。"""
    edges = {}
    queue = [resolver.root_path]
    seen_docs = set()
    while queue:
        doc_path = Path(queue.pop())
        key = _key(doc_path)
        if key in seen_docs:
            continue
        seen_docs.add(key)
        doc = resolver.load(doc_path)
        schemas = doc.get("components", {}).get("schemas", {})
        if not isinstance(schemas, dict):
            continue
        for name, schema in schemas.items():
            node = (key, name)
            refs = []
            _direct_refs(schema, refs)
            for ref in refs:
                if "://" in ref:
                    continue
                doc_part, _, pointer = ref.partition("#")
                target_path = doc_path if not doc_part else doc_path.parent / doc_part
                segs = pointer.lstrip("/").split("/") if pointer else []
                if segs[:2] == ["components", "schemas"] and len(segs) == 3:
                    edges.setdefault(node, []).append((_key(target_path), segs[2]))
                    queue.append(target_path)
    cycles = []
    stack, done = [], set()

    def visit(node):
        if node in stack:
            cycles.append(stack[stack.index(node):] + [node])
            return
        if node in done:
            return
        stack.append(node)
        for nxt in edges.get(node, []):
            visit(nxt)
        stack.pop()
        done.add(node)

    for node in list(edges):
        visit(node)
    unique = []
    for cyc in cycles:
        names = [n[1] for n in cyc]
        if names not in unique:
            unique.append(names)
    return unique
