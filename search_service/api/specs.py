"""GET /api/v1/specs —— 获取适用规范契约。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..models.schemas import SpecItem, SpecResponse

router = APIRouter()


@router.get("/specs", response_model=SpecResponse)
async def get_specs(
    module: str | None = Query(None),
    file_path: str | None = Query(None),
    dependency_constraints: str | None = Query(None, description="JSON string of dependency constraints"),
) -> SpecResponse:
    # 试点期 mock：按设计文档积分/SSO 场景
    specs: list[SpecItem] = []
    warnings: list[str] = []

    if module == "points":
        specs = [
            SpecItem(
                id="spec_987",
                rule="积分变动方法必须记录change_log，并返回流水号",
                category="data_contract",
                effective_condition="module=points && function_prefix=Deduct",
                positive_example="changeId := logPointsChange(...)",
                negative_example="直接调用SDK后未记录流水",
            ),
        ]
        warnings = ["检测到规则 spec_988(异步积分) 与 spec_989(同步返回) 可能存在冲突，建议人工确认。"]

    return SpecResponse(specs=specs, conflict_warnings=warnings)
