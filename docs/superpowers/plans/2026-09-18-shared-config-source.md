# Shared Config Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `graphiti.entity_types` load from a single, git-tracked source instead of being duplicated by hand across each machine's gitignored `config-local.yaml`, so it can never silently drift between machines again.

**Architecture:** `pydantic-settings` already supports composing multiple config sources with a defined priority order (`settings_customise_sources`). Add a second, lower-priority `YamlSettingsSource` pointed at the git-tracked `config.yaml` sitting next to whichever file `CONFIG_PATH` names, so the local file's own values still win for anything it defines, while everything it omits (starting with `entity_types`) falls through to the shared file.

**Tech Stack:** Python, pydantic-settings, pytest (`tmp_path` + `monkeypatch` fixtures) — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-18-shared-config-source-design.md`

## Global Constraints

- Branch: `feat/shared-config-source` only. No commits to `main` without explicit approval.
- No new dependencies.
- Scope is `graphiti.entity_types` only — do not extend the shared source to other config sections in this branch.
- Do not remove or restructure `YamlSettingsSource` — reuse it as-is for the second source.
- `config/config.yaml` is not modified by this branch.
- The Mac must be verified working before pioneer10 is touched — no skipping straight to production.

---

### Task 1: Add the shared config source + unit tests

**Files:**
- Modify: `mcp_server/src/config/schema.py:303-316` (`GraphitiConfig.settings_customise_sources`)
- Test: `mcp_server/tests/test_configuration.py` (append)

**Interfaces:**
- Consumes: the existing `YamlSettingsSource` class (`schema.py:16-73`) — unchanged, reused as-is with a second instance.
- Produces: no new public interface. `GraphitiConfig()` instances now resolve `graphiti.entity_types` from the shared `config.yaml` whenever the machine-local file doesn't define it.

- [ ] **Step 1: Write the three failing/characterizing tests**

Append to `mcp_server/tests/test_configuration.py` (after the existing `test_cli_override` function, before `async def main():`):

```python
def test_shared_config_source_merges_with_local(tmp_path, monkeypatch):
    """Local and shared YAML sources combine into one config — the shared
    file's entity_types survive alongside the local file's own settings,
    instead of one source replacing the other's `graphiti` block wholesale.
    """
    local_yaml = tmp_path / 'config-local.yaml'
    local_yaml.write_text('server:\n  port: 9999\n')
    shared_yaml = tmp_path / 'config.yaml'
    shared_yaml.write_text(
        'graphiti:\n'
        '  entity_types:\n'
        '    - name: "Person"\n'
        '      description: "A human."\n'
    )
    monkeypatch.setenv('CONFIG_PATH', str(local_yaml))

    config = GraphitiConfig()

    assert config.server.port == 9999
    assert len(config.graphiti.entity_types) == 1
    assert config.graphiti.entity_types[0].name == 'Person'


def test_local_entity_types_win_over_shared(tmp_path, monkeypatch):
    """When both the local and shared files define entity_types, the local
    (higher-priority) one wins entirely — proves the priority order is not
    accidentally reversed. This test already passes before Task 1's Step 3
    change too (today's code only ever reads the local file), but it must
    keep passing afterward — it is the regression test for priority order,
    not a RED test for this change.
    """
    local_yaml = tmp_path / 'config-local.yaml'
    local_yaml.write_text(
        'graphiti:\n'
        '  entity_types:\n'
        '    - name: "LocalOnly"\n'
        '      description: "From the local file."\n'
    )
    shared_yaml = tmp_path / 'config.yaml'
    shared_yaml.write_text(
        'graphiti:\n'
        '  entity_types:\n'
        '    - name: "SharedOnly"\n'
        '      description: "From the shared file."\n'
    )
    monkeypatch.setenv('CONFIG_PATH', str(local_yaml))

    config = GraphitiConfig()

    assert len(config.graphiti.entity_types) == 1
    assert config.graphiti.entity_types[0].name == 'LocalOnly'


