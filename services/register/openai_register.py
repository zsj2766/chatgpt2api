from __future__ import annotations

import base64
import hashlib
import json
import random
import secrets
import string
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests
import urllib3
from curl_cffi import requests as curl_requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from services.account_service import account_service
from services.log_service import LOG_TYPE_REGISTER, log_service
from services.register import is_socks_proxy
from services.register import mail_provider

base_dir = Path(__file__).resolve().parent
config = {
    "mail": {
        "request_timeout": 30,
        "wait_timeout": 30,
        "wait_interval": 2,
        "providers": [],
    },
    "proxy": "",
    "total": 10,
    "threads": 3,
}
register_config_file = base_dir.parents[1] / "data" / "register.json"
try:
    saved_config = json.loads(register_config_file.read_text(encoding="utf-8"))
    config.update({key: saved_config[key] for key in ("mail", "proxy", "total", "threads") if key in saved_config})
except Exception:
    pass

auth_base = "https://auth.openai.com"
platform_base = "https://platform.openai.com"
platform_oauth_client_id = "app_2SKx67EdpoN0G6j64rFvigXD"
platform_oauth_redirect_uri = f"{platform_base}/auth/callback"
platform_oauth_audience = "https://api.openai.com/v1"
platform_auth0_client = "eyJuYW1lIjoiYXV0aDAtc3BhLWpzIiwidmVyc2lvbiI6IjEuMjEuMCJ9"
user_agent = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)
sec_ch_ua = '"Google Chrome";v="145", "Not?A_Brand";v="8", "Chromium";v="145"'
sec_ch_ua_full_version_list = '"Chromium";v="145.0.0.0", "Not:A-Brand";v="99.0.0.0", "Google Chrome";v="145.0.0.0"'
default_timeout = 30
print_lock = threading.Lock()
stats_lock = threading.Lock()
stats = {"done": 0, "success": 0, "fail": 0, "start_time": 0.0}
register_log_sink = None

