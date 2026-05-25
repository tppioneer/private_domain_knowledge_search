"""GET /api/v1/entities/{name} —— 精确实体查询。"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Query

from ..engine.factory import create_entity_searcher
from ..models.schemas import EntityDefinition, EntityDetailResponse, EntityType, VersionChange

router = APIRouter()

# 版本要求解析: ">=2.1.0" → (">=", [2,1,0])
_VERSION_RE = re.compile(r"(>=|<=|==|>|<)?\s*(\d+(?:\.\d+)*)")


def _parse_version_req(requirement: str | None) -> tuple[str, tuple[int, ...]] | None:
    if not requirement:
        return None
    m = _VERSION_RE.match(requirement.strip())
    if not m:
        return None
    op = m.group(1) or "=="
    parts = tuple(int(x) for x in m.group(2).split("."))
    return op, parts


def _version_satisfies(version_str: str, op: str, target: tuple[int, ...]) -> bool:
    if not version_str:
        return True  # 无版本信息时不过滤
    try:
        parts = tuple(int(x) for x in version_str.split("."))
    except ValueError:
        return True
    if op == "==":
        return parts == target
    if op == ">=":
        return parts >= target
    if op == "<=":
        return parts <= target
    if op == ">":
        return parts > target
    if op == "<":
        return parts < target
    return True


def _build_response(
    name: str, candidates: list[dict], version_req: str | None,
) -> EntityDetailResponse | None:
    """从候选列表中构建最佳 EntityDetailResponse。"""
    if not candidates:
        return None

    # 过滤版本要求
    ver_parsed = _parse_version_req(version_req)
    filtered = candidates
    if ver_parsed:
        op, target = ver_parsed
        filtered = [
            c for c in candidates
            if _version_satisfies(c["chunk"]["meta"].get("version", ""), op, target)
        ]
        if not filtered:
            return None

    best = filtered[0]
    chunk = best["chunk"]
    meta = chunk.get("meta", {})
    class_name = meta.get("class_name", "")
    method = meta.get("method", "")
    return_type = meta.get("return_type", "")
    return_type_import = meta.get("return_type_import", "")
    version = meta.get("version", "")
    content = chunk.get("content", "")

    # 从 content 中提取参数列表（格式: "ClassName.method(type1 param1, type2 param2) → returnType"）
    params: list[dict] = []
    params_match = re.search(r"\((.*?)\)", content)
    if params_match and params_match.group(1):
        for p in params_match.group(1).split(","):
            p = p.strip()
            parts = p.rsplit(" ", 1)
            if len(parts) == 2:
                params.append({"type": parts[0], "name": parts[1]})
            elif p:
                params.append({"type": p, "name": ""})

    # 返回值显示：有完整路径用完整路径，否则用简单名
    signature_return = return_type_import or return_type

    signature = ""
    if class_name and method:
        sig_parts = [f"{class_name}.{method}({params_match.group(1) if params_match else ''})"]
        if signature_return:
            sig_parts.append(f" → {signature_return}")
        signature = "".join(sig_parts)

    is_api = chunk.get("type") in ("api", "term")  # 两种都可能
    entity_type = EntityType.API if is_api else EntityType.TERM

    # 层级字段
    layer = meta.get("layer")
    requires_context = meta.get("requires_context_building", False)
    context_deps = meta.get("context_dependencies", [])
    context_provider = meta.get("standard_context_provider")
    suggested_alt = meta.get("suggested_alternative")
    alt_obj = None
    if suggested_alt and isinstance(suggested_alt, dict):
        from ..models.schemas import SuggestedAlternative
        alt_obj = SuggestedAlternative(**suggested_alt)

    return EntityDetailResponse(
        entity_name=name,
        entity_type=entity_type,
        definition=EntityDefinition(
            signature=signature or content,
            parameters=params,
            return_type=signature_return or None,
            since_version=version or None,
            code_example=_build_code_example(class_name, method, params, signature_return or return_type),
            layer=layer,
            requires_context_building=requires_context,
            context_dependencies=context_deps,
            standard_context_provider=context_provider,
            suggested_alternative=alt_obj,
        ),
    )


def _build_code_example(
    class_name: str, method: str, params: list[dict], return_type: str,
) -> str | None:
    if not class_name or not method:
        return None
    simple = class_name.rsplit(".", 1)[-1]
    param_names = ", ".join(p["name"] for p in params if p["name"])
    ret = f" = {simple}.{method}({param_names})"
    if return_type and return_type != "void":
        ret = f"{return_type} result" + ret
    return ret


@router.get("/entities/{name}", response_model=EntityDetailResponse)
async def get_entity(
    name: str,
    entity_type: str | None = Query(None, description="term | api"),
    version_requirement: str | None = Query(None, description="如 >=2.1.0"),
) -> EntityDetailResponse:
    searcher = create_entity_searcher()
    candidates = searcher.search_entity(name, entity_type=entity_type)

    if not candidates:
        raise HTTPException(status_code=404, detail=f"entity not found: {name}")

    response = _build_response(name, candidates, version_requirement)
    if response is None:
        raise HTTPException(
            status_code=404,
            detail=f"entity not found: {name} (version: {version_requirement})",
        )
    return response