def test_missing_shared_config_degrades_safely(tmp_path, monkeypatch):
    """No config.yaml sibling present: construction still succeeds and
    entity_types falls back to the model's own default (empty list) instead
    of raising. Also already passes before Task 1's Step 3 change (today
    there is no second source to go missing), but must keep passing after —
    it is the regression test for the "missing shared file" row of the
    spec's Error Handling table, not a RED test for this change.
    """
    local_yaml = tmp_path / 'config-local.yaml'
    local_yaml.write_text('server:\n  port: 8888\n')
    monkeypatch.setenv('CONFIG_PATH', str(local_yaml))
    # Deliberately no tmp_path / 'config.yaml' — the shared file is absent.

    config = GraphitiConfig()

    assert config.server.port == 8888
    assert config.graphiti.entity_types == []
```

- [ ] **Step 2: Run the tests to verify the expected state before the change**

Run: `cd mcp_server && uv run pytest tests/test_configuration.py -v -k "shared_config_source or local_entity_types_win or missing_shared_config"`

Expected: `test_shared_config_source_merges_with_local` **FAILS** — today's `settings_customise_sources` has only one YAML source (the local file), so `config.graphiti.entity_types` stays at its default `[]` instead of picking up `Person` from the shared file; the assertion `len(config.graphiti.entity_types) == 1` fails.

`test_local_entity_types_win_over_shared` and `test_missing_shared_config_degrades_safely` **PASS** already — today's code only ever reads the local file, so "local wins" and "missing shared file doesn't crash" both hold trivially without any change yet. This is expected and correct, not a sign anything is wrong; Step 4 re-runs all three to confirm they *still* pass once Step 3 adds the real second source.

- [ ] **Step 3: Add the second YAML source**

In `mcp_server/src/config/schema.py`, find this exact block:

```python
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Customize settings sources to include YAML."""
        config_path = Path(os.environ.get('CONFIG_PATH', 'config/config.yaml'))
        yaml_settings = YamlSettingsSource(settings_cls, config_path)
        # Priority: CLI args (init) > env vars > yaml > defaults
        return (init_settings, env_settings, yaml_settings, dotenv_settings)
```

Replace it with:

```python
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Customize settings sources to include YAML."""
        config_path = Path(os.environ.get('CONFIG_PATH', 'config/config.yaml'))
        local_settings = YamlSettingsSource(settings_cls, config_path)
        # Shared, git-tracked config living alongside whichever file CONFIG_PATH
        # points at — e.g. config-local.yaml's sibling config.yaml. Lower priority
        # than local_settings, so a machine-specific override (should one ever be
        # needed) still wins; contributes nothing if it doesn't exist
        # (YamlSettingsSource already returns {} for a missing file) or if
        # config_path already IS config.yaml (reads the same file twice, harmless).
        shared_settings = YamlSettingsSource(settings_cls, config_path.parent / 'config.yaml')
        # Priority: CLI args (init) > env vars > local yaml > shared yaml > defaults
        return (init_settings, env_settings, local_settings, shared_settings, dotenv_settings)