common_headers = {
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "origin": auth_base,
    "priority": "u=1, i",
    "user-agent": user_agent,
    "sec-ch-ua": sec_ch_ua,
    "sec-ch-ua-arch": '"x86_64"',
    "sec-ch-ua-bitness": '"64"',
    "sec-ch-ua-full-version-list": sec_ch_ua_full_version_list,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-model": '""',
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua-platform-version": '"10.0.0"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

navigate_headers = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": user_agent,
    "sec-ch-ua": sec_ch_ua,
    "sec-ch-ua-arch": '"x86_64"',
    "sec-ch-ua-bitness": '"64"',
    "sec-ch-ua-full-version-list": sec_ch_ua_full_version_list,
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-model": '""',
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua-platform-version": '"10.0.0"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}


def log(text: str, color: str = "") -> None:
    colors = {"red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m"}
    if register_log_sink:
        try:
            register_log_sink(text, color)
        except Exception:
            pass
    with print_lock:
        prefix = colors.get(color, "")
        suffix = "\033[0m" if prefix else ""
        print(f"{prefix}{datetime.now().strftime('%H:%M:%S')} {text}{suffix}")


def step(index: int, text: str, color: str = "") -> None:
    log(f"[任务{index}] {text}", color)


def _make_trace_headers() -> dict[str, str]:
    trace_id = str(random.getrandbits(64))
    parent_id = str(random.getrandbits(64))
    return {
        "traceparent": f"00-{uuid.uuid4().hex}-{format(int(parent_id), '016x')}-01",
        "tracestate": "dd=s:1;o:rum",
        "x-datadog-origin": "rum",
        "x-datadog-parent-id": parent_id,
        "x-datadog-sampling-priority": "1",
        "x-datadog-trace-id": trace_id,
    }


def _generate_pkce() -> tuple[str, str]:
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def _random_password(length: int = 16) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%"
    value = list(
        secrets.choice(string.ascii_uppercase)
        + secrets.choice(string.ascii_lowercase)
        + secrets.choice(string.digits)
        + secrets.choice("!@#$%")
        + "".join(secrets.choice(chars) for _ in range(max(0, length - 4)))
    )
    random.shuffle(value)
    return "".join(value)


def _random_name() -> tuple[str, str]:
    return random.choice(["James", "Robert", "John", "Michael", "David", "Mary", "Emma", "Olivia"]), random.choice(
        ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller"]
    )


def _random_birthdate() -> str:
    return f"{random.randint(1996, 2006):04d}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"


def _response_json(resp) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _response_debug_detail(resp, limit: int = 800) -> str:
    if resp is None:
        return ""
    data = _response_json(resp)
    parts = [
        f"url={str(getattr(resp, 'url', '') or '')[:300]}",
        f"content_type={str(getattr(resp, 'headers', {}).get('content-type') or '')}",
    ]
    for key in ("cf-ray", "x-request-id", "openai-processing-ms"):
        value = str(getattr(resp, "headers", {}).get(key) or "").strip()
        if value:
            parts.append(f"{key}={value}")
    if data:
        parts.append(f"json={json.dumps(data, ensure_ascii=False)[:limit]}")
    else:
        parts.append(f"body={str(getattr(resp, 'text', '') or '')[:limit]}")
    return ", ".join(parts)


def _is_cloudflare_challenge(resp) -> bool:
    if resp is None:
        return False
    text = str(getattr(resp, "text", "") or "").lower()
    headers = getattr(resp, "headers", {}) or {}
    server = str(headers.get("server") or "").lower()
    return (
        "cloudflare" in server
        or "challenges.cloudflare.com" in text
        or "<title>just a moment" in text
    )


def create_mailbox(username: str | None = None) -> dict:
    return mail_provider.create_mailbox(config["mail"], username)


def wait_for_code(mailbox: dict) -> str | None:
    return mail_provider.wait_for_code(config["mail"], mailbox)


class SentinelTokenGenerator:
    MAX_ATTEMPTS = 500000
    ERROR_PREFIX = "wQ8Lk5FbGpA2NcR9dShT6gYjU7VxZ4D"

    def __init__(self, device_id: str, ua: str):
        self.device_id = device_id
        self.user_agent = ua
        self.sid = str(uuid.uuid4())

    @staticmethod
    def _fnv1a_32(text: str) -> str:
        h = 2166136261
        for ch in text:
            h ^= ord(ch)
            h = (h * 16777619) & 0xFFFFFFFF
        h ^= h >> 16
        h = (h * 2246822507) & 0xFFFFFFFF
        h ^= h >> 13
        h = (h * 3266489909) & 0xFFFFFFFF
        h ^= h >> 16
        return format(h & 0xFFFFFFFF, "08x")

    def _get_config(self) -> list:
        perf_now = random.uniform(1000, 50000)
        return [
            "1920x1080",
            time.strftime("%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)", time.gmtime()),
            4294705152,
            random.random(),
            self.user_agent,
            "https://sentinel.openai.com/sentinel/20260124ceb8/sdk.js",
            None,
            None,
            "en-US",
            random.random(),
            random.choice(["vendorSub-undefined", "plugins-undefined", "mimeTypes-undefined", "hardwareConcurrency-undefined"]),
            random.choice(["location", "implementation", "URL", "documentURI", "compatMode"]),
            random.choice(["Object", "Function", "Array", "Number", "parseFloat", "undefined"]),
            perf_now,
            self.sid,
            "",
            random.choice([4, 8, 12, 16]),
            time.time() * 1000 - perf_now,
        ]

    @staticmethod
    def _b64(data) -> str:
        return base64.b64encode(json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).decode("ascii")

    def generate_requirements_token(self) -> str:
        data = self._get_config()
        data[3] = 1
        data[9] = round(random.uniform(5, 50))
        return "gAAAAAC" + self._b64(data)

    def generate_token(self, seed: str, difficulty: str) -> str:
        start = time.time()
        data = self._get_config()
        difficulty = str(difficulty or "0")
        for i in range(self.MAX_ATTEMPTS):
            data[3] = i
            data[9] = round((time.time() - start) * 1000)
            payload = self._b64(data)
            if self._fnv1a_32(seed + payload)[: len(difficulty)] <= difficulty:
                return "gAAAAAB" + payload + "~S"
        return "gAAAAAB" + self.ERROR_PREFIX + self._b64(str(None))


def build_sentinel_token(session: requests.Session, device_id: str, flow: str) -> str:
    generator = SentinelTokenGenerator(device_id, user_agent)
    resp = session.post(
        "https://sentinel.openai.com/backend-api/sentinel/req",
        data=json.dumps({"p": generator.generate_requirements_token(), "id": device_id, "flow": flow}),
        headers={
            "Content-Type": "text/plain;charset=UTF-8",
            "Referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html",
            "Origin": "https://sentinel.openai.com",
            "User-Agent": user_agent,
            "sec-ch-ua": sec_ch_ua,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        },
        timeout=20,
        verify=False,
    )
    data = _response_json(resp)
    token = str(data.get("token") or "").strip()
    if resp.status_code != 200 or not token:
        raise RuntimeError(f"sentinel_req_failed_{resp.status_code}")
    pow_data = data.get("proofofwork") or {}
    p_value = (
        generator.generate_token(str(pow_data.get("seed") or ""), str(pow_data.get("difficulty") or "0"))
        if pow_data.get("required") and pow_data.get("seed")
        else generator.generate_requirements_token()
    )
    return json.dumps({"p": p_value, "t": "", "c": token, "id": device_id, "flow": flow}, separators=(",", ":"))


def create_session(proxy: str = "") -> Any:
    if is_socks_proxy(proxy):
        return curl_requests.Session(impersonate="chrome136", verify=False, proxy=proxy)
    session = requests.Session()
    retry = Retry(total=2, connect=2, read=2, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503, 504))
    adapter = HTTPAdapter(max_retries=retry, pool_connections=50, pool_maxsize=50)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.verify = False
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


