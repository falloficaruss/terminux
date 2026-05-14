"""Tests for backend/app/classifier.py.

The classifier is regex-based and can silently miscategorize.
These tests pin the expected behaviour and expose known edge cases.
"""
from __future__ import annotations

import pytest

from app.classifier import classify_event, likely_root_cause, CATEGORY_PATTERNS


# ---------------------------------------------------------------------------
# classify_event  –  happy-path: each category fires on its canonical command
# ---------------------------------------------------------------------------
class TestClassifyEventHappyPath:
    """Every declared category must match at least one obvious input."""

    @pytest.mark.parametrize(
        "command, output, expected",
        [
            # git-workflow
            ("git push origin main", "", "git-workflow"),
            ("gh pr create", "", "git-workflow"),
            # deployment
            ("kubectl apply -f deploy.yaml", "", "deployment"),
            ("helm install myrelease ./chart", "", "deployment"),
            ("terraform plan", "", "deployment"),
            # container
            ("docker build .", "", "container"),
            ("docker compose up -d", "", "container"),
            ("podman run alpine", "", "container"),
            # networking
            ("ping 8.8.8.8", "", "networking"),
            ("curl https://example.com", "", "networking"),
            ("ifconfig eth0", "", "networking"),
            # service
            ("systemctl status nginx", "", "service"),
            ("service ssh restart", "", "service"),
            ("journalctl -u nginx", "", "service"),
            # package-management
            ("apt install vim", "", "package-management"),
            ("pip install requests", "", "package-management"),
            ("npm install express", "", "package-management"),
            ("pnpm add vite", "", "package-management"),
            ("yum install gcc", "", "package-management"),
            # python-dev
            ("pytest -v", "", "python-dev"),
            ("uvicorn main:app", "", "python-dev"),
            ("python script.py", "", "python-dev"),
            ("poetry install", "", "python-dev"),
            # gpu
            ("nvidia-smi", "", "gpu"),
            ("cuda-memcheck ./app", "", "gpu"),
            ("rocm-smi", "", "gpu"),
            # auth  (patterns match inside output too)
            ("some-cmd", "permission denied", "auth"),
            ("some-cmd", "401 unauthorized", "auth"),
            ("some-cmd", "403 forbidden", "auth"),
            # filesystem
            ("rm -rf /tmp/foo", "", "filesystem"),
            ("mv a.txt b.txt", "", "filesystem"),
            ("cp -r dir1 dir2", "", "filesystem"),
            ("chmod 644 file", "", "filesystem"),
            ("chown root:root file", "", "filesystem"),
        ],
    )
    def test_canonical_match(self, command: str, output: str, expected: str) -> None:
        assert classify_event(command, output) == expected


