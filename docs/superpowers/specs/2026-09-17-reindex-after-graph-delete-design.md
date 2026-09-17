# Reindex After Graph Delete — Design

## Goal

Branch `fix/reindex-after-graph-delete` (never `main` until reviewed). Fix a confirmed bug: a FalkorDB group can end up permanently missing all its indices (range, fulltext, and the HNSW vector index) after being deleted from the web UI and re-imported into the same `group_id`, causing entity-dedup searches to silently fall back to O(n) full-scan and degrade progressively as the group grows.

Root cause fully confirmed via log analysis on pioneer10 (exact timeline of index-creation bursts correlated with `GRAPH.DELETE` calls and driver construction) and code reading — see the investigation summary in this document's Root Cause section.

---

## Root Cause

Index creation only ever happens as a side effect of constructing a new `FalkorDriver` instance — `falkordb_driver.py:181-187`, inside `__init__`: `self._init_task = loop.create_task(self.build_indices_and_constraints())`, fire-and-forget.

`mcp_server/src/graphiti_mcp_server.py:647-649`, inside the `add_memory_bulk` MCP tool, mutates a long-lived shared object:

```python
if effective_group_id != client.driver._database:
    client.driver = client.driver.clone(database=effective_group_id)
    client.clients.driver = client.driver
```

This makes `client.driver` "sticky" to whichever `group_id` was last bulk-imported, for the remaining lifetime of the process. `FalkorDriver.clone(database)` (`falkordb_driver.py:340-353`) returns `self` — no new object, no `_init_task`, no index creation — whenever the requested `database` already matches the driver's current one.

If the underlying FalkorDB graph is deleted externally (the web app's group-delete button calls `GRAPH.DELETE`, which drops the graph key **and every index on it**) while `client.driver` is still pinned to that exact `group_id`, the next bulk import into the same group hits the `clone()` no-op branch: no new `FalkorDriver` is constructed, so `build_indices_and_constraints()` never runs, and the graph is silently rebuilt from zero with no indices at all. Confirmed end-to-end on group `fast_2311e44b_abs`: indexed correctly at 10:51, deleted at 11:15, correctly re-indexed at 11:22 (driver was pinned elsewhere at that point, so `clone()` *did* construct fresh), deleted again at 11:44, then re-imported at 14:57 with `client.driver` still pinned to this exact group from the 11:22-11:28 window — zero index-creation log output, and the subsequent import logs `HNSW vector search failed, falling back to full scan` repeatedly.

**The fix that already exists and is proven correct, elsewhere in this codebase:** `graphiti_core/graphiti.py`'s `_resolve_request_scope()` (`:1020-1048`) was written specifically to stop this exact class of bug — its own docstring cites issue #1676, a prior incident where mutating a shared `self.driver` caused a concurrent call for a different `group_id` to silently write to the wrong database. It returns a **per-call** driver/clients bundle instead of mutating shared state. `Graphiti.add_episode_bulk` already calls `self._resolve_request_scope(group_id)` as its first step (verified in `graphiti_core/graphiti.py`, inside `add_episode_bulk`).

This means `graphiti_mcp_server.py:647-649`'s manual mutation is not just unsafe — it is **entirely redundant**. `add_memory_bulk` calls `client.add_episode_bulk(bulk_episodes=raw_episodes, group_id=effective_group_id, ...)` two lines later, which already resolves the correct request-scoped driver internally via the exact mechanism this bug needs. Nothing in `add_memory_bulk` reads `client.driver` again after that point.

---

## Architecture

Two independent fixes, both confirmed low-risk:

1. **Remove the redundant, unsafe driver mutation** in `add_memory_bulk` (`graphiti_mcp_server.py`). No replacement code needed — `add_episode_bulk`'s own internal `_resolve_request_scope` call already does the right thing. This also removes the "stickiness" that the search path's `handle_multiple_group_ids` decorator (`decorators.py:60-69`, reads `self.clients.driver._database`) depends on to decide whether it needs to clone — with `client.driver` no longer pinned by bulk imports, that decorator's own clone check works correctly too, as a side effect.

2. **Stop silently swallowing vector-index-creation failures.** `falkordb_driver.py`'s `build_indices_and_constraints()` currently wraps the vector index creation in `with suppress(Exception)` — any failure (not just "already exists", which `execute_query` already handles separately by logging `Index already exists: …` and returning) disappears with no trace. This didn't cause the incident investigated here (nothing ran at all, there was no exception to swallow), but it's a real, separate observability gap: if index creation ever does fail for a genuine reason, nobody will know. Replace the blanket suppression with a caught-and-logged warning.

## Fix 1 — remove the redundant mutation

**File:** `mcp_server/src/graphiti_mcp_server.py`

Remove these three lines from `add_memory_bulk` (currently right after `client = await graphiti_service.get_client()`):

```python
if effective_group_id != client.driver._database:
    client.driver = client.driver.clone(database=effective_group_id)
    client.clients.driver = client.driver
```

`effective_group_id` is still computed and still passed explicitly to `client.add_episode_bulk(group_id=effective_group_id, ...)` a few lines below — that call already resolves a correct, request-scoped driver via `_resolve_request_scope`, without ever touching `client.driver`/`client.clients.driver`.

## Fix 2 — log vector-index-creation failures

**File:** `graphiti_core/driver/falkordb_driver.py`, inside `build_indices_and_constraints()`

Change:

```python
with suppress(Exception):
    await self.execute_query(vector_index_query)
```

to:

```python
try:
    await self.execute_query(vector_index_query)
except Exception as e:
    logger.warning(f'Failed to create HNSW vector index: {e}')
```

`execute_query` (`falkordb_driver.py:248-256`) already special-cases `'already indexed' in str(e)` by logging at `info` level and returning without raising — so this `except` block only fires for genuine failures, not the routine "index already exists" case.

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Group deleted then re-imported into the same `group_id`, with `client.driver` pinned to it | After Fix 1: `add_episode_bulk`'s internal `_resolve_request_scope` always constructs a fresh driver whenever `group_id != self.driver._database` — and since `self.driver` (the `Graphiti` instance's own top-level driver) is never mutated by anything anymore, it stays at its true default (`main`), so any non-default `group_id` always gets a fresh clone and a fresh index-creation attempt. No more permanently-stuck state. |
| Vector index creation genuinely fails (e.g., malformed dimension config) | After Fix 2: logged as a `warning` with the exception message, instead of vanishing silently. Range/fulltext index creation already raises and is logged via `execute_query`'s existing `except` path — unchanged. |
| Concurrent bulk imports to different groups | Already safe today via `_resolve_request_scope`'s per-call driver — Fix 1 doesn't change this, it just stops `add_memory_bulk` from undermining it before the safe path even runs. |

