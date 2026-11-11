"""从采集配置与 OpenAPI 描述生成受控的只读读取计划。

采集配置引用：文档文件、文档版本、允许访问的操作、字段映射与映射版本。
计划生成时强制只读策略：只允许 GET；禁止 path/operation 级 servers 覆盖；
server+path 拼接结果必须留在声明的 https 来源内。
"""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from app.errors import PlanError, PolicyError
from app.openapi import read_document
from app.refs import Resolver
from app.serialize import (encode_query, expand_path, join_url, origin_of,
                           serialize_query_pairs)

METHODS = {"get", "put", "post", "delete", "patch", "head", "options", "trace"}


def load_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    return config, Path(path).parent


def document_digest(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def merge_parameters(path_item, operation):
    """路径级参数被操作继承，操作级同名同位置参数覆盖路径级。"""
    merged = {}
    for spec in list(path_item.get("parameters", [])) + list(operation.get("parameters", [])):
        merged[(spec.get("name"), spec.get("in", "query"))] = spec
    return list(merged.values())


@dataclass
class PlannedOperation:
    operation_id: str
    path: str
    method: str
    url: str
    parameters: list
    response_schema: dict | None
    pagination: dict | None


@dataclass
class ReadPlan:
    name: str
    document_version: str
    document_digest: str
    mapping_version: str
    document_path: str
    document: dict
    resolver: Resolver
    allowed_origins: frozenset
    base_paths: tuple
    operations: list
    mappings: list
    response_schema_names: dict

    def provenance(self):
        """每次采集结果都要记录的溯源信息。"""
        return {
            "document_version": self.document_version,
            "document_digest": self.document_digest,
            "mapping_version": self.mapping_version,
        }

    def request_specs(self):
        return [
            {"operation": op.operation_id, "method": op.method.upper(), "url": op.url}
            for op in self.operations
        ]


def _json_schema_of(operation):
    content = operation.get("responses", {}).get("200", {}).get("content", {})
    if isinstance(content, dict):
        for media, media_spec in content.items():
            if isinstance(media_spec, dict) and media.split(";")[0].endswith("json"):
                schema = media_spec.get("schema")
                if isinstance(schema, dict):
                    return schema
    return None


def build_plan(config, *, base_dir="."):
    base = Path(base_dir)
    for key in ("document", "document_version", "mapping_version", "allowed_operations"):
        if key not in config:
            raise PlanError(f"采集配置缺少字段: {key}")
    doc_path = base / config["document"]
    document = read_document(doc_path)
    resolver = Resolver(doc_path)
    servers = document.get("servers")
    if not servers or not isinstance(servers, list):
        raise PlanError("文档缺少 servers，无法确定读取范围")
    for server in servers:
        join_url(server["url"], "/")  # 对所有声明 server 先过一遍来源策略
    allow_overrides = bool(config.get("allow_server_overrides", False))

    operations_index = {}
    for path, item in document["paths"].items():
        if not isinstance(item, dict):
            continue
        item_override = "servers" in item
        for method, operation in item.items():
            if method.lower() not in METHODS or not isinstance(operation, dict):
                continue
            op_id = operation.get("operationId", f"{method.upper()} {path}")
            override = item_override or "servers" in operation
            operations_index[op_id] = (path, method.lower(), item, operation, override)

    planned = []
    schema_names = {}
    server_url = servers[0]["url"]
    for op_id in config["allowed_operations"]:
        if op_id not in operations_index:
            raise PlanError(f"允许列表中的操作不存在: {op_id}")
        path, method, item, operation, override = operations_index[op_id]
        if override and not allow_overrides:
            raise PolicyError(f"操作 {op_id} 声明了 servers 覆盖，超出计划范围")
        if method != "get":
            raise PolicyError(f"只读策略仅允许 GET，拒绝 {op_id} ({method.upper()})")
        params = merge_parameters(item, operation)
        static = config.get("params", {}).get(op_id, {})
        url = join_url(server_url, expand_path(path, params, static))
        pairs = serialize_query_pairs(params, static)
        if pairs:
            url = f"{url}?{encode_query(pairs)}"
        schema = _json_schema_of(operation)
        declared = config.get("response_schemas", {}).get(op_id)
        if schema is None and declared:
            schema = {"$ref": f"#/components/schemas/{declared}"}
        if declared:
            schema_names[op_id] = declared
        elif isinstance(schema, dict) and schema.get("$ref", "").startswith("#/components/schemas/"):
            schema_names[op_id] = schema["$ref"].rsplit("/", 1)[-1]
        planned.append(PlannedOperation(
            op_id, path, "get", url, params, schema,
            config.get("pagination", {}).get(op_id),
        ))

    allowed_origins = frozenset(
        {origin_of(s["url"]) for s in servers} | set(config.get("allowed_origins", []))
    )
    base_paths = tuple((urlsplit(s["url"]).path or "").rstrip("/") for s in servers)
    return ReadPlan(
        name=config.get("name", doc_path.stem),
        document_version=config["document_version"],
        document_digest=document_digest(doc_path),
        mapping_version=config["mapping_version"],
        document_path=str(doc_path),
        document=document,
        resolver=resolver,
        allowed_origins=allowed_origins,
        base_paths=base_paths,
        operations=planned,
        mappings=list(config.get("mappings", [])),
        response_schema_names=schema_names,
    )
