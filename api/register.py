from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.support import require_admin
from services.account_service import account_service
from services.config import config
from services.register_service import register_service
from services.thread_status import thread_status


class RegisterConfigRequest(BaseModel):
    mail: dict | None = None
    proxy: str | None = None
    total: int | None = None
    threads: int | None = None
    mode: str | None = None
    target_quota: int | None = None
    target_available: int | None = None
    check_interval: int | None = None


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/register")
    async def get_register_config(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.get()}

    @router.post("/api/register")
    async def update_register_config(body: RegisterConfigRequest, authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.update(body.model_dump(exclude_none=True))}

    @router.post("/api/register/start")
    async def start_register(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.start()}

    @router.post("/api/register/stop")
    async def stop_register(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.stop()}

    @router.post("/api/register/reset")
    async def reset_register(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        return {"register": register_service.reset()}

    @router.get("/api/register/events")
    async def register_events(token: str = ""):
        require_admin(f"Bearer {token}")

        async def stream():
            last = ""
            while True:
                payload = json.dumps(register_service.get(), ensure_ascii=False)
                if payload != last:
                    last = payload
                    yield f"data: {payload}\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @router.get("/api/register/system-status")
    async def register_system_status(authorization: str | None = Header(default=None)):
        require_admin(authorization)
        accounts = account_service.get_stats()
        reg = register_service.get()
        stats = reg.get("stats") or {}
        return {
            "threads": thread_status.snapshot(),
            "accounts": {
                "total": accounts.get("total", 0),
                "normal": accounts.get("active", 0),
                "limited": accounts.get("limited", 0),
                "abnormal": accounts.get("abnormal", 0),
                "expired": accounts.get("expired", 0),
                "disabled": accounts.get("disabled", 0),
                "total_quota": accounts.get("total_quota", 0),
            },
            "register": {
                "enabled": bool(reg.get("enabled")),
                "mode": reg.get("mode", ""),
                "success": int(stats.get("success", 0)),
                "fail": int(stats.get("fail", 0)),
                "running": int(stats.get("running", 0)),
            },
            "automation": {
                "auto_remove_invalid_accounts": config.auto_remove_invalid_accounts,
                "auto_remove_rate_limited_accounts": config.auto_remove_rate_limited_accounts,
                "auto_relogin_after_refresh": config.auto_relogin_after_refresh,
                "image_retention_days": config.image_retention_days,
            },
        }

    return router
