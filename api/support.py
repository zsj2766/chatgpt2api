from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from threading import Event, Thread

from fastapi import HTTPException, Request

from services.account_service import account_service
from services.auth_service import auth_service
from services.config import config
from services.log_service import LOG_TYPE_ACCOUNT, log_service
from utils.helper import anonymize_token

BASE_DIR = Path(__file__).resolve().parents[1]
WEB_DIST_DIR = BASE_DIR / "web_dist"


def extract_bearer_token(authorization: str | None) -> str:
    scheme, _, value = str(authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return ""
    return value.strip()


def _legacy_admin_identity(token: str) -> dict[str, object] | None:
    auth_key = str(config.auth_key or "").strip()
    if auth_key and token == auth_key:
        return {"id": "admin", "name": "管理员", "role": "admin"}
    return None


def require_identity(authorization: str | None) -> dict[str, object]:
    token = extract_bearer_token(authorization)
    identity = _legacy_admin_identity(token) or auth_service.authenticate(token)
    if identity is None:
        raise HTTPException(status_code=401, detail={"error": "密钥无效或已失效，请重新登录"})
    return identity


def require_auth_key(authorization: str | None) -> None:
    require_identity(authorization)


def require_admin(authorization: str | None) -> dict[str, object]:
    identity = require_identity(authorization)
    if identity.get("role") != "admin":
        raise HTTPException(status_code=403, detail={"error": "需要管理员权限才能执行这个操作"})
    return identity


def resolve_image_base_url(request: Request) -> str:
    return config.base_url or f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"


def raise_image_quota_error(exc: Exception) -> None:
    message = str(exc)
    if "no available image quota" in message.lower():
        raise HTTPException(status_code=429, detail={"error": "no available image quota"}) from exc
    raise HTTPException(status_code=502, detail={"error": message}) from exc


def sanitize_cpa_pool(pool: dict | None) -> dict | None:
    if not isinstance(pool, dict):
        return None
    return {key: value for key, value in pool.items() if key != "secret_key"}


def sanitize_cpa_pools(pools: list[dict]) -> list[dict]:
    return [sanitized for pool in pools if (sanitized := sanitize_cpa_pool(pool)) is not None]


def sanitize_sub2api_server(server: dict | None) -> dict | None:
    if not isinstance(server, dict):
        return None
    sanitized = {key: value for key, value in server.items() if key not in {"password", "api_key"}}
    sanitized["has_api_key"] = bool(str(server.get("api_key") or "").strip())
    return sanitized


def sanitize_sub2api_servers(servers: list[dict]) -> list[dict]:
    return [sanitized for server in servers if (sanitized := sanitize_sub2api_server(server)) is not None]


def start_account_watcher(stop_event: Event) -> Thread:
    """Unified watcher for account lifecycle: token refresh and rate-limit recovery."""
    before_expiry = config.token_refresh_before_expiry_seconds
    max_interval = max(
        config.token_refresh_interval_minute * 60,
        config.refresh_account_interval_minute * 60,
    )

    def _resolve_expires_at(acc: dict) -> int:
        stored = acc.get("expires_at")
        if stored is not None:
            try:
                return int(stored)
            except (TypeError, ValueError):
                pass
        return account_service._decode_jwt_exp(str(acc.get("access_token") or ""))

    def _parse_restore_at(acc: dict) -> int:
        raw = acc.get("restore_at") or ""
        if not raw:
            return 0
        try:
            # Strip trailing 'Z' for Python <3.11 compatibility
            clean = raw.rstrip("Z")
            return int(datetime.fromisoformat(clean).timestamp())
        except (ValueError, TypeError):
            return 0

    _skip_statuses = {"禁用", "异常", "过期"}

    def worker() -> None:
        rate_limit_cooldown = 0
        while not stop_event.is_set():
            try:
                accounts = account_service.list_accounts()
                now = int(time.time())
                nearest_token_event = 0
                nearest_restore_event = 0

                token_needing_refresh: list[dict] = []
                limited_needing_refresh: list[str] = []

                for acc in accounts:
                    status = acc.get("status") or ""
                    access_token = str(acc.get("access_token") or "").strip()
                    if not access_token:
                        continue

                    # ── 1. Token expiry management ──
                    if status not in _skip_statuses:
                        exp = _resolve_expires_at(acc)
                        refresh_token_str = str(acc.get("refresh_token") or "").strip()

                        if exp > 0:
                            if refresh_token_str:
                                if nearest_token_event == 0 or exp < nearest_token_event:
                                    nearest_token_event = exp
                                if exp - now <= before_expiry:
                                    token_needing_refresh.append(acc)
                            elif exp <= now:
                                account_service.update_account(access_token, {"status": "过期"}, source="token 过期(无 refresh_token)")
                                log_service.add(LOG_TYPE_ACCOUNT, "token 已过期且无 refresh_token",
                                                {"token": anonymize_token(access_token)})

                    # ── 2. Rate-limit recovery ──
                    if status == "限流":
                        restore_ts = _parse_restore_at(acc)
                        if restore_ts > 0:
                            if nearest_restore_event == 0 or restore_ts < nearest_restore_event:
                                nearest_restore_event = restore_ts
                            if restore_ts <= now:
                                limited_needing_refresh.append(access_token)
                        else:
                            # No restore_at → periodic check
                            if nearest_restore_event == 0:
                                nearest_restore_event = now + max_interval

                # ── Execute token refreshes ──
                if token_needing_refresh:
                    refreshed = 0
                    for acc in token_needing_refresh:
                        result = account_service.refresh_access_token(acc)
                        if result is not None:
                            refreshed += 1
                    if refreshed:
                        log_service.add(LOG_TYPE_ACCOUNT, "token 自动刷新完成", {"refreshed": refreshed})
                        continue

                # ── Execute rate-limit recoveries (with cooldown) ──
                if limited_needing_refresh and now >= rate_limit_cooldown:
                    result = account_service.refresh_accounts(limited_needing_refresh)
                    requested_set = set(limited_needing_refresh)
                    actually_recovered = sum(
                        1 for acc in result.get("items", [])
                        if str(acc.get("access_token") or "") in requested_set
                        and acc.get("status") != "限流"
                    )
                    log_service.add(LOG_TYPE_ACCOUNT, "限流账号自动刷新", {
                        "requested": len(requested_set),
                        "recovered": actually_recovered,
                        "errors": len(result.get("errors", [])),
                    })
                    if actually_recovered == 0:
                        rate_limit_cooldown = now + min(max_interval, 300)

                # ── Sleep until next event ──
                sleep_sec = max_interval
                if nearest_token_event and nearest_token_event > now:
                    sleep_sec = min(sleep_sec, max(60, nearest_token_event - now - before_expiry))
                if nearest_restore_event and nearest_restore_event > now:
                    sleep_sec = min(sleep_sec, max(60, nearest_restore_event - now))
                if rate_limit_cooldown > now:
                    sleep_sec = min(sleep_sec, max(60, rate_limit_cooldown - now))
                stop_event.wait(sleep_sec)

            except Exception as exc:
                log_service.add(LOG_TYPE_ACCOUNT, "账号调度异常", {"error": str(exc)})
                stop_event.wait(max_interval)

    thread = Thread(target=worker, name="account-watcher", daemon=True)
    thread.start()
    return thread


def resolve_web_asset(requested_path: str) -> Path | None:
    if not WEB_DIST_DIR.exists():
        return None
    clean_path = requested_path.strip("/")
    base_dir = WEB_DIST_DIR.resolve()
    candidates = [base_dir / "index.html"] if not clean_path else [
        base_dir / Path(clean_path),
        base_dir / clean_path / "index.html",
        base_dir / f"{clean_path}.html",
    ]
    for candidate in candidates:
        try:
            candidate.resolve().relative_to(base_dir)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None
