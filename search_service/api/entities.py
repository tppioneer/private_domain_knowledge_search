"""GET /api/v1/entities/{name} —— 精确实体查询。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..models.schemas import EntityDefinition, EntityDetailResponse, EntityType, VersionChange

router = APIRouter()


@router.get("/entities/{name}", response_model=EntityDetailResponse)
async def get_entity(
    name: str,
    entity_type: str | None = Query(None, description="term | api"),
    version_requirement: str | None = Query(None, description="如 >=2.1.0"),
) -> EntityDetailResponse:
    # 试点期 mock：按设计文档 GamClient.callback 场景
    if "callback" in name.lower() or "gam" in name.lower():
        return EntityDetailResponse(
            entity_name=name,
            entity_type=EntityType.API,
            definition=EntityDefinition(
                signature="public UserInfoResponse callback(String code) throws GamException",
                parameters=[{"name": "code", "type": "String", "description": "GAM回调URL中的授权码"}],
                return_type="UserInfoResponse",
                exceptions=["GamException"],
                since_version="2.0.0",
                code_example="UserInfoResponse resp = gamClient.callback(code);",
                config_requirements="需先构建 GamConfig 并注入 GamClient Bean",
            ),
            related_specs=["所有认证接口必须返回 AuthResult 对象"],
            version_changelog=[VersionChange(version="2.1.0", change="增加超时配置回调参数")],
        )
    # 术语查询
    return EntityDetailResponse(
        entity_name=name,
        entity_type=EntityType.TERM,
        definition=EntityDefinition(
            definition_text=f"{name}：私域知识中的企业术语",
            context_="适用于积分、支付等业务模块",
            synonyms=[],
            related_terms=[],
        ),
    )
