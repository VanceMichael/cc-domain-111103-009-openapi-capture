"""比较两份描述的运行含义差异，输出可审阅报告。

结构兼容不等于语义兼容：字段类型变宽、枚举新增、分页结构变化都会
记录为变更条目，并标注受影响的字段映射。
"""
from app.schema import type_set

# 值域包含关系：number 覆盖 integer 的全部取值
_SUBSUMES = {"number": {"integer"}}


def _covers(new_types, old_types):
    """new 的值域是否覆盖 old 的全部取值。"""
    for t in old_types:
        if t in new_types:
            continue
        if not any(t in _SUBSUMES.get(n, ()) for n in new_types):
            return False
    return True


def _path_match(a, b):
    a, b = a.rstrip("/"), b.rstrip("/")
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _doc_operations(document):
    out = {}
    for path, item in document.get("paths", {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() in {"get", "put", "post", "delete", "patch", "head", "options", "trace"}:
                if isinstance(op, dict):
                    out[op.get("operationId", f"{method.upper()} {path}")] = (method.upper(), path)
    return out


def _record(out, kind, path, detail, pagination_paths):
    entry = {"kind": kind, "path": path or "/", "detail": detail}
    for p in pagination_paths:
        if _path_match(entry["path"], p):
            entry["pagination"] = True
            if entry["path"] == p and kind in {"type_widened", "type_narrowed", "type_changed"}:
                entry["kind"] = "pagination_structure_changed"
            break
    out.append(entry)


def _diff_schema(old, new, ro, rn, do, dn, path, seen, out, pagination_paths):
    if isinstance(old, dict) and isinstance(old.get("$ref"), str):
        old, do = ro.resolve_ref(old["$ref"], do)
    if isinstance(new, dict) and isinstance(new.get("$ref"), str):
        new, dn = rn.resolve_ref(new["$ref"], dn)
    if not isinstance(old, dict) or not isinstance(new, dict):
        return
    key = (id(old), id(new))
    if key in seen:
        return
    seen.add(key)
    ot, nt = type_set(old), type_set(new)
    if ot and nt and ot != nt:
        if _covers(nt, ot):
            kind = "type_widened"
        elif _covers(ot, nt):
            kind = "type_narrowed"
        else:
            kind = "type_changed"
        _record(out, kind, path, f"{sorted(ot)} → {sorted(nt)}", pagination_paths)
    eo, en = old.get("enum"), new.get("enum")
    if isinstance(eo, list) and isinstance(en, list):
        added = [v for v in en if v not in eo]
        removed = [v for v in eo if v not in en]
        if added and not removed:
            _record(out, "enum_values_added", path, f"新增 {added}", pagination_paths)
        elif removed and not added:
            _record(out, "enum_values_removed", path, f"移除 {removed}", pagination_paths)
        elif added and removed:
            _record(out, "enum_changed", path, f"新增 {added} 移除 {removed}", pagination_paths)
    po, pn = old.get("properties", {}), new.get("properties", {})
    if isinstance(po, dict) and isinstance(pn, dict):
        for name in sorted(set(po) - set(pn)):
            _record(out, "property_removed", f"{path}/{name}", "", pagination_paths)
        for name in sorted(set(pn) - set(po)):
            _record(out, "property_added", f"{path}/{name}", "", pagination_paths)
        for name in sorted(set(po) & set(pn)):
            _diff_schema(po[name], pn[name], ro, rn, do, dn, f"{path}/{name}",
                         seen, out, pagination_paths)
    ro_req = set(old.get("required", []) or [])
    rn_req = set(new.get("required", []) or [])
    for name in sorted(rn_req - ro_req):
        _record(out, "required_added", f"{path}/{name}", "", pagination_paths)
    for name in sorted(ro_req - rn_req):
        _record(out, "required_removed", f"{path}/{name}", "", pagination_paths)
    io, in_ = old.get("items"), new.get("items")
    if isinstance(io, dict) and isinstance(in_, dict):
        _diff_schema(io, in_, ro, rn, do, dn, f"{path}/*", seen, out, pagination_paths)


def _diff_parameters(old_op, new_op, out):
    old_p = {(p["name"], p.get("in", "query")): p for p in old_op.parameters}
    new_p = {(p["name"], p.get("in", "query")): p for p in new_op.parameters}
    for key in sorted(set(old_p) - set(new_p)):
        out.append({"kind": "parameter_removed",
                    "path": f"{old_op.operation_id}/parameters/{key[0]}", "detail": key[1]})
    for key in sorted(set(new_p) - set(old_p)):
        out.append({"kind": "parameter_added",
                    "path": f"{old_op.operation_id}/parameters/{key[0]}", "detail": key[1]})
    for key in sorted(set(old_p) & set(new_p)):
        o, n = old_p[key], new_p[key]
        if bool(o.get("required")) != bool(n.get("required")):
            out.append({"kind": "parameter_required_changed",
                        "path": f"{old_op.operation_id}/parameters/{key[0]}",
                        "detail": f"required: {bool(o.get('required'))} → {bool(n.get('required'))}"})
        ot = type_set(o.get("schema", {}) or {})
        nt = type_set(n.get("schema", {}) or {})
        if ot and nt and ot != nt:
            out.append({"kind": "parameter_type_changed",
                        "path": f"{old_op.operation_id}/parameters/{key[0]}",
                        "detail": f"{sorted(ot)} → {sorted(nt)}"})


def _affected_mappings(old, new, changes):
    out = []
    for plan in (old, new):
        for m in plan.mappings:
            schema = plan.response_schema_names.get(m.get("operation"))
            if not schema:
                continue
            full = f"{schema}{m.get('source', '')}"
            for c in changes:
                if _path_match(c["path"], full):
                    out.append({
                        "mapping": m.get("name"),
                        "target": m.get("target"),
                        "mapping_version": plan.mapping_version,
                        "document_version": plan.document_version,
                        "change": c["kind"],
                        "path": c["path"],
                    })
    seen, unique = set(), []
    for a in out:
        key = tuple(sorted(a.items()))
        if key not in seen:
            seen.add(key)
            unique.append(a)
    return unique


def diff_plans(old, new):
    """比较两个读取计划所依据的描述，返回可审阅的差异报告。"""
    changes = []
    pagination_paths = []
    for plan in (old, new):
        for op in plan.operations:
            if op.pagination and op.pagination.get("pointer"):
                schema = plan.response_schema_names.get(op.operation_id)
                if schema:
                    pagination_paths.append(f"{schema}{op.pagination['pointer']}")

    old_ops = _doc_operations(old.document)
    new_ops = _doc_operations(new.document)

    old_allowed = {op.operation_id: op for op in old.operations}
    new_allowed = {op.operation_id: op for op in new.operations}
    for op_id in sorted(set(old_allowed) & set(new_allowed)):
        _diff_parameters(old_allowed[op_id], new_allowed[op_id], changes)
        o, n = old_allowed[op_id], new_allowed[op_id]
        if o.response_schema is not None and n.response_schema is not None:
            root = old.response_schema_names.get(op_id) or new.response_schema_names.get(op_id) or op_id
            _diff_schema(o.response_schema, n.response_schema, old.resolver, new.resolver,
                         old.document_path, new.document_path, root, set(), changes,
                         pagination_paths)

    old_schemas = old.document.get("components", {}).get("schemas", {})
    new_schemas = new.document.get("components", {}).get("schemas", {})
    for name in sorted(set(old_schemas) & set(new_schemas)):
        _diff_schema(old_schemas[name], new_schemas[name], old.resolver, new.resolver,
                     old.document_path, new.document_path, name, set(), changes,
                     pagination_paths)

    seen, unique = set(), []
    for c in changes:
        key = (c["kind"], c["path"], c["detail"])
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return {
        "from": old.provenance(),
        "to": new.provenance(),
        "operations": {
            "added": sorted(set(new_ops) - set(old_ops)),
            "removed": sorted(set(old_ops) - set(new_ops)),
        },
        "changes": unique,
        "affected_mappings": _affected_mappings(old, new, unique),
    }


_KIND_LABELS = {
    "pagination_structure_changed": "分页结构变化",
    "type_widened": "字段类型变宽",
    "type_narrowed": "字段类型变窄",
    "type_changed": "字段类型不兼容变化",
    "enum_values_added": "枚举新增取值",
    "enum_values_removed": "枚举移除取值",
    "enum_changed": "枚举取值变化",
    "property_added": "字段新增",
    "property_removed": "字段移除",
    "required_added": "新增必需字段",
    "required_removed": "移除必需字段",
    "parameter_added": "参数新增",
    "parameter_removed": "参数移除",
    "parameter_required_changed": "参数必需性变化",
    "parameter_type_changed": "参数类型变化",
}


def format_report(report):
    src, dst = report["from"], report["to"]
    lines = [
        f"描述差异报告: {src['document_version']} ({src['document_digest'][:19]}…)"
        f" → {dst['document_version']} ({dst['document_digest'][:19]}…)"
    ]
    for op in report["operations"]["added"]:
        lines.append(f"[操作新增] {op}")
    for op in report["operations"]["removed"]:
        lines.append(f"[操作移除] {op}")
    for c in report["changes"]:
        label = _KIND_LABELS.get(c["kind"], c["kind"])
        flag = "（分页相关）" if c.get("pagination") and c["kind"] != "pagination_structure_changed" else ""
        detail = f": {c['detail']}" if c["detail"] else ""
        lines.append(f"[{label}]{flag} {c['path']}{detail}")
    if report["affected_mappings"]:
        lines.append("受影响映射:")
        for m in report["affected_mappings"]:
            lines.append(
                f"  - {m['mapping']} (映射版本 {m['mapping_version']}, "
                f"描述 {m['document_version']}): {m['change']} @ {m['path']}"
            )
    else:
        lines.append("受影响映射: 无")
    return "\n".join(lines)