def request_with_local_retry(session: requests.Session, method: str, url: str, retry_attempts: int = 3, **kwargs):
    last_error = ""
    for _ in range(max(1, retry_attempts)):
        try:
            return session.request(method.upper(), url, timeout=default_timeout, **kwargs), ""
        except Exception as error:
            last_error = str(error)
            time.sleep(1)
    return None, last_error


def validate_otp(session: requests.Session, device_id: str, code: str):
    headers = dict(common_headers)
    headers["referer"] = f"{auth_base}/email-verification"
    headers["oai-device-id"] = device_id
    headers.update(_make_trace_headers())
    resp, error = request_with_local_retry(session, "post", f"{auth_base}/api/accounts/email-otp/validate", json={"code": code}, headers=headers, verify=False)
    if resp is not None and resp.status_code == 200:
        return resp, ""
    headers["openai-sentinel-token"] = build_sentinel_token(session, device_id, "authorize_continue")
    resp, error = request_with_local_retry(session, "post", f"{auth_base}/api/accounts/email-otp/validate", json={"code": code}, headers=headers, verify=False)
    return resp, error


def extract_oauth_callback_params_from_url(url: str) -> dict[str, str] | None:
    if not url:
        return None
    try:
        params = parse_qs(urlparse(url).query)
    except Exception:
        return None
    code = str((params.get("code") or [""])[0]).strip()
    if not code:
        return None
    return {"code": code, "state": str((params.get("state") or [""])[0]).strip(), "scope": str((params.get("scope") or [""])[0]).strip()}


def _exchange_oauth_token(code: str, code_verifier: str) -> dict | None:
    """Exchange OAuth authorization code for tokens via /oauth/token (form-encoded)."""
    session = create_session(config["proxy"])
    try:
        resp = session.post(
            f"{auth_base}/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": platform_oauth_redirect_uri,
                "client_id": platform_oauth_client_id,
                "code_verifier": code_verifier,
            },
            verify=False,
            timeout=60,
        )
        data = _response_json(resp)
        if resp.status_code != 200 or not data.get("access_token") or not data.get("refresh_token") or not data.get("id_token"):
            log(f"[exchange] /oauth/token 失败: status={resp.status_code}, body={json.dumps(data, ensure_ascii=False)[:300]}", "red")
            log_service.add(LOG_TYPE_REGISTER, "token 交换失败", {"status": resp.status_code, "body": json.dumps(data, ensure_ascii=False)[:300]})
            return None
        payload = account_service._decode_jwt_payload(str(data.get("id_token") or "")) or account_service._decode_jwt_payload(str(data.get("access_token") or ""))
        refresh_token_expires_at = None
        rt_expires_in = data.get("refresh_token_expires_in")
        if rt_expires_in is not None:
            try:
                refresh_token_expires_at = int(time.time()) + int(rt_expires_in)
            except (TypeError, ValueError):
                pass
        if refresh_token_expires_at is None:
            rt_payload = account_service._decode_jwt_payload(str(data.get("refresh_token") or ""))
            rt_exp = rt_payload.get("exp")
            if rt_exp:
                try:
                    refresh_token_expires_at = int(rt_exp)
                except (TypeError, ValueError):
                    pass
        if refresh_token_expires_at is None:
            refresh_token_expires_at = int(time.time()) + 86400 * 30
        email = str(payload.get("email") or "").strip()
        log(f"[exchange] token 交换成功: {email}, rt_expires_in={rt_expires_in}", "green")
        log_service.add(LOG_TYPE_REGISTER, "token 交换成功", {"email": email, "rt_expires_in": rt_expires_in})
        return {
            "email": email,
            "access_token": str(data.get("access_token") or "").strip(),
            "refresh_token": str(data.get("refresh_token") or "").strip(),
            "id_token": str(data.get("id_token") or "").strip(),
            "refresh_token_expires_at": refresh_token_expires_at,
        }
    finally:
        session.close()


