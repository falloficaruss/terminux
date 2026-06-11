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
        """Verify 'compose' alone is not classified as container unless followed by subcommands."""
        assert classify_event("compose an email", "") == "general"
        assert classify_event("compose up", "") == "container"
        assert classify_event("docker compose down", "") == "container"

    def test_uv_word_boundary_false_positive(self) -> None:
        """Verify 'uv' is not classified as python-dev unless it's the tool itself."""
        assert classify_event("check uv levels", "") == "general"
        assert classify_event("uv levels", "") == "general"
        assert classify_event("uv index", "") == "general"
        assert classify_event("uv rays", "") == "general"
        assert classify_event("uv pip install requests", "") == "python-dev"
        assert classify_event("uv run script.py", "") == "python-dev"
        assert classify_event("uv", "") == "python-dev"

    def test_token_in_auth_matches_broadly(self) -> None:
        """Verify 'token' only triggers auth in explicit credential/auth contexts."""
        assert classify_event("generate token", "") == "general"
        assert classify_event("echo token_name", "") == "general"
        assert classify_event("export access_token=secret", "") == "auth"
        assert classify_event("api_token: sk-123", "") == "auth"
        assert classify_event("some-cmd", "error: token expired") == "auth"

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

    def test_priority_ordering(self) -> None:
        """When a command matches multiple categories, the first-declared wins.

        git-workflow is declared before package-management, so 'gh' must
        return git-workflow even if the command also contains 'install'.
        """
        assert classify_event("gh repo clone user/repo", "") == "git-workflow"
        assert classify_event("gh pr create", "") == "git-workflow"
        # 'gh' matches git-workflow even in an install context
        assert classify_event("pip install gh", "") == "git-workflow"
        # 'npm' matches package-management when no earlier category matches
        assert classify_event("npm init", "") == "package-management"


# ---------------------------------------------------------------------------
# likely_root_cause
# ---------------------------------------------------------------------------
class TestLikelyRootCause:
    @pytest.mark.parametrize(
        "output, expected_cause, expected_confidence",
        [
            ("Error: address already in use", "port conflict", "high"),
            ("port is already allocated", "port conflict", "high"),
            ("Permission denied", "permission issue", "high"),
            ("connection refused", "service unavailable", "high"),
            ("ModuleNotFoundError: No module named 'foo'", "missing dependency", "high"),
            ("module not found: bar", "missing dependency", "high"),
            ("401 Authentication required", "authentication failure", "medium"),
            ("HTTP 401 Unauthorized", "authentication failure", "medium"),
            ("cp: no such file or directory: /x", "missing file or path", "high"),
        ],
    )
    def test_known_causes(self, output: str, expected_cause: str, expected_confidence: str) -> None:
        cause, confidence = likely_root_cause(output)
        assert cause == expected_cause
        assert confidence == expected_confidence

    def test_none_for_unknown_output(self) -> None:
        cause, confidence = likely_root_cause("Everything looks fine")
        assert cause is None
        assert confidence == "low"

    def test_case_insensitive(self) -> None:
        cause1, conf1 = likely_root_cause("ADDRESS ALREADY IN USE")
        assert cause1 == "port conflict"
        assert conf1 == "high"
        cause2, conf2 = likely_root_cause("PERMISSION DENIED")
        assert cause2 == "permission issue"
        assert conf2 == "high"

    def test_priority_order(self) -> None:
        """When output contains multiple signals, the first match wins."""
        combined = "address already in use and permission denied"
        cause, _ = likely_root_cause(combined)
        assert cause == "port conflict"


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
