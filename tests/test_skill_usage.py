"""Tests for api/skill_usage.py — file-based skill usage counter.

Covers:
  - read_skill_usage with various .usage.json states
  - increment_skill_usage with skill_view/skill_manage tool_calls
  - Non-skill tools and missing name args are correctly ignored
  - GET /api/skills/usage route presence
"""

import json
import re
from pathlib import Path

from api.skill_usage import increment_skill_usage, read_skill_usage

_ROUTES = Path(__file__).resolve().parent.parent / "api" / "routes.py"


# ── read_skill_usage ──────────────────────────────────────────────────────────


class TestReadSkillUsage:
    def test_read_empty(self, tmp_path):
        """File does not exist -> returns {}."""
        assert read_skill_usage(tmp_path) == {}

    def test_read_valid(self, tmp_path):
        """Well-formed .usage.json with nested entries is returned as-is."""
        data = {
            "research-arxiv": {"use_count": 12, "view_count": 5},
            "hermes-agent": {"use_count": 8, "view_count": 3},
        }
        (tmp_path / ".usage.json").write_text(json.dumps(data), encoding="utf-8")
        assert read_skill_usage(tmp_path) == data

    def test_read_actual_format(self, tmp_path):
        """Actual production format with full metadata is accepted."""
        data = {
            "dev-workflow": {
                "use_count": 77,
                "view_count": 77,
                "last_used_at": 1712345678.0,
                "state": "active",
            },
        }
        (tmp_path / ".usage.json").write_text(json.dumps(data), encoding="utf-8")
        assert read_skill_usage(tmp_path) == data

    def test_read_corrupt_json(self, tmp_path):
        """Corrupt JSON returns {} without raising."""
        (tmp_path / ".usage.json").write_text("not json", encoding="utf-8")
        assert read_skill_usage(tmp_path) == {}

    def test_read_wrong_type(self, tmp_path):
        """Non-dict top-level value returns {}."""
        (tmp_path / ".usage.json").write_text("42", encoding="utf-8")
        assert read_skill_usage(tmp_path) == {}


# ── increment_skill_usage ─────────────────────────────────────────────────────


def _skill_tc(name: str) -> dict:
    return {"name": "skill_view", "args": {"name": name}}


def _skill_manage_tc(name: str) -> dict:
    return {"name": "skill_manage", "args": {"name": name}}


def _get_counter(result: dict, name: str, key: str) -> int:
    entry = result.get(name, {})
    return entry.get(key, 0) if isinstance(entry, dict) else 0


class TestIncrementSkillUsage:
    def test_skill_view_increments_view_count(self, tmp_path):
        """A single skill_view call increments view_count to 1."""
        increment_skill_usage(tmp_path, [_skill_tc("research-arxiv")])
        result = read_skill_usage(tmp_path)
        assert _get_counter(result, "research-arxiv", "view_count") == 1

    def test_skill_manage_increments_use_count(self, tmp_path):
        """A single skill_manage call increments use_count to 1."""
        increment_skill_usage(tmp_path, [_skill_manage_tc("research-arxiv")])
        result = read_skill_usage(tmp_path)
        assert _get_counter(result, "research-arxiv", "use_count") == 1

    def test_view_and_manage_tracked_separately(self, tmp_path):
        """skill_view bumps view_count; skill_manage bumps use_count."""
        increment_skill_usage(tmp_path, [
            _skill_tc("research-arxiv"),
            _skill_manage_tc("research-arxiv"),
        ])
        result = read_skill_usage(tmp_path)
        assert _get_counter(result, "research-arxiv", "view_count") == 1
        assert _get_counter(result, "research-arxiv", "use_count") == 1

    def test_accumulates(self, tmp_path):
        """Same skill called twice -> count = 2."""
        for _ in range(2):
            increment_skill_usage(tmp_path, [_skill_manage_tc("research-arxiv")])
        result = read_skill_usage(tmp_path)
        assert _get_counter(result, "research-arxiv", "use_count") == 2

    def test_multiple_skills_in_one_batch(self, tmp_path):
        """Multiple skills in a single tool_calls list are all counted."""
        increment_skill_usage(tmp_path, [
            _skill_manage_tc("research-arxiv"),
            _skill_manage_tc("hermes-agent"),
            _skill_manage_tc("research-arxiv"),
        ])
        result = read_skill_usage(tmp_path)
        assert _get_counter(result, "research-arxiv", "use_count") == 2
        assert _get_counter(result, "hermes-agent", "use_count") == 1

    def test_non_skill_tools_ignored(self, tmp_path):
        """Terminal, web_search, etc. not counted."""
        increment_skill_usage(tmp_path, [
            {"name": "terminal", "args": {"command": "ls"}},
            {"name": "web_search", "args": {"query": "test"}},
            _skill_manage_tc("github-pr-workflow"),
        ])
        result = read_skill_usage(tmp_path)
        assert _get_counter(result, "github-pr-workflow", "use_count") == 1

    def test_skill_manage_without_name(self, tmp_path):
        """skill_manage with no name arg -> skipped."""
        increment_skill_usage(tmp_path, [
            {"name": "skill_manage", "args": {}},
            _skill_manage_tc("research-arxiv"),
        ])
        result = read_skill_usage(tmp_path)
        assert _get_counter(result, "research-arxiv", "use_count") == 1

    def test_empty_tool_calls(self, tmp_path):
        """Empty list -> no file written."""
        increment_skill_usage(tmp_path, [])
        assert not (tmp_path / ".usage.json").exists()

    def test_preserves_existing_metadata(self, tmp_path):
        """Existing metadata fields are preserved after increment."""
        original = {
            "dev-workflow": {
                "use_count": 77,
                "view_count": 50,
                "state": "active",
                "pinned": False,
            },
        }
        (tmp_path / ".usage.json").write_text(json.dumps(original), encoding="utf-8")
        increment_skill_usage(tmp_path, [_skill_manage_tc("dev-workflow")])
        result = read_skill_usage(tmp_path)
        entry = result.get("dev-workflow", {})
        assert entry["use_count"] == 78
        assert entry["view_count"] == 50
        assert entry["state"] == "active"
        assert entry["pinned"] is False


# ── API route presence ────────────────────────────────────────────────────────


class TestApiSkillsUsageRoute:
    def test_route_handler_present(self):
        """routes.py contains a handler for GET /api/skills/usage."""
        src = _ROUTES.read_text(encoding="utf-8")
        assert '"/api/skills/usage"' in src, (
            "Missing /api/skills/usage route in api/routes.py"
        )
        assert "read_skill_usage" in src, (
            "read_skill_usage import missing in api/routes.py"
        )

    def test_route_returns_usage_structure(self):
        """The route response shape includes usage/skill_names/total_invocations."""
        src = _ROUTES.read_text(encoding="utf-8")
        match = re.search(
            r'return j\(handler,\s*\{[^}]*"usage"[^}]*"skill_names"[^}]*'
            r'"total_invocations"[^}]*"unique_skills_used"[^}]*\}\)',
            src,
        )
        assert match, (
            "Expected /api/skills/usage to return {usage, skill_names, "
            "total_invocations, unique_skills_used}"
        )


# ── Streaming hook presence ───────────────────────────────────────────────────


class TestStreamingHook:
    def test_skill_usage_counter_called_in_streaming(self):
        """streaming.py imports and calls increment_skill_usage."""
        from api import streaming as _s
        content = Path(_s.__file__).read_text(encoding="utf-8")
        assert "increment_skill_usage" in content, (
            "Missing increment_skill_usage call in streaming.py"
        )
