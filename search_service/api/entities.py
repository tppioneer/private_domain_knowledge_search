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
    """从候选列表中构建最佳 EntityDetailResponse。

    类名查询（无点）：返回该类的所有方法签名。
    方法名查询（有点或单方法名）：返回精确匹配的方法。
    """
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
    best_chunk = best["chunk"]
    best_meta = best_chunk.get("meta", {})
    best_class = best_meta.get("class_name", "")
    is_class_query = "." not in name

    # 类名查询：收集该类的所有方法
    if is_class_query and best_class:
        class_candidates = [
            c for c in filtered
            if c["chunk"]["meta"].get("class_name", "") == best_class
        ]
    else:
        class_candidates = [best]

    # 构建所有方法签名
    method_sigs, construction_sig = _build_class_methods(class_candidates)

    # 选代表方法（优先构造入口）
    rep = best
    for c in class_candidates:
        if c["chunk"]["meta"].get("construction_pattern"):
            rep = c
            break

    rep_chunk = rep["chunk"]
    rep_meta = rep_chunk.get("meta", {})
    version = rep_meta.get("version", "")
    return_type = rep_meta.get("return_type", "")
    return_type_import = rep_meta.get("return_type_import", "")
    signature_return = return_type_import or return_type

    signature = method_sigs if is_class_query else (method_sigs.strip() if method_sigs else rep_chunk.get("content", ""))

    is_api = rep_chunk.get("type") in ("api", "term")
    entity_type = EntityType.API if is_api else EntityType.TERM

    layer = rep_meta.get("layer")
    requires_context = rep_meta.get("requires_context_building", False)
    context_deps = rep_meta.get("context_dependencies", [])
    context_provider = rep_meta.get("standard_context_provider")
    suggested_alt = rep_meta.get("suggested_alternative")
    alt_obj = None
    if suggested_alt and isinstance(suggested_alt, dict):
        from ..models.schemas import SuggestedAlternative
        alt_obj = SuggestedAlternative(**suggested_alt)

    # 类查询时 code_example 优先用构造入口
    code_example = construction_sig or _build_code_example(
        rep_meta.get("class_name", ""),
        rep_meta.get("method", ""),
        _extract_params(rep_chunk.get("content", "")),
        signature_return or return_type,
    )

    return EntityDetailResponse(
        entity_name=name,
        entity_type=entity_type,
        definition=EntityDefinition(
            signature=signature or rep_chunk.get("content", ""),
            parameters=_extract_params(rep_chunk.get("content", "")),
            return_type=signature_return or None,
            since_version=version or None,
            code_example=code_example,
            definition_text=f"共 {len(class_candidates)} 个方法" if is_class_query else None,
            layer=layer,
            requires_context_building=requires_context,
            context_dependencies=context_deps,
            standard_context_provider=context_provider,
            suggested_alternative=alt_obj,
        ),
    )


def _extract_params(content: str) -> list[dict]:
    """从 content 中提取参数列表。"""
    params: list[dict] = []
    m = re.search(r"\((.*?)\)", content)
    if m and m.group(1):
        for p in m.group(1).split(","):
            p = p.strip()
            parts = p.rsplit(" ", 1)
            if len(parts) == 2:
                params.append({"type": parts[0], "name": parts[1]})
            elif p:
                params.append({"type": p, "name": ""})
    return params


def _build_class_methods(candidates: list[dict]) -> tuple[str, str | None]:
    """构建类的方法签名列表和构造入口示例。"""
    lines: list[str] = []
    construction: str | None = None
    for c in candidates:
        meta = c["chunk"].get("meta", {})
        content = c["chunk"].get("content", "")
        class_name = meta.get("class_name", "")
        method = meta.get("method", "")
        rt = meta.get("return_type", "")
        rt_import = meta.get("return_type_import", "")
        cp = meta.get("construction_pattern", "")

        # 从 content 提取参数部分
        params_match = re.search(r"\((.*?)\)", content)
        params_str = params_match.group(1) if params_match else ""

        ret = rt_import or rt
        line = f"{class_name}.{method}({params_str})"
        if ret:
            line += f" → {ret}"
        lines.append(line)

        # 记录构造入口
        if cp and not construction:
            simple = class_name.rsplit(".", 1)[-1] if class_name else ""
            pnames = [p.rsplit(" ", 1)[-1] for p in params_str.split(",") if p.strip()]
            pnames_str = ", ".join(pnames)
            if cp == "builder":
                construction = f"{simple}.{method}().build()"
            elif cp == "static_factory":
                construction = f"{simple}.{method}({pnames_str})"
            elif cp == "singleton":
                construction = f"{simple}.{method}()"

    return "\n".join(lines), construction


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
