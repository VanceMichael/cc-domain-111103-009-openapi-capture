"""响应 schema 校验（JSON Schema 子集）。

$ref 惰性解析，用 (实例, schema 节点) 对切断循环，循环引用 schema
校验有限实例时必然终止。$ref 节点的同级关键字暂不展开（子集实现）。
"""


def _type_ok(instance, t):
    if t == "object":
        return isinstance(instance, dict)
    if t == "array":
        return isinstance(instance, list)
    if t == "string":
        return isinstance(instance, str)
    if t == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if t == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if t == "boolean":
        return isinstance(instance, bool)
    if t == "null":
        return instance is None
    return True


def type_set(schema):
    """归一化声明类型：3.0 的 nullable 与 3.1 的 type 列表都展开成集合。"""
    t = schema.get("type")
    types = set(t) if isinstance(t, list) else ({t} if isinstance(t, str) else set())
    if schema.get("nullable") is True:
        types.add("null")
    return types


def _esc(seg):
    return seg.replace("~", "~0").replace("/", "~1")


def validate(instance, schema, resolver, doc_path):
    """返回违例列表 [{path, message}]，空列表表示通过。path 为 JSON 指针。"""
    violations = []
    _validate(instance, schema or {}, resolver, doc_path, "", set(), violations)
    return violations


def _validate(instance, schema, resolver, doc_path, path, seen, out):
    if not isinstance(schema, dict):
        return
    ref = schema.get("$ref")
    if isinstance(ref, str):
        target, target_doc = resolver.resolve_ref(ref, doc_path)
        key = (id(instance), id(target))
        if key in seen:
            return
        seen.add(key)
        _validate(instance, target, resolver, target_doc, path, seen, out)
        return
    where = path or "/"
    if instance is None:
        types = type_set(schema)
        if types and "null" not in types:
            out.append({"path": where, "message": "null 不在声明类型内"})
        return
    types = type_set(schema)
    if types and not any(_type_ok(instance, t) for t in types):
        out.append({"path": where, "message": f"类型 {type(instance).__name__} 不符合声明 {sorted(types)}"})
        return
    if "enum" in schema and instance not in schema["enum"]:
        out.append({"path": where, "message": "取值不在枚举范围内"})
    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                out.append({"path": where, "message": f"缺少必需字段 {name}"})
        props = schema.get("properties", {})
        for name, sub in props.items():
            if name in instance:
                _validate(instance[name], sub, resolver, doc_path, f"{path}/{_esc(name)}", seen, out)
        extra = [k for k in instance if k not in props]
        addl = schema.get("additionalProperties", True)
        if addl is False:
            for name in extra:
                out.append({"path": f"{path}/{_esc(name)}", "message": "未声明的字段"})
        elif isinstance(addl, dict):
            for name in extra:
                _validate(instance[name], addl, resolver, doc_path, f"{path}/{_esc(name)}", seen, out)
    elif isinstance(instance, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(instance):
                _validate(item, items, resolver, doc_path, f"{path}/{i}", seen, out)
