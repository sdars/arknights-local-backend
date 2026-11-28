"""Build protocol player-data deltas from two player states.

The game client treats ``playerDataDelta`` as an instruction to replace or
merge the fields that are present.  Echoing the complete player object on every
sync makes the client believe that the whole account changed, which triggers
its cross-day/data-resync prompt even when nothing meaningful changed.

This module keeps the wire format incremental.  The client already supports
nested patches (Arkdays and the official response shapes use them), so sending
only changed leaves is both smaller and semantically correct.
"""

from __future__ import annotations

from copy import deepcopy
import time
from typing import Any, Iterable, Sequence


Path = tuple[str, ...]


def _is_ignored(path: Path, ignored: set[Path]) -> bool:
    return path in ignored


def _walk(previous: Any, current: Any, path: Path, ignored: set[Path], modified: dict, deleted: dict) -> None:
    if _is_ignored(path, ignored):
        return
    if isinstance(previous, dict) and isinstance(current, dict):
        for key, old_value in previous.items():
            child_path = path + (str(key),)
            if _is_ignored(child_path, ignored):
                continue
            if key not in current:
                deleted[str(key)] = deepcopy(old_value)
                continue
            new_value = current[key]
            if new_value == old_value:
                continue
            if isinstance(old_value, dict) and isinstance(new_value, dict):
                child_modified: dict = {}
                child_deleted: dict = {}
                _walk(old_value, new_value, child_path, ignored, child_modified, child_deleted)
                if child_modified:
                    modified[str(key)] = child_modified
                if child_deleted:
                    deleted[str(key)] = child_deleted
            else:
                modified[str(key)] = deepcopy(new_value)
        for key, new_value in current.items():
            if key in previous:
                continue
            child_path = path + (str(key),)
            if _is_ignored(child_path, ignored):
                continue
            modified[str(key)] = deepcopy(new_value)
        return
    if previous != current:
        # This branch is only reached for a changed scalar/list.  The caller
        # handles the root and dict cases; keeping it here makes the helper
        # safe for direct use in tests.
        if path:
            modified[path[-1]] = deepcopy(current)


def compute_delta(previous: Any, current: Any, ignore_paths: Iterable[Sequence[str]] = ()) -> dict:
    """Return ``{"modified": ..., "deleted": ...}`` for two JSON values.

    ``ignore_paths`` are tuple field paths that are intentionally kept out of
    the diff.  The sync endpoints use this for the two timestamps which are
    server bookkeeping rather than player-visible state.
    """
    ignored = {tuple(str(part) for part in path) for path in ignore_paths}
    modified: dict = {}
    deleted: dict = {}
    if isinstance(previous, dict) and isinstance(current, dict):
        _walk(previous, current, (), ignored, modified, deleted)
    elif previous != current:
        modified = deepcopy(current)
    return {"modified": modified, "deleted": deleted}


# These are refreshed on every sync request for server-side online tracking.
# They are not user-visible changes and must not make the client re-sync.
SYNC_BOOKKEEPING_PATHS: tuple[Path, ...] = (
    ("status", "lastOnlineTs"),
    ("status", "lastRefreshTs"),
)


def sync_delta(previous: Any, current: Any) -> dict:
    """Compute the delta used by the two periodic account sync endpoints.

    The online timestamps are normally ignored because they move on every
    poll.  They are emitted once when the 04:00 game-day bucket changes so the
    client can advance its local daily-refresh cursor without receiving a
    non-empty delta on every request.
    """
    delta = compute_delta(previous, current, SYNC_BOOKKEEPING_PATHS)
    if not isinstance(previous, dict) or not isinstance(current, dict):
        return delta
    old_status = previous.get("status")
    new_status = current.get("status")
    if not isinstance(old_status, dict) or not isinstance(new_status, dict):
        return delta
    old_ts = old_status.get("lastRefreshTs")
    new_ts = new_status.get("lastRefreshTs")
    if old_ts is None or new_ts is None or _game_day(old_ts) == _game_day(new_ts):
        return delta
    modified_status = delta.setdefault("modified", {}).setdefault("status", {})
    modified_status.setdefault("lastOnlineTs", new_status.get("lastOnlineTs"))
    modified_status.setdefault("lastRefreshTs", new_ts)
    return delta


def _game_day(value: Any) -> str:
    """Return the 04:00-to-04:00 game-day key for a Unix timestamp."""
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return ""
    return time.strftime("%Y-%m-%d", time.localtime(ts - 4 * 3600))


def expand_dungeon_stages(delta: dict, current: dict) -> dict:
    """Send a complete changed stage node rather than a leaf-only patch.

    The client's battle-start and battle-finish response models read the stage
    node as a unit (``stageId``, ``state``, ``completeTimes`` and replay data
    are consumed together).  Sending only the mutated leaf works for the JSON
    merge layer but leaves those consumers with a partially populated object.
    Expand only stage nodes that actually changed; all other fields remain
    incremental.
    """
    modified = delta.get("modified") or {}
    dungeon = modified.get("dungeon")
    if not isinstance(dungeon, dict):
        return delta
    changed_stages = dungeon.get("stages")
    if not isinstance(changed_stages, dict):
        return delta
    current_stages = ((current.get("dungeon") or {}).get("stages") or {})
    for stage_id in list(changed_stages):
        node = current_stages.get(stage_id)
        if isinstance(node, dict):
            changed_stages[stage_id] = deepcopy(node)
    return delta