```

- [ ] **Step 4: Run the tests to verify they all pass**

Run: `cd mcp_server && uv run pytest tests/test_configuration.py -v -k "shared_config_source or local_entity_types_win or missing_shared_config"`
Expected: PASS (all three)

- [ ] **Step 5: Run the full configuration test file**

Run: `cd mcp_server && uv run pytest tests/test_configuration.py -v`
Expected: all tests pass, including the pre-existing `test_config_loading` and `test_cli_override` — confirms this change didn't disturb the existing tests that build a real (non-`tmp_path`) `GraphitiConfig()`.

- [ ] **Step 6: Commit**

```bash
cd /Users/jtvv/Documents/AIMC/Fail-AIMC-Graphiti/vendor/graphiti
git add mcp_server/src/config/schema.py mcp_server/tests/test_configuration.py
git commit -m "feat: load entity_types from a shared config source"
```

---

### Task 2: Verify on the Mac before touching pioneer10

**Files:**
- Modify: `/Users/jtvv/Documents/AIMC/Fail-AIMC-Graphiti/config/config-local.yaml` (Mac's copy — gitignored, not part of this git repo's history, edited directly)

**Interfaces:**
- Consumes: Task 1's `settings_customise_sources` change (already merged into this branch's working tree).
- Produces: nothing new tasks depend on — this task is a verification gate. Task 3 must not start until this task's Step 4 passes.

No dedicated automated test: this task is the spec's required manual verification, run against the real local files, not synthetic `tmp_path` fixtures. Do not skip it or treat Task 1's unit tests as a substitute — those prove the merge *logic*; this proves the *real* `config-local.yaml` and `config.yaml` on this specific machine merge correctly and the server actually starts.

- [ ] **Step 1: Confirm the current `entity_types` in the shared config**

Run: `grep -c "^\s*- name:" /Users/jtvv/Documents/AIMC/Fail-AIMC-Graphiti/config/config.yaml`
Expected: `13` (the current count of entity types in the git-tracked shared file — record this number, Step 4 compares against it).

- [ ] **Step 2: Remove `entity_types` from the Mac's local config**

`entity_types:` is the last key in the file today (nothing follows its list), so deleting from that line to end-of-file removes exactly the block and nothing else:

```bash
sed -i '' '/^[[:space:]]*entity_types:/,$d' /Users/jtvv/Documents/AIMC/Fail-AIMC-Graphiti/config/config-local.yaml
```

(macOS `sed` needs the empty `-i ''` — omitting it, or using bare `-i`, is the GNU-sed syntax and will error on macOS. Also use `[[:space:]]`, not `\s` — BSD/macOS `sed` doesn't support `\s` as whitespace; a `\s` pattern silently matches nothing instead of erroring, so the command exits 0 having deleted nothing. Confirmed by hitting exactly this on the real file during execution: `\s*entity_types:` left the file untouched with no error, `[[:space:]]*entity_types:` correctly deleted the block.)

Verify: `tail -5 /Users/jtvv/Documents/AIMC/Fail-AIMC-Graphiti/config/config-local.yaml` should now end with the `graphiti: group_id:` line (or whatever key preceded `entity_types:`), not an entity list. Every other key (`server`, `llm`, `embedder`, `database`) is untouched — the command only ever deletes from `entity_types:` onward.

- [ ] **Step 3: Verify the resolved `entity_types` directly, without starting the full server**

`GraphitiConfig()` instantiation is a pure config-parsing step — no network calls, no FalkorDB or LLM credentials needed — so it can be checked in isolation, faster and with a clearer failure signal than starting the whole server and hunting for a log line (nothing today logs the resolved entity types at startup):

```bash
cd /Users/jtvv/Documents/AIMC/Fail-AIMC-Graphiti/vendor/graphiti/mcp_server
CONFIG_PATH=/Users/jtvv/Documents/AIMC/Fail-AIMC-Graphiti/config/config-local.yaml uv run python -c "
import sys
sys.path.insert(0, 'src')
from config.schema import GraphitiConfig
config = GraphitiConfig()
names = [e.name for e in config.graphiti.entity_types]
print(f'{len(names)} entity types: {names}')
assert len(names) == 13, f'expected 13, got {len(names)}'
assert 'Service' in names, 'Service type missing — shared config.yaml may not be the current one'
print('OK')
"
```

Expected: prints `13 entity types: [...]` listing all 13 names, includes `Service` (added to `config.yaml` earlier today — its presence specifically proves the *current* shared file was read, not a cached or old copy), and ends with `OK`.

If the count is `0`: Step 2 likely also removed something it shouldn't have, or the shared path resolution in Task 1 Step 3 is wrong for this real directory layout — stop and re-examine before proceeding, do not guess a fix.

If the count is correct but `Service` is missing: a different `config.yaml` than expected is being read (e.g. a stale copy elsewhere) — verify which file was actually loaded before proceeding.

- [ ] **Step 4: Start the real server briefly to confirm it boots**

Step 3 proves the config layer resolves correctly in isolation; this step proves nothing else about server startup broke (e.g. a YAML syntax slip while editing Step 2's block deletion):

```bash
cd /Users/jtvv/Documents/AIMC/Fail-AIMC-Graphiti/vendor/graphiti/mcp_server
timeout 15 uv run main.py --config /Users/jtvv/Documents/AIMC/Fail-AIMC-Graphiti/config/config-local.yaml
```

Expected: startup log lines, no traceback, no exit before the 15-second timeout kills it (a crash-on-startup would exit immediately instead of running until the timeout).

No commit for this task — `config-local.yaml` is gitignored; there is nothing to commit in this repo. The only durable artifact is the passing verification itself, which the next task's dispatch will reference.

---

### Task 3: Apply the same change to pioneer10

**Files:**
- Modify: `config-local.yaml` on pioneer10 (`~/AIMC/Fail-AIMC-Graphiti/config/config-local.yaml`, via SSH — gitignored, not part of this git repo's history)

**Interfaces:**
- Consumes: Task 2 having passed (Mac verified working) — do not start this task otherwise.
- Produces: nothing further tasks depend on.

This task is deliberately separate from Task 2 — production is only touched after the Mac has already proven the change safe. Do not combine these tasks even if Task 2 passes cleanly.

- [ ] **Step 1: Pull this branch's commits onto pioneer10**

The code change (Task 1) lives in `vendor/graphiti` on branch `feat/shared-config-source`, which has not been merged to `main` yet. pioneer10's checkout of `vendor/graphiti` needs to be on this branch to pick up Task 1's `schema.py` change before Step 3 of this task can use it:

```bash
ssh pioneer10 "cd ~/AIMC/Fail-AIMC-Graphiti/vendor/graphiti && git fetch origin && git checkout feat/shared-config-source && git pull origin feat/shared-config-source && git log -1 --oneline"
```

Expected: the log line shows Task 1's commit (`feat: load entity_types from a shared config source`) as HEAD.

- [ ] **Step 2: Confirm the current `entity_types` count in pioneer10's shared config**

Run: `ssh pioneer10 "grep -c '^\s*- name:' ~/AIMC/Fail-AIMC-Graphiti/config/config.yaml"`
Expected: `13` (same as Task 2 Step 1 — `config.yaml` is git-tracked and already synced to `main` on pioneer10, unaffected by this branch).

- [ ] **Step 3: Remove `entity_types` from pioneer10's local config**

Same reasoning as Task 2 Step 2 — `entity_types:` is the last key in the file, so this removes exactly the block and nothing else (pioneer10 uses GNU sed via a normal Linux shell, so no `-i ''` quirk here):

```bash
ssh pioneer10 "sed -i '/^\s*entity_types:/,\$d' ~/AIMC/Fail-AIMC-Graphiti/config/config-local.yaml"
```

Verify: `ssh pioneer10 "tail -5 ~/AIMC/Fail-AIMC-Graphiti/config/config-local.yaml"` should now end with the `graphiti: group_id:` line (or whatever key preceded `entity_types:`), not an entity list. Leaves `server`, `llm`, `embedder`, `database` untouched — do not otherwise "fix" this file's unquoted-scalar YAML style (differs from the Mac's quoted style; that's a pre-existing, unrelated cosmetic difference, out of scope here).

- [ ] **Step 4: Restart the MCP server on pioneer10**

```bash
ssh pioneer10 "systemctl --user restart aimc-graphiti && sleep 2 && systemctl --user status aimc-graphiti --no-pager -l | head -8"
```

Expected: `Active: active (running)`, no restart loop, no error in the status output.

- [ ] **Step 5: Confirm `entity_types` resolved correctly on pioneer10**

Same direct check as Task 2 Step 3, run over SSH instead of locally:

```bash
ssh pioneer10 "cd ~/AIMC/Fail-AIMC-Graphiti/vendor/graphiti/mcp_server && CONFIG_PATH=~/AIMC/Fail-AIMC-Graphiti/config/config-local.yaml uv run python -c \"
import sys
sys.path.insert(0, 'src')
from config.schema import GraphitiConfig
config = GraphitiConfig()
names = [e.name for e in config.graphiti.entity_types]
print(f'{len(names)} entity types: {names}')
assert len(names) == 13, f'expected 13, got {len(names)}'
assert 'Service' in names, 'Service type missing — shared config.yaml may not be the current one'
print('OK')
\""
```

Expected: same as Task 2 Step 3 — `13 entity types: [...]` including `Service`, ending in `OK`. The service restarted cleanly in Step 4 is corroborating evidence the server itself boots; this step confirms specifically that *this* process resolved the correct, current entity types.

If this step fails: pioneer10 is now running with the code from Task 1 but pointing at a `config-local.yaml` missing `entity_types` and (per the failure) not correctly falling back to the shared file. Do not leave it in this state — restore pioneer10's `config-local.yaml` `entity_types` block from `config/config.yaml`'s current content (same 13 entries) as an immediate rollback, restart the service again, and only then investigate Task 1's shared-path derivation against pioneer10's actual directory layout.

---

### Task 4: Code review

- [ ] Run `superpowers:requesting-code-review` against the full diff on `feat/shared-config-source` relative to `main`, covering Task 1 (the only code change — Tasks 2 and 3 touch gitignored files outside this repo's history and have no diff to review).