class PlatformRegistrar:
    def __init__(self, proxy: str = "") -> None:
        self.session = create_session(proxy)
        self.device_id = str(uuid.uuid4())
        self.code_verifier = ""
        self.platform_auth_code = ""

    def close(self) -> None:
        self.session.close()

    def _navigate_headers(self, referer: str = "") -> dict[str, str]:
        headers = dict(navigate_headers)
        if referer:
            headers["referer"] = referer
        return headers

    def _json_headers(self, referer: str) -> dict[str, str]:
        headers = dict(common_headers)
        headers["referer"] = referer
        headers["oai-device-id"] = self.device_id
        headers.update(_make_trace_headers())
        return headers

    def _platform_authorize(self, email: str, index: int) -> None:
        step(index, "开始 platform authorize")
        self.session.cookies.set("oai-did", self.device_id, domain=".auth.openai.com")
        self.session.cookies.set("oai-did", self.device_id, domain="auth.openai.com")
        self.code_verifier, code_challenge = _generate_pkce()
        params = {
            "issuer": auth_base,
            "client_id": platform_oauth_client_id,
            "audience": platform_oauth_audience,
            "redirect_uri": platform_oauth_redirect_uri,
            "device_id": self.device_id,
            "screen_hint": "login_or_signup",
            "max_age": "0",
            "login_hint": email,
            "scope": "openid profile email offline_access",
            "response_type": "code",
            "response_mode": "query",
            "state": secrets.token_urlsafe(32),
            "nonce": secrets.token_urlsafe(32),
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "auth0Client": platform_auth0_client,
        }
        resp, error = request_with_local_retry(self.session, "get", f"{auth_base}/api/accounts/authorize?{urlencode(params)}", headers=self._navigate_headers(f"{platform_base}/"), allow_redirects=True, verify=False)
        if resp is None or resp.status_code != 200:
            err = _response_json(resp).get("error", {}) if resp is not None else {}
            detail = f": {err.get('code', '')} - {err.get('message', '')}".strip(" -") if err else ""
            if _is_cloudflare_challenge(resp):
                raise RuntimeError("被 Cloudflare 拦截，请更换 IP 重试")
            debug = _response_debug_detail(resp)
            status = getattr(resp, "status_code", "unknown")
            raise RuntimeError(error or f"platform_authorize_http_{status}{detail}, {debug}")
        step(index, "platform authorize 完成")

    def _register_user(self, email: str, password: str, index: int) -> None:
        step(index, "开始提交注册密码")
        headers = self._json_headers(f"{auth_base}/create-account/password")
        headers["openai-sentinel-token"] = build_sentinel_token(self.session, self.device_id, "username_password_create")
        resp, error = request_with_local_retry(self.session, "post", f"{auth_base}/api/accounts/user/register", json={"username": email, "password": password}, headers=headers, verify=False)
        if resp is None or resp.status_code != 200:
            data = _response_json(resp) if resp is not None else {}
            if data.get("message") == "Failed to create account. Please try again.":
                step(index, "注册失败提示: 邮箱域名很可能因滥用被封禁，请更换邮箱域名", "yellow")
            detail = f", detail={json.dumps(data, ensure_ascii=False)}" if data else ""
            raise RuntimeError(error or f"user_register_http_{getattr(resp, 'status_code', 'unknown')}{detail}")
        step(index, "提交注册密码完成")

    def _send_otp(self, index: int) -> None:
        step(index, "开始发送验证码")
        resp, error = request_with_local_retry(self.session, "get", f"{auth_base}/api/accounts/email-otp/send", headers=self._navigate_headers(f"{auth_base}/create-account/password"), allow_redirects=True, verify=False)
        if resp is None or resp.status_code not in (200, 302):
            raise RuntimeError(error or f"send_otp_http_{getattr(resp, 'status_code', 'unknown')}")
        step(index, "发送验证码完成")

    def _validate_otp(self, code: str, index: int) -> None:
        step(index, f"开始校验验证码 {code}")
        resp, error = validate_otp(self.session, self.device_id, code)
        if resp is None or resp.status_code != 200:
            body = ""
            try:
                body = (resp.text or "")[:500] if resp is not None else ""
            except Exception:
                pass
            raise RuntimeError(error or f"validate_otp_http_{getattr(resp, 'status_code', 'unknown')}_body={body}")
        step(index, "验证码校验完成")

    def _create_account(self, name: str, birthdate: str, index: int) -> None:
        step(index, "开始创建账号资料")
        headers = self._json_headers(f"{auth_base}/about-you")
        headers["openai-sentinel-token"] = build_sentinel_token(self.session, self.device_id, "oauth_create_account")
        resp, error = request_with_local_retry(self.session, "post", f"{auth_base}/api/accounts/create_account", json={"name": name, "birthdate": birthdate}, headers=headers, verify=False)
        if resp is None or resp.status_code not in (200, 302):
            data = _response_json(resp) if resp is not None else {}
            if data.get("message") == "Failed to create account. Please try again.":
                step(index, "创建账号失败提示: 邮箱域名很可能因滥用被封禁，请更换邮箱域名", "yellow")
            detail = f", detail={json.dumps(data, ensure_ascii=False)}" if data else ""
            raise RuntimeError(error or f"create_account_http_{getattr(resp, 'status_code', 'unknown')}{detail}")
        data = _response_json(resp)
        callback_params = extract_oauth_callback_params_from_url(str(data.get("continue_url") or "").strip())
        self.platform_auth_code = str((callback_params or {}).get("code") or "").strip()
        step(index, "创建账号资料完成")

    def _exchange_registered_tokens(self, index: int) -> dict:
        step(index, "开始换 token")
        if not self.platform_auth_code:
            raise RuntimeError("create_account 未返回 auth code")
        tokens = _exchange_oauth_token(self.platform_auth_code, self.code_verifier)
        if not tokens:
            raise RuntimeError("token换取失败")
        step(index, "token 换取完成")
        return tokens

    def register(self, index: int) -> dict:
        step(index, "开始创建邮箱")
        mailbox = create_mailbox()
        email = str(mailbox.get("address") or "").strip()
        if not email:
            raise RuntimeError("邮箱服务未返回 address")
        label = str(mailbox.get("label") or "")
        step(index, f"邮箱创建完成[{label}]: {email}")
        password = _random_password()
        first_name, last_name = _random_name()
        self._platform_authorize(email, index)
        self._register_user(email, password, index)
        self._send_otp(index)
        step(index, "开始等待注册验证码")
        code = wait_for_code(mailbox)
        if not code:
            raise RuntimeError("等待注册验证码超时")
        step(index, f"收到注册验证码: {code}")
        self._validate_otp(code, index)
        self._create_account(f"{first_name} {last_name}", _random_birthdate(), index)
        tokens = self._exchange_registered_tokens(index)
        return {
            "email": email,
            "password": password,
            "access_token": str(tokens.get("access_token") or "").strip(),
            "refresh_token": str(tokens.get("refresh_token") or "").strip(),
            "id_token": str(tokens.get("id_token") or "").strip(),
            "source_type": "web",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "refresh_token_expires_at": tokens.get("refresh_token_expires_at"),
        }


