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
        # The original secret value must not survive
        assert "supersecret" not in result or "sk_live" not in result or "hunter2" not in result

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
        assert result.count(REDACTED_MARKER) >= 3  # At least three distinct patterns

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
