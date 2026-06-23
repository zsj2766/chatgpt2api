"""测试账号服务的错误分类功能"""
import tempfile
from pathlib import Path
import pytest
from services.account_service import AccountService
from services.storage.json_storage import JSONStorageBackend


class TestErrorClassification:
    """测试网络错误和账号错误的区分逻辑"""

    @classmethod
    def setup_class(cls):
        # 使用临时文件避免影响真实数据
        cls.temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        cls.temp_file.close()
        storage = JSONStorageBackend(Path(cls.temp_file.name))
        cls.service = AccountService(storage)

    @classmethod
    def teardown_class(cls):
        # 清理临时文件
        Path(cls.temp_file.name).unlink(missing_ok=True)

    def test_network_errors(self):
        """测试网络错误识别"""
        network_errors = [
            "curl: (56) CONNECT tunnel failed, response 429",
            "Failed to perform, curl: (7) connection refused",
            "connection timeout occurred",
            "response 429 Too Many Requests",
            "response 502 Bad Gateway",
            "response 503 Service Unavailable",
            "SSL handshake failed",
            "TLS connection error",
            "network unreachable",
            "temporarily unavailable",
            "proxy connection failed",
        ]

        for error in network_errors:
            assert self.service._is_network_or_proxy_error(error), \
                f"Should recognize as network error: {error}"
            assert not self.service._is_account_error(error), \
                f"Should NOT recognize as account error: {error}"

    def test_account_errors(self):
        """测试账号错误识别"""
        account_errors = [
            "refresh_token_invalidated",
            "Your session has ended. Please log in again.",
            "invalid_request_error",
            "account banned",
            "account suspended",
            "401 unauthorized",
            "authentication failed",
        ]

        for error in account_errors:
            assert self.service._is_account_error(error), \
                f"Should recognize as account error: {error}"
            assert not self.service._is_network_or_proxy_error(error), \
                f"Should NOT recognize as network error: {error}"

    def test_neither_error(self):
        """测试既不是网络错误也不是账号错误的情况"""
        ambiguous_errors = [
            "unknown error",
            "unexpected response",
            "",
            None,
        ]

        for error in ambiguous_errors:
            error_str = str(error) if error else ""
            is_network = self.service._is_network_or_proxy_error(error_str)
            is_account = self.service._is_account_error(error_str)
            # 既不是网络错误也不是账号错误时，会被默认当作账号错误处理（保守策略）
            assert not is_network and not is_account, \
                f"Should be neither: {error}"

    def test_case_insensitive(self):
        """测试大小写不敏感"""
        assert self.service._is_network_or_proxy_error("CURL: (56) ERROR")
        assert self.service._is_network_or_proxy_error("Curl: (56) Error")
        assert self.service._is_account_error("REFRESH_TOKEN_INVALIDATED")
        assert self.service._is_account_error("Session Has Ended")

    def test_partial_match(self):
        """测试部分匹配"""
        # 完整错误消息包含关键词即可识别
        full_error = "oauth_refresh_http_401: {'message': 'Your session has ended. Please log in again.', 'type': 'invalid_request_error', 'param': None, 'code': 'refresh_token_invalidated'}"
        assert self.service._is_account_error(full_error)

        full_network_error = "Failed to perform, curl: (56) CONNECT tunnel failed, response 429. See https://curl.se/libcurl/c/libcurl-errors.html first for more details."
        assert self.service._is_network_or_proxy_error(full_network_error)

    def test_real_world_errors(self):
        """测试真实日志中的错误消息"""
        # 来自用户日志的真实错误
        real_errors = [
            (
                "oauth_refresh_http_401: {'message': 'Your session has ended. Please log in again.', 'type': 'invalid_request_error', 'param': None, 'code': 'refresh_token_invalidated'}",
                'account'
            ),
            (
                "Failed to perform, curl: (56) CONNECT tunnel failed, response 429. See https://curl.se/libcurl/c/libcurl-errors.html first for more details.",
                'network'
            ),
            (
                "token invalidated (/backend-api/me)",
                'account'
            ),
        ]

        for error, expected_type in real_errors:
            if expected_type == 'network':
                assert self.service._is_network_or_proxy_error(error), \
                    f"Real-world network error not recognized: {error}"
            elif expected_type == 'account':
                assert self.service._is_account_error(error), \
                    f"Real-world account error not recognized: {error}"


if __name__ == "__main__":
    # 直接运行测试
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