def worker(index: int) -> dict:
    start = time.time()
    registrar = PlatformRegistrar(config["proxy"])
    try:
        step(index, "任务启动")
        result = registrar.register(index)
        cost = time.time() - start
        access_token = str(result["access_token"])
        account_service.add_account_items([result])
        refresh_result = account_service.refresh_accounts([access_token])
        if refresh_result.get("errors"):
            step(index, f"账号已保存，刷新状态暂未成功，稍后可重试: {refresh_result['errors']}", "yellow")
        with stats_lock:
            stats["done"] += 1
            stats["success"] += 1
            avg = (time.time() - stats["start_time"]) / stats["success"]
        log(f'{result["email"]} 注册成功，本次耗时{cost:.1f}s，全局平均每个号注册耗时{avg:.1f}s', "green")
        log_service.add(LOG_TYPE_REGISTER, "注册成功", {"email": result["email"], "cost_seconds": round(cost, 1), "avg_seconds": round(avg, 1)})
        return {"ok": True, "index": index, "result": result}
    except Exception as e:
        cost = time.time() - start
        with stats_lock:
            stats["done"] += 1
            stats["fail"] += 1
        log(f"任务{index} 注册失败，本次耗时{cost:.1f}s，原因: {e}", "red")
        log_service.add(LOG_TYPE_REGISTER, "注册失败", {"index": index, "cost_seconds": round(cost, 1), "error": str(e)[:300]})
        return {"ok": False, "index": index, "error": str(e)}
    finally:
        registrar.close()


# ── Relogin (standalone login for existing accounts) ──

