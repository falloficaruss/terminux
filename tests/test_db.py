"""Tests for backend/app/db.py.

Focuses on session-gap logic, project-root anchoring, event storage,
failure-fix bookkeeping, and the cross-session failure lookup.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


from app.db import Store, find_project_root, to_iso, parse_iso, utc_now


# ---------------------------------------------------------------------------
# Helpers
BASE_TIME = utc_now()


def _ts(minutes_offset: int = 0, base: datetime | None = None) -> datetime:
    """Return a UTC datetime offset by *minutes_offset* from *base*."""
    base = base or BASE_TIME
    return base + timedelta(minutes=minutes_offset)


def _add(
    store: Store,
    command: str = "echo hi",
    output: str = "",
    exit_code: int = 0,
    cwd: str = "/home/user/project",
    project_root: str = "/home/user/project",
    category: str = "general",
    root_cause: str | None = None,
    root_cause_confidence: str | None = None,
    event_time: datetime | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, int]:
    """Convenience wrapper around store.add_event with sensible defaults."""
    return store.add_event(
        command=command,
        output=output,
        exit_code=exit_code,
        duration_ms=None,
        cwd=cwd,
        project_root=project_root,
        category=category,
        root_cause=root_cause,
        root_cause_confidence=root_cause_confidence,
        event_time=event_time or _ts(),
        env=env,
    )


# ---------------------------------------------------------------------------
# to_iso / parse_iso round-trip
# ---------------------------------------------------------------------------
class TestIsoHelpers:
    def test_round_trip_aware(self) -> None:
        dt = datetime(2026, 1, 15, 8, 30, 0, tzinfo=timezone.utc)
        assert parse_iso(to_iso(dt)) == dt

    def test_naive_gets_utc(self) -> None:
        naive = datetime(2026, 1, 15, 8, 30, 0)
        iso = to_iso(naive)
        parsed = parse_iso(iso)
        assert parsed.tzinfo is not None

    def test_utc_now_is_aware(self) -> None:
        now = utc_now()
        assert now.tzinfo is not None


# ---------------------------------------------------------------------------
# find_project_root
# ---------------------------------------------------------------------------
class TestFindProjectRoot:
    def test_git_dir_detected(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        sub = tmp_path / "src" / "pkg"
        sub.mkdir(parents=True)
        assert find_project_root(str(sub)) == str(tmp_path)

    def test_pyproject_detected(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").touch()
        sub = tmp_path / "deep" / "nested"
        sub.mkdir(parents=True)
        assert find_project_root(str(sub)) == str(tmp_path)

    def test_package_json_detected(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").touch()
        assert find_project_root(str(tmp_path)) == str(tmp_path)

    def test_go_mod_detected(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").touch()
        assert find_project_root(str(tmp_path)) == str(tmp_path)

    def test_cargo_toml_detected(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").touch()
        assert find_project_root(str(tmp_path)) == str(tmp_path)

    def test_no_marker_returns_path_itself(self, tmp_path: Path) -> None:
        bare = tmp_path / "no_markers"
        bare.mkdir()
        assert find_project_root(str(bare)) == str(bare)

    def test_invalid_path_returns_input(self) -> None:
        bogus = "/this/path/does/not/exist/at/all"
        result = find_project_root(bogus)
        # Should not raise; returns the input string as fallback
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Session-gap logic
# ---------------------------------------------------------------------------
class TestSessionGap:
    """The Store uses a configurable gap (default 20 min) to decide whether
    consecutive events belong to the same session."""

    def test_events_within_gap_share_session(self, store: Store) -> None:
        _, s1 = _add(store, event_time=_ts(0))
        _, s2 = _add(store, event_time=_ts(10))  # +10 min, within 20-min gap
        assert s1 == s2

    def test_events_beyond_gap_create_new_session(self, store: Store) -> None:
        _, s1 = _add(store, event_time=_ts(0))
        _, s2 = _add(store, event_time=_ts(25))  # +25 min, exceeds 20-min gap
        assert s1 != s2

    def test_exact_gap_boundary_stays_in_session(self, store: Store) -> None:
        """An event arriving exactly at the gap boundary is still in-session."""
        _, s1 = _add(store, event_time=_ts(0))
        _, s2 = _add(store, event_time=_ts(20))  # exactly 20 min = gap
        assert s1 == s2

    def test_one_second_past_gap_creates_new_session(self, store: Store) -> None:
        t0 = _ts(0)
        t_over = t0 + timedelta(seconds=1201)  # 1 second past 1200s gap
        _, s1 = _add(store, event_time=t0)
        _, s2 = _add(store, event_time=t_over)
        assert s1 != s2

    def test_session_gap_extends_with_activity(self, store: Store) -> None:
        """The gap is measured from the *last* event, not the session start.
        Three events each 15 min apart should all share one session."""
        _, s1 = _add(store, event_time=_ts(0))
        _, s2 = _add(store, event_time=_ts(15))
        _, s3 = _add(store, event_time=_ts(30))
        assert s1 == s2 == s3

    def test_different_project_roots_get_separate_sessions(self, store: Store) -> None:
        _, s1 = _add(store, project_root="/project-a", event_time=_ts(0))
        _, s2 = _add(store, project_root="/project-b", event_time=_ts(0))
        assert s1 != s2

    def test_custom_gap_seconds(self, tmp_path: Path) -> None:
        """A Store with a 60-second gap splits much more aggressively."""
        short = Store(sqlite_path=str(tmp_path / "short.db"), session_gap_seconds=60)
        _, s1 = _add(short, event_time=_ts(0))
        _, s2 = _add(short, event_time=_ts(2))  # +2 min = 120s > 60s gap
        assert s1 != s2


# ---------------------------------------------------------------------------
# Session touch / last_event_at bookkeeping
# ---------------------------------------------------------------------------
class TestSessionTouchSemantics:
    def test_last_event_at_updated(self, store: Store) -> None:
        _, sid = _add(store, event_time=_ts(0))
        _add(store, event_time=_ts(5))
        session = store.get_session(sid)
        assert session is not None
        last = parse_iso(session["last_event_at"])
        assert last == _ts(5)

    def test_session_started_at_unchanged(self, store: Store) -> None:
        _, sid = _add(store, event_time=_ts(0))
        _add(store, event_time=_ts(5))
        session = store.get_session(sid)
        started = parse_iso(session["started_at"])
        assert started == _ts(0)


# ---------------------------------------------------------------------------
# add_event & retrieval
# ---------------------------------------------------------------------------
class TestAddAndRetrieve:
    def test_event_stored_and_retrievable(self, store: Store) -> None:
        eid, sid = _add(
            store,
            command="pytest -x",
            output="PASSED",
            exit_code=0,
            category="python-dev",
            event_time=_ts(0),
        )
        event = store.get_event(eid)
        assert event is not None
        assert event["command"] == "pytest -x"
        assert event["output"] == "PASSED"
        assert event["exit_code"] == 0
        assert event["category"] == "python-dev"
        assert event["session_id"] == sid

    def test_recent_session_events_ordering(self, store: Store) -> None:
        _add(store, command="cmd1", event_time=_ts(0))
        _add(store, command="cmd2", event_time=_ts(1))
        _add(store, command="cmd3", event_time=_ts(2))
        # All in same session, limit=50
        events = store.recent_session_events(session_id=1, limit=50)
        commands = [e["command"] for e in events]
        assert commands == ["cmd1", "cmd2", "cmd3"]

    def test_recent_session_events_limit(self, store: Store) -> None:
        for i in range(5):
            _add(store, command=f"cmd{i}", event_time=_ts(i))
        events = store.recent_session_events(session_id=1, limit=2)
        assert len(events) == 2

    def test_env_json_round_trip(self, store: Store) -> None:
        import json

        env = {"HOME": "/home/user", "SHELL": "/bin/bash"}
        eid, _ = _add(store, env=env, event_time=_ts(0))
        event = store.get_event(eid)
        assert json.loads(event["env_json"]) == env


# ---------------------------------------------------------------------------
# Failure detection
# ---------------------------------------------------------------------------
class TestFailureDetection:
    def test_find_recent_failure_for_command(self, store: Store) -> None:
        _add(store, command="make build", exit_code=1, event_time=_ts(0))
        _add(store, command="make test", exit_code=0, event_time=_ts(1))
        _, sid = _add(store, command="make build", exit_code=0, event_time=_ts(2))

        failure = store.find_recent_failure_for_command(sid, "make build")
        assert failure is not None
        assert failure["exit_code"] == 1

    def test_no_failure_returns_none(self, store: Store) -> None:
        _, sid = _add(store, command="echo ok", exit_code=0, event_time=_ts(0))
        assert store.find_recent_failure_for_command(sid, "echo ok") is None

    def test_find_recent_failure_cross_session(self, store: Store) -> None:
        root = "/home/user/project"
        # Session 1
        _add(store, command="deploy", exit_code=1, project_root=root, event_time=_ts(0))
        # Session 2 (gap exceeded)
        _add(
            store, command="deploy", exit_code=0, project_root=root, event_time=_ts(25)
        )

        failure = store.find_recent_failure_cross_session(
            root, "deploy", hours_lookback=48
        )
        assert failure is not None
        assert failure["exit_code"] == 1


# ---------------------------------------------------------------------------
# Failure-fix bookkeeping
# ---------------------------------------------------------------------------
class TestFailureFix:
    def test_add_failure_fix(self, store: Store) -> None:
        eid_fail, sid = _add(store, command="build", exit_code=1, event_time=_ts(0))
        eid_fix, _ = _add(store, command="build", exit_code=0, event_time=_ts(5))

        store.add_failure_fix(sid, eid_fail, eid_fix, "Fixed the Makefile target")

        row = store.conn.execute(
            "SELECT * FROM failure_fixes WHERE session_id = ?", (sid,)
        ).fetchone()
        assert row is not None
        assert row["failure_event_id"] == eid_fail
        assert row["success_event_id"] == eid_fix
        assert row["summary"] == "Fixed the Makefile target"


# ---------------------------------------------------------------------------
# search_events_like
# ---------------------------------------------------------------------------
class TestSearchEventsLike:
    def test_matches_command(self, store: Store) -> None:
        _add(store, command="docker compose up", event_time=_ts(0))
        _add(store, command="echo hello", event_time=_ts(1))
        results = store.search_events_like("docker", limit=10)
        assert len(results) == 1
        assert results[0]["command"] == "docker compose up"

    def test_matches_output(self, store: Store) -> None:
        _add(store, command="run", output="connection refused", event_time=_ts(0))
        results = store.search_events_like("refused", limit=10)
        assert len(results) == 1

    def test_matches_category(self, store: Store) -> None:
        _add(store, command="x", category="gpu", event_time=_ts(0))
        results = store.search_events_like("gpu", limit=10)
        assert len(results) == 1

    def test_matches_root_cause(self, store: Store) -> None:
        _add(store, command="x", root_cause="port conflict", event_time=_ts(0))
        results = store.search_events_like("port conflict", limit=10)
        assert len(results) == 1

    def test_limit_respected(self, store: Store) -> None:
        for i in range(5):
            _add(store, command=f"git cmd{i}", event_time=_ts(i))
        results = store.search_events_like("git", limit=3)
        assert len(results) == 3


# ---------------------------------------------------------------------------
# find_session_by_query
# ---------------------------------------------------------------------------
class TestFindSessionByQuery:
    def test_finds_session_with_most_hits(self, store: Store) -> None:
        # Session 1: 1 docker event
        _add(store, command="docker build .", project_root="/a", event_time=_ts(0))
        # Session 2: 3 docker events
        _add(store, command="docker run x", project_root="/b", event_time=_ts(0))
        _add(store, command="docker ps", project_root="/b", event_time=_ts(1))
        _add(store, command="docker logs c", project_root="/b", event_time=_ts(2))

        session = store.find_session_by_query("docker")
        assert session is not None
        assert session["total_hits"] == 3

    def test_returns_none_when_no_match(self, store: Store) -> None:
        _add(store, command="echo hi", event_time=_ts(0))
        assert store.find_session_by_query("kubernetes") is None


# ---------------------------------------------------------------------------
# preflight_warnings
# ---------------------------------------------------------------------------
class TestPreflightWarnings:
    def test_returns_warnings_for_past_failures(self, store: Store) -> None:
        _add(
            store,
            command="docker compose up",
            exit_code=1,
            root_cause="port conflict",
            event_time=_ts(0),
        )
        warnings = store.preflight_warnings("deploy", ["docker compose up"])
        assert len(warnings) >= 1
        assert "port conflict" in warnings[0]["message"]

    def test_no_warnings_when_clean_history(self, store: Store) -> None:
        _add(store, command="echo ok", exit_code=0, event_time=_ts(0))
        warnings = store.preflight_warnings("run", ["echo ok"])
        assert warnings == []

    def test_deduplication(self, store: Store) -> None:
        """If the same term appears as both task and command, we shouldn't
        get duplicate warnings."""
        _add(store, command="build", exit_code=1, event_time=_ts(0))
        warnings = store.preflight_warnings("build", ["build"])
        # The dedup dict should collapse them
        assert len(warnings) == 1


# ---------------------------------------------------------------------------
# weekly_stats
# ---------------------------------------------------------------------------
class TestWeeklyStats:
    def test_basic_stats(self, store: Store) -> None:
        _add(store, exit_code=0, event_time=utc_now())
        _add(store, exit_code=1, event_time=utc_now())
        _add(store, exit_code=1, event_time=utc_now())
        stats = store.weekly_stats(days=7)
        assert stats["total_events"] == 3
        assert stats["total_failures"] == 2
        assert abs(stats["failure_rate"] - 2 / 3) < 0.01

    def test_empty_db_stats(self, store: Store) -> None:
        stats = store.weekly_stats(days=7)
        assert stats["total_events"] == 0
        assert stats["total_failures"] == 0
        assert stats["failure_rate"] == 0.0


# ---------------------------------------------------------------------------
# Correction
# ---------------------------------------------------------------------------
class TestEventCorrection:
    def test_correct_category(self, store: Store) -> None:
        eid, _ = _add(store, command="echo hi", category="general", event_time=_ts(0))
        updated = store.update_event_correction(eid, category="python-dev")
        assert updated is not None
        assert updated["category"] == "python-dev"
        assert updated["command"] == "echo hi"  # unchanged

    def test_correct_root_cause(self, store: Store) -> None:
        eid, _ = _add(
            store, command="build", root_cause="port conflict", event_time=_ts(0)
        )
        updated = store.update_event_correction(eid, root_cause="permission issue")
        assert updated is not None
        assert updated["root_cause"] == "permission issue"

    def test_correct_both(self, store: Store) -> None:
        eid, _ = _add(
            store,
            command="build",
            category="container",
            root_cause="port conflict",
            event_time=_ts(0),
        )
        updated = store.update_event_correction(
            eid, category="deployment", root_cause="auth failure"
        )
        assert updated["category"] == "deployment"
        assert updated["root_cause"] == "auth failure"

    def test_correct_none_returns_unchanged(self, store: Store) -> None:
        eid, _ = _add(store, command="echo hi", category="general", event_time=_ts(0))
        updated = store.update_event_correction(eid)
        assert updated is not None
        assert updated["category"] == "general"

    def test_correct_nonexistent_event(self, store: Store) -> None:
        updated = store.update_event_correction(9999, category="general")
        assert updated is None
