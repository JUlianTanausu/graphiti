# Reindex After Graph Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop a FalkorDB group from permanently losing all its indices after being deleted and re-imported into the same `group_id`.

**Architecture:** Remove a redundant, unsafe manual driver-mutation in `add_memory_bulk` that duplicates (and undermines) `Graphiti._resolve_request_scope`'s already-correct, already-used-elsewhere request-scoped driver resolution — this is the actual root-cause fix. Separately, replace a blanket `suppress(Exception)` around vector-index creation with a logged warning, so a genuine future failure is visible instead of silent.

**Tech Stack:** Python, pytest, unittest.mock (AsyncMock/MagicMock/patch) — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-17-reindex-after-graph-delete-design.md`

## Global Constraints

- Branch: `fix/reindex-after-graph-delete` only. No commits to `main` without explicit approval.
- No new dependencies.
- Do not change `_resolve_request_scope`, `handle_multiple_group_ids`, or any other already-correct request-scoping code.
- Do not touch any group's data or attempt migrations — out of scope.
- Existing test conventions: `mcp_server/tests/` uses `pytest` + `unittest.mock` with `patch.object(mod, 'graphiti_service', mock_service)` / `patch.object(mod, 'config', _MOCK_CONFIG, create=True)`; `tests/driver/test_falkordb_driver.py` uses a `TestFalkorDriver` class with `setup_method` constructing a `FalkorDriver()` with `FalkorDB` patched, then assigning `self.driver.client = self.mock_client`, and patches `graphiti_core.driver.falkordb_driver.logger` for log assertions.

---

### Task 1: Remove the redundant driver mutation in `add_memory_bulk`

**Files:**
- Modify: `mcp_server/src/graphiti_mcp_server.py`
- Test: `mcp_server/tests/test_add_memory_bulk_params.py` (add to the existing file — it already has the exact fixtures this test needs)

**Interfaces:**
- No new interfaces produced or consumed — this task only removes code inside the existing `add_memory_bulk` function. Its signature and behavior (from the caller's perspective) are unchanged; `client.add_episode_bulk(..., group_id=effective_group_id, ...)` is still called exactly as before.

- [ ] **Step 1: Write the failing test**

Add this test to `mcp_server/tests/test_add_memory_bulk_params.py`, after the existing `test_episode_metadata_defaults_to_none` function (it reuses the file's existing `mock_service`, `mock_client`, `_BASE_EP`, `_MOCK_CONFIG` fixtures — no new imports needed):

```python
async def test_does_not_mutate_shared_driver(mock_service, mock_client):
    """add_memory_bulk must not reassign client.driver / client.clients.driver.

    add_episode_bulk already resolves a request-scoped driver internally via
    Graphiti._resolve_request_scope — reassigning client.driver here duplicated
    (and undermined) that safe mechanism, and is what let a group's driver stay
    permanently pinned to a deleted graph, silently skipping index (re)creation
    forever. See docs/superpowers/specs/2026-09-17-reindex-after-graph-delete-design.md.
    """
    original_driver = mock_client.driver
    original_clients = mock_client.clients

    with (
        patch.object(mod, 'graphiti_service', mock_service),
        patch.object(mod, 'config', _MOCK_CONFIG, create=True),
    ):
        await mod.add_memory_bulk(episodes=[_BASE_EP], group_id='some_other_group')

    assert mock_client.driver is original_driver
    assert mock_client.clients is original_clients
    mock_client.driver.clone.assert_not_called()

    mock_client.add_episode_bulk.assert_called_once()
    kwargs = mock_client.add_episode_bulk.call_args.kwargs
    assert kwargs.get('group_id') == 'some_other_group'
```

Note: the fixture's `mock_client.driver._database` is `'test_group'` (set in the file's existing `mock_client` fixture) and this test passes `group_id='some_other_group'` — a different group_id from the driver's current database. This is deliberately the exact condition (`effective_group_id != client.driver._database`) that used to trigger the buggy mutation.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd mcp_server && uv run pytest tests/test_add_memory_bulk_params.py::test_does_not_mutate_shared_driver -v`
Expected: FAIL at `mock_client.driver.clone.assert_not_called()` — the fixture sets `mock_client.driver._database = 'test_group'`, the test passes `group_id='some_other_group'`, so today's code's `if effective_group_id != client.driver._database:` is true and calls `client.driver.clone(database='some_other_group')`.

- [ ] **Step 3: Remove the redundant mutation**

In `mcp_server/src/graphiti_mcp_server.py`, inside `add_memory_bulk`, find this exact block:

```python
        effective_group_id = normalize_group_id(group_id or config.graphiti.group_id)
        client = await graphiti_service.get_client()

        if effective_group_id != client.driver._database:
            client.driver = client.driver.clone(database=effective_group_id)
            client.clients.driver = client.driver

        raw_episodes = []
```