def extract_oauth_callback_params_from_consent_session(
        session: requests.Session, consent_url: str, device_id: str,
) -> dict[str, str] | None:
    if consent_url.startswith("/"):
        consent_url = f"{auth_base}{consent_url}"
    direct_params = extract_oauth_callback_params_from_url(consent_url)
    if direct_params:
        return direct_params
    current_url = consent_url
    redirect_chain: list[str] = []
    for _ in range(10):
        resp = session.get(current_url, headers=navigate_headers, verify=False, timeout=30, allow_redirects=False)
        redirect_chain.append(f"{current_url[:80]} → {resp.status_code}")
        for source in (str(resp.url), str(resp.headers.get("Location") or "")):
            callback_params = extract_oauth_callback_params_from_url(source.strip())
            if callback_params:
                return callback_params
        location = str(resp.headers.get("Location") or "").strip()
        if resp.status_code not in (301, 302, 303, 307, 308) or not location:
            break
        current_url = f"{auth_base}{location}" if location.startswith("/") else location
    raw = session.cookies.get("oai-client-auth-session", domain=".auth.openai.com") or session.cookies.get("oai-client-auth-session")
    if not raw:
        log(f"[relogin] OAuth 回调提取失败：缺少 oai-client-auth-session cookie, chain={redirect_chain}", "yellow")
        return None
    try:
        first_part = raw.split(".")[0]
        padding = 4 - len(first_part) % 4
        if padding != 4:
            first_part += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(first_part))
        workspace_id = payload["workspaces"][0]["id"]
    except Exception as exc:
        log(f"[relogin] OAuth 回调提取失败：cookie JWT 解析失败: {exc}", "yellow")
        return None
    headers = dict(common_headers)
    headers["referer"] = consent_url
    headers["oai-device-id"] = device_id
    headers.update(_make_trace_headers())
    ws_resp = session.post(f"{auth_base}/api/accounts/workspace/select", json={"workspace_id": workspace_id}, headers=headers, verify=False, timeout=30, allow_redirects=False)
    callback_params = extract_oauth_callback_params_from_url(str(ws_resp.headers.get("Location") or "").strip())
    if callback_params:
        return callback_params
    ws_data = _response_json(ws_resp)
    orgs = ((ws_data.get("data") or {}).get("orgs") or []) if isinstance(ws_data, dict) else []
    if not orgs:
        log(f"[relogin] OAuth 回调提取失败：workspace/select 返回无 orgs, status={ws_resp.status_code}", "yellow")
        return None
    org_id = str((orgs[0] or {}).get("id") or "").strip()
    project_id = str(((orgs[0] or {}).get("projects") or [{}])[0].get("id") or "").strip()
    if not org_id:
        log(f"[relogin] OAuth 回调提取失败：orgs 数据缺少 id", "yellow")
        return None
    org_headers = dict(common_headers)
    org_headers["referer"] = str(ws_data.get("continue_url") or consent_url)
    org_headers["oai-device-id"] = device_id
    org_headers.update(_make_trace_headers())
    body = {"org_id": org_id}
    if project_id:
        body["project_id"] = project_id
    org_resp = session.post(f"{auth_base}/api/accounts/organization/select", json=body, headers=org_headers, verify=False, timeout=30, allow_redirects=False)
    result = extract_oauth_callback_params_from_url(str(org_resp.headers.get("Location") or "").strip())
    if not result:
        log(f"[relogin] OAuth 回调提取失败：organization/select 返回无 callback, status={org_resp.status_code}", "yellow")
    return result


def _exchange_relogin_tokens(session: requests.Session, device_id: str, code_verifier: str, consent_url: str) -> dict | None:
    direct_params = extract_oauth_callback_params_from_url(consent_url)
    code = str((direct_params or {}).get("code") or "").strip() if direct_params else ""
    if not code:
        callback_params = extract_oauth_callback_params_from_consent_session(session, consent_url, device_id)
        if not callback_params:
            log(f"[relogin] 重登 token 换取失败：无法提取 OAuth 回调参数, consent_url={consent_url[:120]}", "red")
            log_service.add(LOG_TYPE_REGISTER, "重登 token 换取失败", {"reason": "无法提取 OAuth 回调参数", "consent_url": consent_url[:120]})
            return None
        code = str(callback_params.get("code") or "").strip()
    if not code:
        log("[relogin] 重登 token 换取失败：回调参数中缺少 code", "red")
        log_service.add(LOG_TYPE_REGISTER, "重登 token 换取失败", {"reason": "回调参数中缺少 code"})
        return None
    tokens = _exchange_oauth_token(code, code_verifier)
    if not tokens or not tokens.get("access_token"):
        log("[relogin] 重登 token 换取失败：/oauth/token 返回异常", "red")
        log_service.add(LOG_TYPE_REGISTER, "重登 token 换取失败", {"reason": "/oauth/token 返回异常"})
        return None
    return tokens


_relogin_sessions: dict[str, dict] = {}
_relogin_lock = threading.Lock()


def _cleanup_expired_sessions() -> None:
    now = time.time()
    with _relogin_lock:
        expired = [k for k, v in _relogin_sessions.items() if v.get("expires_at", 0) < now]
        for k in expired:
            s = _relogin_sessions.pop(k, None)
            if s:
                try:
                    s["session"].close()
                except Exception:
                    pass


