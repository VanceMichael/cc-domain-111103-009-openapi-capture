import json
from pathlib import Path

def read_document(path):
    value=json.loads(Path(path).read_text(encoding="utf-8"))
    if "openapi" not in value or not isinstance(value.get("paths"),dict): raise ValueError("OpenAPI 文档结构无效")
    return value

def operation_names(document):
    return sorted(f"{path} {method.upper()}" for path,item in document["paths"].items() for method in item if method.lower() in {"get","post","put","delete","patch"})
