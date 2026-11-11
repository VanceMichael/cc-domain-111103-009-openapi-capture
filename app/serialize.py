"""OpenAPI 参数序列化与 URL 拼接。只构造字符串，不发起任何请求。"""
import re
from urllib.parse import quote, urlsplit

from app.errors import PlanError, PolicyError


class SerializationError(PlanError):
    """参数序列化不被支持或取值非法。"""


def _fmt(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _q(value):
    return quote(_fmt(value), safe="")


def serialize_query_pairs(parameters, values):
    """按声明的 style/explode 序列化查询参数，返回 (名, 已编码值) 对列表。"""
    pairs = []
    for spec in parameters:
        if spec.get("in", "query") != "query":
            continue
        name = spec["name"]
        value = values.get(name)
        if value is None:
            continue
        style = spec.get("style", "form")
        explode = spec.get("explode", style == "form")
        if isinstance(value, list):
            if style == "form":
                if explode:
                    pairs.extend((name, _q(v)) for v in value)
                else:
                    pairs.append((name, ",".join(_q(v) for v in value)))
            elif style == "spaceDelimited":
                if explode:
                    pairs.extend((name, _q(v)) for v in value)
                else:
                    pairs.append((name, "%20".join(_q(v) for v in value)))
            elif style == "pipeDelimited":
                if explode:
                    pairs.extend((name, _q(v)) for v in value)
                else:
                    pairs.append((name, "%7C".join(_q(v) for v in value)))
            else:
                raise SerializationError(f"不支持的查询数组格式: {style}")
        elif isinstance(value, dict):
            if style == "deepObject" and explode:
                pairs.extend((f"{name}[{k}]", _q(v)) for k, v in value.items())
            elif style == "form":
                if explode:
                    pairs.extend((k, _q(v)) for k, v in value.items())
                else:
                    flat = []
                    for k, v in value.items():
                        flat.append(_q(k))
                        flat.append(_q(v))
                    pairs.append((name, ",".join(flat)))
            else:
                raise SerializationError(f"对象参数不支持 style={style}")
        else:
            pairs.append((name, _q(value)))
    return pairs


def encode_query(pairs):
    return "&".join(f"{quote(k, safe='[]')}={v}" for k, v in pairs)


_TEMPLATE = re.compile(r"\{([^{}]+)\}")


def expand_path(path_template, parameters, values):
    """按 simple/label/matrix 展开路径模板参数。"""
    specs = {p["name"]: p for p in parameters if p.get("in") == "path"}

    def repl(match):
        name = match.group(1)
        if name not in specs:
            raise SerializationError(f"路径参数未声明: {name}")
        if values.get(name) is None:
            raise SerializationError(f"路径参数缺少取值: {name}")
        spec = specs[name]
        style = spec.get("style", "simple")
        explode = spec.get("explode", False)
        value = values[name]
        if isinstance(value, dict):
            raise SerializationError("路径参数暂不支持对象取值")
        items = value if isinstance(value, list) else [value]
        body = ",".join(_q(v) for v in items)
        if style == "simple":
            return body
        if style == "label":
            if explode and isinstance(value, list):
                return "." + ".".join(_q(v) for v in items)
            return "." + body
        if style == "matrix":
            if explode and isinstance(value, list):
                return "".join(f";{quote(name, safe='')}={_q(v)}" for v in items)
            return f";{quote(name, safe='')}={body}"
        raise SerializationError(f"不支持的路径参数格式: {style}")

    return _TEMPLATE.sub(repl, path_template)


def origin_of(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def join_url(server, path):
    """拼接 server 与路径，拒绝会把请求带离声明范围的写法。"""
    parts = urlsplit(server)
    if parts.scheme != "https":
        raise PolicyError(f"server 必须使用 https: {server}")
    if parts.query or parts.fragment:
        raise PolicyError("server URL 不允许携带 query/fragment")
    if parts.username or parts.password:
        raise PolicyError("server URL 不允许携带凭据")
    if not path.startswith("/") or "://" in path:
        raise PolicyError(f"非法路径拼接: {path}")
    if any(seg == ".." for seg in path.split("/")):
        raise PolicyError("路径不允许包含 .. 段")
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}{path}"


def check_next_url(url, allowed_origins, base_paths):
    """校验响应给出的分页链接。返回 None 表示允许，否则返回拒绝原因。"""
    parts = urlsplit(url)
    if parts.scheme != "https":
        return "分页链接必须使用 https"
    if parts.username or parts.password:
        return "分页链接不允许携带凭据"
    if origin_of(url) not in allowed_origins:
        return f"分页链接 origin 未授权: {origin_of(url)}"
    path = parts.path or "/"
    in_scope = False
    for base in base_paths:
        base = base.rstrip("/")
        if not base or path == base or path.startswith(base + "/"):
            in_scope = True
            break
    if not in_scope:
        return f"分页链接路径超出声明范围: {path}"
    if any(seg == ".." for seg in path.split("/")):
        return "分页链接路径不允许包含 .. 段"
    return None