def relogin_and_get_tokens(proxy: str, email: str, password: str) -> dict:
    log(f"[relogin] 重登开始（密码模式）: {email}")
    log_service.add(LOG_TYPE_REGISTER, "重登开始（密码模式）", {"email": email})
    session = create_session(proxy)
    device_id = str(uuid.uuid4())
    code_verifier, code_challenge = _generate_pkce()
    session.cookies.set("oai-did", device_id, domain=".auth.openai.com")
    session.cookies.set("oai-did", device_id, domain="auth.openai.com")
    _keep_session = False
    try:
        params = {"issuer": auth_base, "client_id": platform_oauth_client_id, "audience": platform_oauth_audience, "redirect_uri": platform_oauth_redirect_uri, "device_id": device_id, "screen_hint": "login_or_signup", "max_age": "0", "login_hint": email, "scope": "openid profile email offline_access", "response_type": "code", "response_mode": "query", "state": secrets.token_urlsafe(32), "nonce": secrets.token_urlsafe(32), "code_challenge": code_challenge, "code_challenge_method": "S256", "auth0Client": platform_auth0_client}
        nav_h = dict(navigate_headers)
        nav_h["referer"] = f"{platform_base}/"
        resp, error = request_with_local_retry(session, "get", f"{auth_base}/api/accounts/authorize?{urlencode(params)}", headers=nav_h, allow_redirects=False, verify=False)
        if resp is None:
            raise RuntimeError(error or "relogin_authorize_failed")
        if resp.status_code in (302, 303, 307, 308):
            location = str(resp.headers.get("Location") or "").strip()
            if location:
                login_url = f"{auth_base}{location}" if location.startswith("/") else location
                request_with_local_retry(session, "get", login_url, headers=dict(navigate_headers), allow_redirects=False, verify=False)
        jh = dict(common_headers)
        jh["referer"] = f"{auth_base}/log-in/password"
        jh["oai-device-id"] = device_id
        jh.update(_make_trace_headers())
        jh["openai-sentinel-token"] = build_sentinel_token(session, device_id, "password_verify")
        resp, error = request_with_local_retry(session, "post", f"{auth_base}/api/accounts/password/verify", json={"password": password}, headers=jh, allow_redirects=False, verify=False)
        if resp is None or resp.status_code != 200:
            body = resp.text[:500] if resp is not None else ""
            log_service.add(LOG_TYPE_REGISTER, "重登密码验证失败", {"email": email, "status": getattr(resp, "status_code", "none"), "body": body[:300]})
            raise RuntimeError(error or f"password_verify_http_{getattr(resp, 'status_code', 'unknown')}{' body:' + body if body else ''}")
        payload = _response_json(resp)
        continue_url = str(payload.get("continue_url") or "").strip()
        page_type = str(((payload.get("page") or {}).get("type")) or "")
        if page_type == "email_otp_verification" or "email-verification" in continue_url or "email-otp" in continue_url:
            otp_send_h = dict(navigate_headers)
            otp_send_h["referer"] = f"{auth_base}/log-in/password"
            otp_send_h["oai-device-id"] = device_id
            otp_send_h.update(_make_trace_headers())
            send_resp, send_err = request_with_local_retry(session, "get", f"{auth_base}/api/accounts/email-otp/send", headers=otp_send_h, allow_redirects=True, verify=False)
            send_status = getattr(send_resp, "status_code", None)
            if send_resp is None or send_status not in (200, 302):
                raise RuntimeError(f"send_otp_http_{send_status}: {send_err}")
            _cleanup_expired_sessions()
            session_id = secrets.token_urlsafe(16)
            with _relogin_lock:
                _relogin_sessions[session_id] = {"session": session, "device_id": device_id, "code_verifier": code_verifier, "expires_at": time.time() + 300}
            _keep_session = True
            return {"otp_required": True, "session_id": session_id}
        if not continue_url:
            continue_url = f"{auth_base}/sign-in-with-chatgpt/codex/consent"
        tokens = _exchange_relogin_tokens(session, device_id, code_verifier, continue_url)
        if not tokens:
            raise RuntimeError("relogin_exchange_failed")
        log(f"[relogin] 重登成功（密码模式）: {email}", "green")
        log_service.add(LOG_TYPE_REGISTER, "重登成功（密码模式）", {"email": email})
        return tokens
    finally:
        if not _keep_session:
            session.close()


