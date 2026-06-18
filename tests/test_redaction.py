"""Tests for backend/app/redaction.py.

Covers both free-text redaction and environment-dict redaction,
including the four compiled regex patterns and the env-key heuristic.
"""

from __future__ import annotations

import pytest

from app.redaction import (
    REDACTED_MARKER,
    REDACTION_PATTERNS,
    redact_environment,
    redact_sensitive_text,
)


# ---------------------------------------------------------------------------
# redact_sensitive_text  –  individual pattern coverage
# ---------------------------------------------------------------------------
class TestRedactSensitiveText:
    """Each REDACTION_PATTERN must fire on its intended input."""

    # -- Pattern 1: key=value / key:value secrets --
    @pytest.mark.parametrize(
        "text",
        [
            "API_KEY=sk_live_abc123xyz",
            "api-key: supersecret",
            "TOKEN=ghp_abcdef1234567890abcd",
            "secret = my_s3cret_value",
            "password:hunter2",
            "PASSWORD = admin123",
        ],
    )
    def test_key_value_secrets_redacted(self, text: str) -> None:
        result = redact_sensitive_text(text)
        assert REDACTED_MARKER in result

    # -- Multi-token unquoted values (Pattern 1 extended to end of line) --
    @pytest.mark.parametrize(
        "text",
        [
            "password = supersecret extra stuff",
            "TOKEN = abc123 def456 ghi789",
        ],
    )
    def test_multi_token_value_redacted(self, text: str) -> None:
        result = redact_sensitive_text(text)
        assert REDACTED_MARKER in result
        # No words after the first should survive
        for word in text.split("=", 1)[-1].strip().split():
            assert word not in result, f"{word} leaked through"

    # -- Pattern 2: Authorization bearer tokens --
    @pytest.mark.parametrize(
        "text",
        [
            "Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.payload.sig",
            "authorization: bearer abc123def456",
        ],
    )
    def test_bearer_token_redacted(self, text: str) -> None:
        result = redact_sensitive_text(text)
        assert REDACTED_MARKER in result
        assert "eyJhbGciOiJSUzI1NiJ9" not in result

    # -- Pattern 3: GitHub PATs (ghp_...) --
    @pytest.mark.parametrize(
        "text",
        [
            "ghp_1234567890abcdefghij1234567890ab",
            "token is ghp_ABCDEFghijklmnopqrstuv",
        ],
    )
    def test_github_pat_redacted(self, text: str) -> None:
        result = redact_sensitive_text(text)
        assert REDACTED_MARKER in result
        assert "ghp_" not in result

    # -- Pattern 4: OpenAI-style API keys (sk-...) --
    @pytest.mark.parametrize(
        "text",
        [
            "sk-abcdefghijklmnopqrstuvwx",
            "key: sk-ABCDEFGHIJKLMNOPQRSTUVWX0123456789",
        ],
    )
    def test_openai_key_redacted(self, text: str) -> None:
        result = redact_sensitive_text(text)
        assert REDACTED_MARKER in result
        assert "sk-" not in result

    # -- Pattern 5: IP Addresses --
    @pytest.mark.parametrize(
        "text",
        [
            "Server IP is 192.168.1.1",
            "connected to 10.0.0.255 on port 80",
        ],
    )
    def test_ip_address_redacted(self, text: str) -> None:
        result = redact_sensitive_text(text)
        assert REDACTED_MARKER in result
        assert "192" not in result and "10." not in result

    # -- IP patterns should NOT match version strings (false positive prevention) --
    @pytest.mark.parametrize(
        "text",
        [
            "version 1.0.0.1 released",
            "npm package 2.1.3.4",
            "pip install 3.0.0.0",
        ],
    )
    def test_version_string_not_redacted(self, text: str) -> None:
        result = redact_sensitive_text(text)
        assert REDACTED_MARKER not in result

    # -- Pattern 6: Database Connection Strings --
    @pytest.mark.parametrize(
        "text",
        [
            "postgresql://dbuser:secretpass@localhost/mydb",
            "mysql://root:root123@127.0.0.1:3306/test",
        ],
    )
    def test_db_connection_string_redacted(self, text: str) -> None:
        result = redact_sensitive_text(text)
        assert REDACTED_MARKER in result
        assert "secretpass" not in result and "root123" not in result

    # -- Pattern 7: AWS Access Keys --
    @pytest.mark.parametrize(
        "text",
        [
            "aws_access_key_id = AKIAIOSFODNN7EXAMPLE",
            "AKIAIOSFODNN7EXAMPLE",
        ],
    )
    def test_aws_access_key_redacted(self, text: str) -> None:
        result = redact_sensitive_text(text)
        assert REDACTED_MARKER in result
        assert "AKIAIOSFODNN7EXAMPLE" not in result

    # -- Pattern 8: JWT Tokens --
    @pytest.mark.parametrize(
        "text",
        [
            "token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        ],
    )
    def test_jwt_token_redacted(self, text: str) -> None:
        result = redact_sensitive_text(text)
        assert REDACTED_MARKER in result
        assert "eyJhbGciOi" not in result

    # -- Pattern 9: SSH Private Keys --
    def test_ssh_private_key_redacted(self) -> None:
        key = (
            "-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtcn\n"
            "cHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcA==\n"
            "-----END OPENSSH PRIVATE KEY-----"
        )
        result = redact_sensitive_text(key)
        assert REDACTED_MARKER in result
        assert "b3BlbnNzaC" not in result

    # -- Pattern 10: Slack Webhooks --
    def test_slack_webhook_redacted(self) -> None:
        webhook = "https://hooks.slack.com/services/T012ABC34/B012DEF34/abc123xyz456"
        result = redact_sensitive_text(webhook)
        assert REDACTED_MARKER in result
        assert "T012ABC34" not in result

    # -- Pattern 11: Discord Webhooks --
    def test_discord_webhook_redacted(self) -> None:
        webhook = "https://discord.com/api/webhooks/1234567890/abc123xyz_DEF-ghi"
        result = redact_sensitive_text(webhook)
        assert REDACTED_MARKER in result
        assert "1234567890" not in result

    # -- Pattern 12: Quoted Secret Assignments --
    @pytest.mark.parametrize(
        "text",
        [
            "password = 'my_secret_password'",
            'secret: "another_secret_val"',
            'API_KEY = "my-api-key-123"',
        ],
    )
    def test_quoted_secret_assignment_redacted(self, text: str) -> None:
        result = redact_sensitive_text(text)
        assert REDACTED_MARKER in result
        assert "my_secret_password" not in result
        assert "another_secret_val" not in result

    # -- Pattern 13: Gemini / Google API Keys --
    @pytest.mark.parametrize(
        "text",
        [
            "TERMINUX_GEMINI_API_KEY=AIzaSyDf9c8d7e6f5a4b3c2d1e0f",
            "key: AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ0123456",
            "AIzaSy123456789abcdefghijklmnopqrstuvwxyz",
        ],
    )
    def test_gemini_api_key_redacted(self, text: str) -> None:
        result = redact_sensitive_text(text)
        assert REDACTED_MARKER in result

    # -- Safe text should survive unmodified --
    @pytest.mark.parametrize(
        "text",
        [
            "echo hello world",
            "git push origin main",
            "INFO: Server started on port 8000",
            "Compiling 42 modules...",
        ],
    )
    def test_safe_text_unchanged(self, text: str) -> None:
        assert redact_sensitive_text(text) == text

    def test_multiple_secrets_in_one_string(self) -> None:
        text = (
            "API_KEY=abc123 and also TOKEN=xyz789 "
            "and ghp_longpersonalaccesstoken12 and sk-openaikey1234567890abcdef"
        )
        result = redact_sensitive_text(text)
        assert REDACTED_MARKER in result
        # With end-of-line matching, one Pattern 1 match can consume adjacent
        # key=value pairs; the important thing is no secret values survive.
        assert "abc123" not in result
        assert "xyz789" not in result
        assert "ghp_longpersonalaccesstoken12" not in result
        assert "sk-openaikey1234567890abcdef" not in result

    def test_multiline_redaction(self) -> None:
        text = "line1\nAPI_KEY=secret_val\nline3"
        result = redact_sensitive_text(text)
        assert REDACTED_MARKER in result
        assert "line1" in result
        assert "line3" in result


# ---------------------------------------------------------------------------
# redact_environment
# ---------------------------------------------------------------------------
class TestRedactEnvironment:
    def test_empty_and_none(self) -> None:
        assert redact_environment(None) == {}
        assert redact_environment({}) == {}

    def test_sensitive_key_names_redacted(self) -> None:
        env = {
            "API_KEY": "sk-live-abc",
            "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCY",
            "AUTH_TOKEN": "tok_123",
            "DB_PASSWORD": "hunter2",
        }
        result = redact_environment(env)
        for key in env:
            assert result[key] == REDACTED_MARKER, f"{key} should be redacted"

    def test_safe_keys_preserved(self) -> None:
        env = {
            "HOME": "/home/user",
            "SHELL": "/bin/bash",
            "LANG": "en_US.UTF-8",
        }
        result = redact_environment(env)
        assert result == env

    def test_mixed_env(self) -> None:
        env = {
            "PATH": "/usr/bin:/usr/local/bin",
            "MY_SECRET": "don't-leak",
            "EDITOR": "vim",
        }
        result = redact_environment(env)
        assert result["PATH"] == env["PATH"]
        assert result["MY_SECRET"] == REDACTED_MARKER
        assert result["EDITOR"] == env["EDITOR"]

    def test_value_containing_secret_pattern_redacted(self) -> None:
        """Even if the key is innocuous, a value that looks like a secret
        should be redacted via the text-level patterns."""
        env = {
            "LOG_LINE": "set API_KEY=abc123 in config",
        }
        result = redact_environment(env)
        assert REDACTED_MARKER in result["LOG_LINE"]

    def test_case_insensitive_key_matching(self) -> None:
        """Key matching normalises to upper-case."""
        env = {"my_api_key": "secret", "Auth_Token": "tok"}
        result = redact_environment(env)
        assert result["my_api_key"] == REDACTED_MARKER
        assert result["Auth_Token"] == REDACTED_MARKER


# ---------------------------------------------------------------------------
# REDACTION_PATTERNS  –  structural sanity
# ---------------------------------------------------------------------------
class TestRedactionPatternsStructure:
    def test_all_patterns_are_compiled(self) -> None:
        import re

        for i, pattern in enumerate(REDACTION_PATTERNS):
            assert isinstance(pattern, re.Pattern), f"Pattern {i} is not compiled"

    def test_minimum_pattern_count(self) -> None:
        """We expect at least the four documented patterns."""
        assert len(REDACTION_PATTERNS) >= 4
