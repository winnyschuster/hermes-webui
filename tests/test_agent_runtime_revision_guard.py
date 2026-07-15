"""Regression coverage for Hermes Agent source changes during a WebUI process lifetime."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_loaded_agent_runtime_fails_closed_after_source_revision_changes(tmp_path: Path):
    agent_dir = tmp_path / "hermes-agent"
    agent_dir.mkdir()
    (agent_dir / "run_agent.py").write_text(
        "class AIAgent:\n    revision = 'before'\n",
        encoding="utf-8",
    )
    _git(agent_dir, "init", "-q")
    _git(agent_dir, "add", "run_agent.py")
    _git(agent_dir, "commit", "-qm", "before")

    probe = tmp_path / "probe.py"
    probe.write_text(
        """
from pathlib import Path
import subprocess

import api.streaming as streaming
from api import agent_runtime

agent_dir = Path(__file__).parent / "hermes-agent"
assert agent_runtime._AGENT_DIR == agent_dir.resolve()
assert streaming._get_ai_agent().revision == "before"

(agent_dir / "run_agent.py").write_text(
    "class AIAgent:\\n    revision = 'after'\\n",
    encoding="utf-8",
)
subprocess.run(["git", "add", "run_agent.py"], cwd=agent_dir, check=True)
subprocess.run(
    [
        "git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "after",
    ],
    cwd=agent_dir,
    check=True,
)

try:
    streaming._get_ai_agent()
except RuntimeError as exc:
    message = str(exc)
    assert "Hermes Agent was updated" in message
    assert "Restart Hermes WebUI" in message
else:
    raise AssertionError("stale in-process AIAgent was reused after its source revision changed")

try:
    agent_runtime.require_ai_agent_class()
except agent_runtime.AgentRuntimeChangedError as exc:
    assert "Restart Hermes WebUI" in str(exc)
else:
    raise AssertionError("unguarded AIAgent import was allowed after its source revision changed")
""".strip()
        + "\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.update(
        {
            "HERMES_WEBUI_AGENT_DIR": str(agent_dir),
            "HERMES_HOME": str(tmp_path / "hermes-home"),
            "HERMES_WEBUI_STATE_DIR": str(tmp_path / "webui-state"),
            "PYTHONPATH": os.pathsep.join((str(REPO), str(agent_dir))),
        }
    )
    result = subprocess.run(
        [sys.executable, str(probe)],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_initial_non_git_source_preserves_supported_runtime(monkeypatch):
    """Non-Git installs cannot be compared, so they preserve existing behavior."""
    from api import agent_runtime

    monkeypatch.setattr(agent_runtime, "_AGENT_REVISION", None)
    monkeypatch.setattr(agent_runtime, "_read_agent_revision", lambda _path: None)

    agent_runtime.ensure_agent_runtime_current()


def test_known_revision_becoming_unreadable_fails_closed(monkeypatch):
    """Losing a previously-known revision is indistinguishable from source drift."""
    from api import agent_runtime

    monkeypatch.setattr(agent_runtime, "_AGENT_REVISION", "known-revision")
    monkeypatch.setattr(agent_runtime, "_read_agent_revision", lambda _path: None)

    with pytest.raises(agent_runtime.AgentRuntimeChangedError):
        agent_runtime.ensure_agent_runtime_current()


def test_chat_start_rejects_stale_runtime_before_session_materialization(monkeypatch):
    """A stale local runtime must not claim, create, or mutate session state."""
    from api import agent_runtime, routes

    monkeypatch.setattr(routes, "webui_gateway_chat_enabled", lambda _cfg: False)
    monkeypatch.setattr(routes, "get_config", lambda: {})

    def stale():
        raise agent_runtime.AgentRuntimeChangedError("restart required")

    monkeypatch.setattr(routes, "ensure_agent_runtime_current", stale)

    def must_not_materialize(*_args, **_kwargs):
        raise AssertionError("session materialized before stale-runtime barrier")

    monkeypatch.setattr(routes, "_get_or_materialize_session", must_not_materialize)
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200: {"status": status, "payload": payload},
    )

    response = routes._handle_chat_start(object(), {"session_id": "session-1"})

    assert response == {
        "status": 409,
        "payload": {
            "error": "restart required",
            "type": "agent_runtime_stale",
            "retryable": True,
        },
    }
