# Task 2 Report — bulk-import: add_memory_bulk new parameters

**Status:** DONE  
**Date:** 2026-09-16  
**Branch:** feat/bulk-import

## Changes

### `mcp_server/src/graphiti_mcp_server.py`
- Added three new optional parameters to `add_memory_bulk`:
  - `custom_extraction_instructions: str | None = None`
  - `excluded_entity_types: list[str] | None = None`
  - `saga: str | None = None`
- Added per-episode `episode_metadata` mapping: `ep.get('episode_metadata') or None` passed to `RawEpisode(episode_metadata=...)`
- Forwarded all three new params to `client.add_episode_bulk(...)`
- Updated docstring to document the new parameters

### `mcp_server/tests/test_add_memory_bulk_params.py` (new file)
Five unit tests using `AsyncMock` / `MagicMock` with `patch.object(..., create=True)`:

| Test | Verifies |
|---|---|
| `test_custom_extraction_instructions_passed_through` | kwarg forwarded to `add_episode_bulk` |
| `test_excluded_entity_types_passed_through` | kwarg forwarded to `add_episode_bulk` |
| `test_saga_passed_through` | kwarg forwarded to `add_episode_bulk` |
| `test_episode_metadata_per_episode` | per-episode dict value reaches `RawEpisode` |
| `test_episode_metadata_defaults_to_none` | absent key → `None` in `RawEpisode` |

## Test results

```
5 passed in 0.67s
```

Command: `cd mcp_server && uv run pytest tests/test_add_memory_bulk_params.py -v`

## Notes

- `config` is declared as a bare type annotation (`config: GraphitiConfig`) at module level — no initial value. `patch.object(..., create=True)` is required to mock it.
- `add_episode_bulk` in graphiti_core already accepted all three new params; only the MCP wrapper needed updating.