Replace it with:

```python
        effective_group_id = normalize_group_id(group_id or config.graphiti.group_id)
        client = await graphiti_service.get_client()

        raw_episodes = []
```

(Only the three-line `if` block is removed. `effective_group_id` is still used a few lines below, passed to `client.add_episode_bulk(group_id=effective_group_id, ...)` — that call is unchanged.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd mcp_server && uv run pytest tests/test_add_memory_bulk_params.py -v`
Expected: PASS (all tests in the file, including the 5 pre-existing ones and the new one — 6/6)

- [ ] **Step 5: Commit**

```bash
cd /Users/jtvv/Documents/AIMC/Fail-AIMC-Graphiti/vendor/graphiti
git add mcp_server/src/graphiti_mcp_server.py mcp_server/tests/test_add_memory_bulk_params.py
git commit -m "fix: stop add_memory_bulk from mutating the shared client driver"
```

---

### Task 2: Log vector-index-creation failures instead of swallowing them

**Files:**
- Modify: `graphiti_core/driver/falkordb_driver.py`
- Test: `tests/driver/test_falkordb_driver.py`

**Interfaces:**
- No new interfaces — `build_indices_and_constraints()`'s signature and return type (`None`) are unchanged; only its internal failure handling for the vector-index query changes from silent to logged.

- [ ] **Step 1: Write the failing test**

Add this test to the `TestFalkorDriver` class in `tests/driver/test_falkordb_driver.py`, after `test_delete_all_indexes` (reuses the class's `setup_method` fixture, which already provides `self.driver` with a mocked `client`):

```python
    @pytest.mark.asyncio
    @unittest.skipIf(not HAS_FALKORDB, 'FalkorDB is not installed')
    async def test_build_indices_and_constraints_logs_vector_index_failure(self):
        """A genuine failure creating the HNSW vector index is logged as a
        warning, not silently swallowed — see
        docs/superpowers/specs/2026-09-17-reindex-after-graph-delete-design.md.
        """
        async def fake_execute_query(query, **kwargs):
            if 'VECTOR INDEX' in query:
                raise Exception('dimension mismatch')
            return None

        self.driver.execute_query = AsyncMock(side_effect=fake_execute_query)

        with patch('graphiti_core.driver.falkordb_driver.logger') as mock_logger:
            await self.driver.build_indices_and_constraints()

        mock_logger.warning.assert_called_once()
        assert 'dimension mismatch' in str(mock_logger.warning.call_args)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/driver/test_falkordb_driver.py::TestFalkorDriver::test_build_indices_and_constraints_logs_vector_index_failure -v`
Expected: FAIL — `mock_logger.warning.assert_called_once()` fails because the current code's `with suppress(Exception)` discards the exception without ever calling `logger.warning`.

- [ ] **Step 3: Replace the blanket suppression with a logged warning**

In `graphiti_core/driver/falkordb_driver.py`, inside `build_indices_and_constraints`, find this exact block:

```python
        with suppress(Exception):
            await self.execute_query(vector_index_query)
```

Replace it with:

```python
        try:
            await self.execute_query(vector_index_query)
        except Exception as e:
            logger.warning(f'Failed to create HNSW vector index: {e}')
```

Note: `execute_query` (unchanged by this task) already special-cases the routine "already indexed" case — it catches that specific error, logs it at `info` level via `logger.info(f'Index already exists: {e}')`, and returns `None` without raising. So the `except` block added here only ever fires for genuine failures, not the normal idempotent-rerun case. If the `suppress` import (`from contextlib import suppress`) becomes unused after this change, check whether it's still used elsewhere in the file before removing the import — search with `grep -n suppress graphiti_core/driver/falkordb_driver.py`; only remove the import if this was its last use.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/driver/test_falkordb_driver.py::TestFalkorDriver -v`
Expected: PASS (all tests in the `TestFalkorDriver` class, including the new one)

- [ ] **Step 5: Run the full driver test file**

Run: `uv run pytest tests/driver/test_falkordb_driver.py -v`
Expected: all tests pass (confirms nothing else in the file broke, e.g. if the `suppress` import was removed and something else needed it)

- [ ] **Step 6: Commit**

```bash
cd /Users/jtvv/Documents/AIMC/Fail-AIMC-Graphiti/vendor/graphiti
git add graphiti_core/driver/falkordb_driver.py tests/driver/test_falkordb_driver.py
git commit -m "fix: log HNSW vector index creation failures instead of swallowing them"
```

---

### Task 3: Code review

- [ ] Run `superpowers:requesting-code-review` against the full diff on `fix/reindex-after-graph-delete` relative to `main`, covering both tasks above.