# ---------------------------------------------------------------------------
# classify_event  –  edge cases & ambiguity
# ---------------------------------------------------------------------------
class TestClassifyEventEdgeCases:
    def test_fallback_to_general(self) -> None:
        """Unrecognised commands with clean output land in 'general'."""
        assert classify_event("echo hello", "hello") == "general"

    def test_error_in_output_triggers_debugging(self) -> None:
        """Commands with error/failed in output but no category match -> debugging."""
        assert classify_event("make build", "error: undefined reference") == "debugging"
        assert classify_event("make build", "build failed at step 3") == "debugging"

    def test_first_matching_category_wins(self) -> None:
        """When multiple categories could match, the first in dict order wins."""
        # 'git' matches git-workflow, but the command also contains 'docker'.
        # Since git-workflow is declared first, it should win.
        result = classify_event("git clone && docker build .", "")
        assert result == "git-workflow"

    def test_case_insensitive(self) -> None:
        """Classifier lowercases the blob, so UPPER-CASE commands still match."""
        assert classify_event("GIT PUSH", "") == "git-workflow"
        assert classify_event("DOCKER BUILD .", "") == "container"

    def test_output_only_classification(self) -> None:
        """Category can be detected from output alone, even if command is opaque."""
        assert classify_event("./run.sh", "kubectl apply finished") == "deployment"

    # -- known problematic patterns --

    def test_ip_word_boundary_false_positive(self) -> None:
        """The \\bip\\b pattern matches bare 'ip' – but also in words that
        are exactly 'ip'.  This documents the current behaviour.
        If this test starts failing it means the regex was improved."""
        # 'ip' as a standalone word IS a valid networking command
        assert classify_event("ip addr show", "") == "networking"

    def test_compose_without_docker_matches_container(self) -> None:
        """'compose' alone triggers 'container', which may be wrong for
        e.g. 'docker compose' vs 'compose email'. Documents the current regex."""
        assert classify_event("compose an email", "") == "container"

    def test_uv_word_boundary_false_positive(self) -> None:
        """The \\buv\\b pattern is meant for the Python tool 'uv' but will match
        any bare 'uv' token, e.g. 'uv light'.  Documents the ambiguity."""
        assert classify_event("check uv levels", "") == "python-dev"

    def test_token_in_auth_matches_broadly(self) -> None:
        """The auth category includes 'token' without word boundaries.
        Any command/output containing the substring 'token' triggers auth."""
        assert classify_event("generate token", "") == "auth"
        # Even harmless references:
        assert classify_event("echo token_name", "") == "auth"

    def test_service_keyword_false_positive(self) -> None:
        """'service' without context matches the service category.
        e.g. 'the microservice' contains 'service' as a substring – but \\b
        boundaries save us here."""
        # \\bservice\\b won't match 'microservice' because of the prefix.
        assert classify_event("deploy microservice", "") != "service"

    def test_pip_in_pipeline_false_positive(self) -> None:
        """'pip' in '|pip' would match \\bpip\\b since '|' is a boundary.
        Documents this gotcha."""
        assert classify_event("cat reqs.txt |pip install -r -", "") == "package-management"


# ---------------------------------------------------------------------------
# likely_root_cause
# ---------------------------------------------------------------------------
class TestLikelyRootCause:
    @pytest.mark.parametrize(
        "output, expected",
        [
            ("Error: address already in use", "port conflict"),
            ("port is already allocated", "port conflict"),
            ("Permission denied", "permission issue"),
            ("connection refused", "service unavailable"),
            ("ModuleNotFoundError: No module named 'foo'", "missing dependency"),
            ("module not found: bar", "missing dependency"),
            ("401 Authentication required", "authentication failure"),
            ("HTTP 401 Unauthorized", "authentication failure"),
            ("cp: no such file or directory: /x", "missing file or path"),
        ],
    )
    def test_known_causes(self, output: str, expected: str) -> None:
        assert likely_root_cause(output) == expected

    def test_none_for_unknown_output(self) -> None:
        assert likely_root_cause("Everything looks fine") is None

    def test_case_insensitive(self) -> None:
        assert likely_root_cause("ADDRESS ALREADY IN USE") == "port conflict"
        assert likely_root_cause("PERMISSION DENIED") == "permission issue"

    def test_priority_order(self) -> None:
        """When output contains multiple signals, the first match wins."""
        combined = "address already in use and permission denied"
        assert likely_root_cause(combined) == "port conflict"


# ---------------------------------------------------------------------------
# CATEGORY_PATTERNS  –  structural sanity
# ---------------------------------------------------------------------------
class TestCategoryPatternsStructure:
    def test_all_patterns_are_valid_regex(self) -> None:
        """Every pattern string must compile without error."""
        import re

        for category, patterns in CATEGORY_PATTERNS.items():
            for p in patterns:
                try:
                    re.compile(p)
                except re.error as exc:
                    pytest.fail(f"Invalid regex in {category!r}: {p!r} -> {exc}")

    def test_no_empty_categories(self) -> None:
        for category, patterns in CATEGORY_PATTERNS.items():
            assert len(patterns) > 0, f"Category {category!r} has no patterns"