def relogin_send_otp(proxy: str, email: str) -> str:
    log(f"[relogin] 重登 OTP 发送开始: {email}")
    log_service.add(LOG_TYPE_REGISTER, "重登 OTP 发送开始", {"email": email})
    _cleanup_expired_sessions()
    session = create_session(proxy)
    device_id = str(uuid.uuid4())
    code_verifier, code_challenge = _generate_pkce()
    session.cookies.set("oai-did", device_id, domain=".auth.openai.com")
    session.cookies.set("oai-did", device_id, domain="auth.openai.com")
    try:
        params = {"issuer": auth_base, "client_id": platform_oauth_client_id, "audience": platform_oauth_audience, "redirect_uri": platform_oauth_redirect_uri, "device_id": device_id, "screen_hint": "login_or_signup", "max_age": "0", "login_hint": email, "scope": "openid profile email offline_access", "response_type": "code", "response_mode": "query", "state": secrets.token_urlsafe(32), "nonce": secrets.token_urlsafe(32), "code_challenge": code_challenge, "code_challenge_method": "S256", "auth0Client": platform_auth0_client}
        nav_h = dict(navigate_headers)
        nav_h["referer"] = f"{platform_base}/"
        resp, error = request_with_local_retry(session, "get", f"{auth_base}/api/accounts/authorize?{urlencode(params)}", headers=nav_h, allow_redirects=False, verify=False)
        if resp is None:
            raise RuntimeError(error or "relogin_authorize_failed")
        if resp.status_code in (302, 303, 307, 308):
            location = str(resp.headers.get("Location") or "").strip()
            if location:
                login_url = f"{auth_base}{location}" if location.startswith("/") else location
                request_with_local_retry(session, "get", login_url, headers=dict(navigate_headers), allow_redirects=False, verify=False)
        otp_h = dict(navigate_headers)
        otp_h["referer"] = f"{auth_base}/log-in/password"
        otp_h["oai-device-id"] = device_id
        otp_h.update(_make_trace_headers())
        resp, error = request_with_local_retry(session, "get", f"{auth_base}/api/accounts/email-otp/send", headers=otp_h, allow_redirects=True, verify=False)
        if resp is None or resp.status_code not in (200, 302):
            raise RuntimeError(error or f"send_otp_http_{getattr(resp, 'status_code', 'unknown')}")
        session_id = secrets.token_urlsafe(16)
        with _relogin_lock:
            _relogin_sessions[session_id] = {"session": session, "device_id": device_id, "code_verifier": code_verifier, "expires_at": time.time() + 300}
        log(f"[relogin] 重登 OTP 已发送: {email}", "green")
        log_service.add(LOG_TYPE_REGISTER, "重登 OTP 已发送", {"email": email})
        return session_id
    except Exception as exc:
        session.close()
        log(f"[relogin] 重登 OTP 发送失败: {email}, error={exc}", "red")
        log_service.add(LOG_TYPE_REGISTER, "重登 OTP 发送失败", {"email": email, "error": str(exc)})
        raise


def relogin_submit_otp(session_id: str, code: str) -> dict:
    _cleanup_expired_sessions()
    with _relogin_lock:
        state = _relogin_sessions.pop(session_id, None)
    if state is None:
        raise RuntimeError("验证码会话已过期，请重新尝试")
    session = state["session"]
    device_id = state["device_id"]
    code_verifier = state["code_verifier"]
    try:
        resp, error = validate_otp(session, device_id, code)
        if resp is None or resp.status_code != 200:
            log(f"[relogin] OTP 验证失败: status={getattr(resp, 'status_code', 'none')}, error={error}", "red")
            log_service.add(LOG_TYPE_REGISTER, "重登 OTP 验证失败", {"status": getattr(resp, "status_code", "none"), "error": str(error or "")[:300]})
            raise RuntimeError(error or f"validate_otp_http_{getattr(resp, 'status_code', 'unknown')}")
        body = _response_json(resp)
        continue_url = str(body.get("continue_url") or "").strip()
        if not continue_url:
            continue_url = f"{auth_base}/sign-in-with-chatgpt/codex/consent"
        log(f"[relogin] OTP 验证通过，开始换取 token, continue_url={continue_url[:120]}", "yellow")
        log_service.add(LOG_TYPE_REGISTER, "重登 OTP 验证通过", {"continue_url": continue_url[:120]})
        tokens = _exchange_relogin_tokens(session, device_id, code_verifier, continue_url)
        if not tokens:
            raise RuntimeError("token换取失败")
        log(f"[relogin] 重登成功（OTP 模式）: {tokens.get('email', '')}", "green")
        log_service.add(LOG_TYPE_REGISTER, "重登成功（OTP 模式）", {"email": str(tokens.get("email") or "")[:100]})
        return tokens
    finally:
        session.close()