---

## Testing

- **Unit test** in `mcp_server/tests/`, following the existing test file conventions for `add_memory_bulk` (see `mcp_server/tests/test_add_memory_bulk_params.py`, which already mocks `graphiti_service`/`client` with `AsyncMock`/`patch.object(..., create=True)`): call `add_memory_bulk` twice with different `group_id`s using a mocked `client` whose `.driver` is a plain sentinel object, and assert `client.driver` (identity, e.g. `is` comparison) is **unchanged** after both calls — this is the regression test for the redundant-mutation removal. Also assert `client.add_episode_bulk` was called with `group_id=` matching each call's `effective_group_id`, confirming the parameter is still threaded through correctly.
- **Unit test** for Fix 2: call `build_indices_and_constraints()` against a driver whose `execute_query` is mocked to raise a non-"already indexed" exception specifically for the vector index query, and assert `logger.warning` was called with a message containing the exception text, and that the exception does not propagate out of `build_indices_and_constraints()` (existing behavior preserved — a failure here must not block the rest of index creation or driver construction).

---

## Global Constraints

- Branch: `fix/reindex-after-graph-delete` only. No commits to `main` without explicit approval.
- No new dependencies.
- Do not change `_resolve_request_scope`, `handle_multiple_group_ids`, or any other already-correct request-scoping code — this fix removes code that duplicates and undermines them, it doesn't touch them.
- Do not address the pre-existing, unrelated missing indices on other groups (`valeria-fast`, etc.) or attempt to migrate any group's data — out of scope, handled separately (already done for `fast_2311e44b_abs` via the existing `migrate_vector_indices.py` script, outside this branch).
- Python, existing test conventions (pytest via `uv run pytest`, mocks matching `test_add_memory_bulk_params.py`'s style).
