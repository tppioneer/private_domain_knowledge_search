"""路由注册。"""

from fastapi import APIRouter

from .search import router as search_router
from .entities import router as entities_router
from .specs import router as specs_router
from .recommend import router as recommend_router
from .feedback import router as feedback_router
from .health import router as health_router

router = APIRouter()

router.include_router(search_router, tags=["search"])
router.include_router(entities_router, tags=["entity"])
router.include_router(specs_router, tags=["spec"])
router.include_router(recommend_router, tags=["recommend"])
router.include_router(feedback_router, tags=["feedback"])
router.include_router(health_router, tags=["health"])
