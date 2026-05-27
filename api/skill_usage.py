# ── Skill usage counter (file-based, best-effort) ──

import json
import logging
import os
import threading
import time

from pathlib import Path

logger = logging.getLogger(__name__)

_SKILL_TOOL_NAMES = frozenset({"skill_view", "skill_manage", "skill_patch"})
_USAGE_FILE = ".usage.json"


def read_skill_usage(skills_dir: Path) -> dict:
    """Read the current .usage.json.

    Returns the raw nested dict ``{skill_name: {use_count: N, view_count: N, ...}}``
    or an empty dict when the file does not exist or is corrupt.
    """
    usage_path = skills_dir / _USAGE_FILE
    if not usage_path.exists():
        return {}
    try:
        raw = usage_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        logger.debug("Unexpected .usage.json format, resetting: %s", raw[:200])
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("Failed to read .usage.json: %s", exc)
        return {}


def _ensure_skill_entry(usage: dict, name: str) -> dict:
    """Return the metadata dict for *name*, creating a default one if missing."""
    entry = usage.get(name)
    if not isinstance(entry, dict):
        entry = {"use_count": 0, "view_count": 0, "patch_count": 0}
        usage[name] = entry
    return entry


def increment_skill_usage(skills_dir: Path, tool_calls: list[dict]) -> None:
    """Scan *tool_calls* for skill_view/skill_manage invocations and bump
    the corresponding counters in .usage.json atomically.

    - ``skill_view``  → increments ``view_count``, sets ``last_viewed_at``
    - ``skill_manage`` → increments ``use_count``, sets ``last_used_at``

    This is a best-effort helper — failures are logged at DEBUG level and
    silently swallowed so they never block the calling stream handler.
    """
    if not tool_calls:
        return

    updates: list[tuple[str, str]] = []
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        tname = tc.get("name") or tc.get("tool_name") or ""
        if tname not in _SKILL_TOOL_NAMES:
            continue
        args = tc.get("args") or tc.get("input") or {}
        if isinstance(args, dict):
            skill_name = args.get("name")
            if skill_name and isinstance(skill_name, str):
                if tname == "skill_view":
                    action = "view"
                elif tname == "skill_patch":
                    action = "patch"
                else:
                    action = "use"
                updates.append((skill_name, action))

    if not updates:
        return

    usage = read_skill_usage(skills_dir)
    now = time.time()

    for name, action in updates:
        entry = _ensure_skill_entry(usage, name)
        if action == "use":
            entry["use_count"] = entry.get("use_count", 0) + 1
            entry["last_used_at"] = now
        elif action == "patch":
            entry["patch_count"] = entry.get("patch_count", 0) + 1
            entry["last_patched_at"] = now
        else:
            entry["view_count"] = entry.get("view_count", 0) + 1
            entry["last_viewed_at"] = now

    usage_path = skills_dir / _USAGE_FILE
    tmp_path = usage_path.with_suffix(
        f".tmp.{os.getpid()}.{threading.current_thread().ident}"
    )
    try:
        tmp_path.write_text(
            json.dumps(usage, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_path.replace(usage_path)
    except OSError as exc:
        logger.debug("Failed to atomically write .usage.json: %s", exc)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
