from __future__ import annotations

import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Condition, Lock
from typing import Any
from datetime import datetime

from services.config import config
from services.log_service import (
    LOG_TYPE_ACCOUNT,
    log_service,
)
from services.storage.base import StorageBackend
from utils.helper import anonymize_token


class AccountService:
    """账号池服务，使用 token -> account 的 dict 保存账号。"""

    def __init__(self, storage_backend: StorageBackend):
        self.storage = storage_backend
        self._lock = Lock()
        self._image_slot_condition = Condition(self._lock)
        self._index = 0
        self._accounts = self._load_accounts()
        self._image_inflight: dict[str, int] = {}

    def _load_accounts(self) -> dict[str, dict]:
        accounts = self.storage.load_accounts()
        return {
            normalized["access_token"]: normalized
            for item in accounts
            if (normalized := self._normalize_account(item)) is not None
        }

    def _save_accounts(self) -> None:
        self.storage.save_accounts(list(self._accounts.values()))

    @staticmethod
    def _is_image_account_available(account: dict) -> bool:
        if not isinstance(account, dict):
            return False
        if account.get("status") in {"禁用", "限流", "异常", "过期"}:
            return False
        if bool(account.get("image_quota_unknown")):
            return True
        return int(account.get("quota") or 0) > 0

    @staticmethod
    def _decode_jwt_exp(token: str) -> int:
        try:
            payload = token.split(".")[1]
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += "=" * padding
            claims = json.loads(base64.urlsafe_b64decode(payload))
            return int(claims.get("exp") or 0)
        except Exception:
            return 0

    def _normalize_account(self, item: dict) -> dict | None:
        if not isinstance(item, dict):
            return None
        access_token = item.get("access_token") or ""
        if not access_token:
            return None
        normalized = dict(item)
        normalized["access_token"] = access_token
        normalized["type"] = normalized.get("type") or "free"
        normalized["status"] = normalized.get("status") or "正常"
        normalized["quota"] = max(0, int(normalized.get("quota") if normalized.get("quota") is not None else 0))
        normalized["image_quota_unknown"] = bool(normalized.get("image_quota_unknown"))
        normalized["email"] = normalized.get("email") or None
        normalized["user_id"] = normalized.get("user_id") or None
        limits_progress = normalized.get("limits_progress")
        normalized["limits_progress"] = limits_progress if isinstance(limits_progress, list) else []
        normalized["default_model_slug"] = normalized.get("default_model_slug") or None
        normalized["restore_at"] = normalized.get("restore_at") or None
        normalized["success"] = int(normalized.get("success") or 0)
        normalized["fail"] = int(normalized.get("fail") or 0)
        normalized["last_used_at"] = normalized.get("last_used_at")
        normalized["refresh_token"] = normalized.get("refresh_token") or ""
        normalized["password"] = normalized.get("password") or ""
        normalized["created_at"] = normalized.get("created_at") or None
        last_refreshed_at = normalized.get("last_refreshed_at")
        normalized["last_refreshed_at"] = int(last_refreshed_at) if last_refreshed_at else None
        expires_at = normalized.get("expires_at")
        if expires_at is not None:
            try:
                normalized["expires_at"] = int(expires_at)
            except (TypeError, ValueError):
                normalized["expires_at"] = None
        else:
            normalized["expires_at"] = None
        refresh_token_expires_at = normalized.get("refresh_token_expires_at")
        if refresh_token_expires_at is not None:
            try:
                normalized["refresh_token_expires_at"] = int(refresh_token_expires_at)
            except (TypeError, ValueError):
                normalized["refresh_token_expires_at"] = None
        else:
            normalized["refresh_token_expires_at"] = None
        return normalized

    def list_tokens(self) -> list[str]:
        with self._lock:
            return list(self._accounts)

    def _list_ready_candidate_tokens(self, excluded_tokens: set[str] | None = None) -> list[str]:
        excluded = set(excluded_tokens or set())
        return [
            token
            for item in self._accounts.values()
            if self._is_image_account_available(item)
               and (token := item.get("access_token") or "")
               and token not in excluded
        ]

    def _list_available_candidate_tokens(self, excluded_tokens: set[str] | None = None) -> list[str]:
        max_concurrency = max(1, int(config.image_account_concurrency or 1))
        return [
            token
            for token in self._list_ready_candidate_tokens(excluded_tokens)
            if int(self._image_inflight.get(token, 0)) < max_concurrency
        ]

    def _acquire_next_candidate_token(self, excluded_tokens: set[str] | None = None) -> str:
        with self._image_slot_condition:
            while True:
                if not self._list_ready_candidate_tokens(excluded_tokens):
                    raise RuntimeError("no available image quota")
                tokens = self._list_available_candidate_tokens(excluded_tokens)
                if tokens:
                    access_token = tokens[self._index % len(tokens)]
                    self._index += 1
                    self._image_inflight[access_token] = int(self._image_inflight.get(access_token, 0)) + 1
                    return access_token
                self._image_slot_condition.wait(timeout=1.0)

    def release_image_slot(self, access_token: str) -> None:
        if not access_token:
            return
        with self._image_slot_condition:
            current_inflight = int(self._image_inflight.get(access_token, 0))
            if current_inflight <= 1:
                self._image_inflight.pop(access_token, None)
            else:
                self._image_inflight[access_token] = current_inflight - 1
            self._image_slot_condition.notify_all()

    def get_available_access_token(self) -> str:
        attempted_tokens: set[str] = set()
        while True:
            access_token = self._acquire_next_candidate_token(excluded_tokens=attempted_tokens)
            attempted_tokens.add(access_token)
            try:
                account = self.fetch_remote_info(access_token, "get_available_access_token")
            except Exception:
                self.release_image_slot(access_token)
                continue
            if self._is_image_account_available(account or {}):
                return access_token
            self.release_image_slot(access_token)

    def get_text_access_token(self, excluded_tokens: set[str] | None = None) -> str:
        excluded = set(excluded_tokens or set())
        with self._lock:
            candidates = [
                token
                for account in self._accounts.values()
                if account.get("status") not in {"禁用", "异常", "过期"}
                   and (token := account.get("access_token") or "")
                   and token not in excluded
            ]
            if not candidates:
                return ""
            access_token = candidates[self._index % len(candidates)]
            self._index += 1
            return access_token

    def mark_text_used(self, access_token: str) -> None:
        if not access_token:
            return
        with self._lock:
            current = self._accounts.get(access_token)
            if current is None:
                return
            next_item = dict(current)
            next_item["last_used_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            account = self._normalize_account(next_item)
            if account is None:
                return
            self._accounts[access_token] = account
            self._save_accounts()

    def remove_invalid_token(self, access_token: str, event: str) -> bool:
        self.update_account(access_token, {"status": "异常", "quota": 0}, source="账号标记异常")
        log_service.add(LOG_TYPE_ACCOUNT, "账号标记异常",
                        {"source": event, "token": anonymize_token(access_token)})
        return False

    def get_account(self, access_token: str) -> dict | None:
        if not access_token:
            return None
        with self._lock:
            account = self._accounts.get(access_token)
            return dict(account) if account else None

    def list_accounts(self) -> list[dict]:
        with self._lock:
            return [dict(item) for item in self._accounts.values()]

    def list_limited_tokens(self) -> list[str]:
        with self._lock:
            return [
                token
                for item in self._accounts.values()
                if item.get("status") == "限流"
                   and (token := item.get("access_token") or "")
            ]

    def add_accounts(self, tokens: list) -> dict:
        filtered: list = []
        seen: set[str] = set()
        for token in tokens:
            if not token:
                continue
            if isinstance(token, dict):
                key = str(token.get("access_token") or "").strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                filtered.append(token)
            else:
                key = str(token or "").strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                filtered.append(key)
        if not filtered:
            return {"added": 0, "skipped": 0, "items": self.list_accounts()}

        with self._lock:
            added = 0
            skipped = 0
            for item in filtered:
                if isinstance(item, dict):
                    access_token = str(item.get("access_token") or "").strip()
                    if not access_token:
                        continue
                else:
                    access_token = str(item or "").strip()
                    item = {}
                current = self._accounts.get(access_token)
                is_new = current is None
                if is_new:
                    added += 1
                    current = {}
                    expires_at = self._decode_jwt_exp(access_token) or None
                else:
                    skipped += 1
                    expires_at = current.get("expires_at")
                account = self._normalize_account(
                    {
                        **current,
                        **({k: v for k, v in item.items() if k != "access_token"} if isinstance(item, dict) else {}),
                        "access_token": access_token,
                        "type": str(current.get("type") or item.get("type") or "free"),
                        "expires_at": expires_at,
                    }
                )
                if account is not None:
                    self._accounts[access_token] = account
            self._save_accounts()
            items = [dict(item) for item in self._accounts.values()]
            log_service.add(LOG_TYPE_ACCOUNT, f"新增 {added} 个账号，跳过 {skipped} 个",
                            {"added": added, "skipped": skipped})
        return {"added": added, "skipped": skipped, "items": items}

    def delete_accounts(self, tokens: list[str]) -> dict:
        target_set = set(token for token in tokens if token)
        if not target_set:
            return {"removed": 0, "items": self.list_accounts()}
        with self._lock:
            removed = sum(self._accounts.pop(token, None) is not None for token in target_set)
            for token in target_set:
                self._image_inflight.pop(token, None)
            if removed:
                if self._accounts:
                    self._index %= len(self._accounts)
                else:
                    self._index = 0
                self._save_accounts()
                log_service.add(LOG_TYPE_ACCOUNT, f"删除 {removed} 个账号", {"removed": removed})
            items = [dict(item) for item in self._accounts.values()]
        return {"removed": removed, "items": items}

    def update_account(self, access_token: str, updates: dict, source: str = "更新账号") -> dict | None:
        if not access_token:
            return None
        with self._lock:
            current = self._accounts.get(access_token)
            if current is None:
                return None
            account = self._normalize_account({**current, **updates, "access_token": access_token})
            if account is None:
                return None
            if account.get("status") == "限流" and config.auto_remove_rate_limited_accounts:
                self._accounts.pop(access_token, None)
                self._save_accounts()
                log_service.add(LOG_TYPE_ACCOUNT, "自动移除限流账号", {
                    "token": anonymize_token(access_token),
                    "email": account.get("email"),
                    "source": source,
                })
                return None
            changed_fields = [k for k in updates if current.get(k) != account.get(k)]
            self._accounts[access_token] = account
            self._save_accounts()
            log_service.add(LOG_TYPE_ACCOUNT, source, {
                "token": anonymize_token(access_token),
                "email": account.get("email"),
                "status": account.get("status"),
                "changed_fields": changed_fields,
            })
            return dict(account)
        return None

    def replace_token(self, old_token: str, new_token: str, updates: dict | None = None) -> dict | None:
        """Replace an account's access_token key and apply updates (used for token rotation)."""
        if not old_token or not new_token or old_token == new_token:
            if old_token and updates:
                return self.update_account(old_token, updates, source="token 替换(同 token 更新)")
            return None
        with self._lock:
            current = self._accounts.pop(old_token, None)
            self._image_inflight.pop(old_token, None)
            if current is None:
                return None
            merged = dict(current)
            merged["access_token"] = new_token
            if updates:
                merged.update(updates)
            expires_at = self._decode_jwt_exp(new_token) or None
            merged["expires_at"] = expires_at
            normalized = self._normalize_account(merged)
            if normalized is None:
                return None
            self._accounts[new_token] = normalized
            self._save_accounts()
            log_service.add(LOG_TYPE_ACCOUNT, "token 已替换",
                            {"old": anonymize_token(old_token), "new": anonymize_token(new_token),
                             "email": normalized.get("email")})
            return dict(normalized)

    def mark_image_result(self, access_token: str, success: bool) -> dict | None:
        if not access_token:
            return None
        self.release_image_slot(access_token)
        with self._lock:
            current = self._accounts.get(access_token)
            if current is None:
                return None
            next_item = dict(current)
            next_item["last_used_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            image_quota_unknown = bool(next_item.get("image_quota_unknown"))
            if success:
                next_item["success"] = int(next_item.get("success") or 0) + 1
                if not image_quota_unknown:
                    next_item["quota"] = max(0, int(next_item.get("quota") or 0) - 1)
                if not image_quota_unknown and next_item["quota"] == 0:
                    next_item["status"] = "限流"
                    next_item["restore_at"] = next_item.get("restore_at") or None
                elif next_item.get("status") == "限流":
                    next_item["status"] = "正常"
            else:
                next_item["fail"] = int(next_item.get("fail") or 0) + 1
            account = self._normalize_account(next_item)
            if account is None:
                return None
            if account.get("status") == "限流" and config.auto_remove_rate_limited_accounts:
                self._accounts.pop(access_token, None)
                self._save_accounts()
                log_service.add(LOG_TYPE_ACCOUNT, "自动移除限流账号", {"token": anonymize_token(access_token)})
                return None
            self._accounts[access_token] = account
            self._save_accounts()
            return dict(account)
        return None

    def fetch_remote_info(self, access_token: str, event: str = "fetch_remote_info") -> dict[str, Any] | None:
        if not access_token:
            raise ValueError("access_token is required")

        try:
            from services.openai_backend_api import InvalidAccessTokenError, OpenAIBackendAPI
            result = OpenAIBackendAPI(access_token).get_user_info()
        except InvalidAccessTokenError:
            self.remove_invalid_token(access_token, event)
            raise
        result["last_refreshed_at"] = time.time()
        return self.update_account(access_token, result, source=f"刷新账号信息({event})")

    def refresh_accounts(self, access_tokens: list[str]) -> dict[str, Any]:
        access_tokens = list(dict.fromkeys(token for token in access_tokens if token))
        if not access_tokens:
            return {"refreshed": 0, "errors": [], "items": self.list_accounts()}

        refreshed = 0
        errors = []
        max_workers = min(10, len(access_tokens))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.fetch_remote_info, token, "refresh_accounts"): token
                for token in access_tokens
            }
            for future in as_completed(futures):
                try:
                    account = future.result()
                except Exception as exc:
                    errors.append({"token": anonymize_token(futures[future]), "error": str(exc)})
                    continue
                if account is not None:
                    refreshed += 1

        return {
            "refreshed": refreshed,
            "errors": errors,
            "items": self.list_accounts(),
        }

    def refresh_access_token(self, account: dict) -> dict | None:
        """Use refresh_token to obtain a new access_token and update storage."""
        refresh_token = str(account.get("refresh_token") or "").strip()
        access_token = str(account.get("access_token") or "").strip()
        if not refresh_token or not access_token:
            return None

        import requests

        proxy = config.get_proxy_settings()
        session = requests.Session()
        session.verify = False
        if proxy:
            session.proxies.update({"http": proxy, "https": proxy})

        status_code = None
        data: dict = {}
        try:
            resp = session.post(
                "https://auth.openai.com/oauth/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": "app_2SKx67EdpoN0G6j4rFvigXD",
                    "redirect_uri": "https://platform.openai.com/auth/callback",
                },
                timeout=30,
            )
            status_code = resp.status_code
            try:
                body = resp.json()
                data = body if isinstance(body, dict) else {}
            except Exception:
                data = {}
        except Exception as exc:
            log_service.add(LOG_TYPE_ACCOUNT, "refresh_token 请求失败",
                            {"token": anonymize_token(access_token), "error": str(exc)})
            return None

        if status_code != 200 or not data.get("access_token"):
            if status_code in (400, 401):
                log_service.add(LOG_TYPE_ACCOUNT, "refresh_token 已失效",
                                {"token": anonymize_token(access_token), "status": status_code})
                with self._lock:
                    current = self._accounts.get(access_token)
                    if current is not None:
                        current["status"] = "过期"
                        self._save_accounts()
            return None

        new_access_token = str(data.get("access_token") or "").strip()
        new_refresh_token = str(data.get("refresh_token") or "").strip()

        with self._lock:
            current = self._accounts.get(access_token)
            if current is None:
                return None
            updated = dict(current)
            updated["access_token"] = new_access_token
            updated["refresh_token"] = new_refresh_token or refresh_token
            if current.get("status") in {"异常", "过期"}:
                updated["status"] = "正常"
            updated["last_refreshed_at"] = time.time()
            new_expires_at = self._decode_jwt_exp(new_access_token) or None
            updated["expires_at"] = new_expires_at
            rt_expires_in = data.get("refresh_token_expires_in")
            if rt_expires_in is not None:
                try:
                    updated["refresh_token_expires_at"] = int(time.time()) + int(rt_expires_in)
                except (TypeError, ValueError):
                    pass
            normalized = self._normalize_account(updated)
            if normalized is None:
                return None
            self._accounts.pop(access_token, None)
            self._image_inflight.pop(access_token, None)
            self._accounts[new_access_token] = normalized
            self._save_accounts()
            log_service.add(LOG_TYPE_ACCOUNT, "access_token 已刷新",
                            {"old": anonymize_token(access_token), "new": anonymize_token(new_access_token),
                             "email": normalized.get("email")})
            return dict(normalized)


account_service = AccountService(config.get_storage_backend())
