# Shared Config Source — Design

## Goal

Branch `feat/shared-config-source` (never `main` until reviewed). Stop `graphiti.entity_types` from
being able to silently drift between machines. Today it lives duplicated inside each machine's
gitignored `config/config-local.yaml` (one independent copy on the Mac, one on pioneer10), with no
way to diff the two copies against each other — a change to one can go unnoticed on the other
indefinitely.

This is a deliberately low-urgency, high-caution change: no real drift has occurred yet (today's
entity-type fix landed identically on both machines). The task exists because the investigator
almost reported a false drift by being unable to compare the two gitignored files reliably, which
exposed the underlying risk. Given that, this spec favors safety and reversibility over elegance —
see "Approaches Considered" for the alternative that was seriously considered and explicitly not
chosen for a first attempt at making this "the right way."

## Root Cause

`config/config-local.yaml` (Fail-AIMC-Graphiti, gitignored, one independent copy per machine) mixes
two kinds of settings that have no business being in the same un-versioned file:

1. **Genuinely per-machine**: `server.port` (8001 on the Mac, 8002 on pioneer10, due to a port
   conflict), `database.providers.falkordb` host/port, whether `llm.small_model` is enabled.
2. **Product logic that must be identical everywhere**: `graphiti.entity_types` — 13 entries, each
   a `name` + free-text `description`, defining the entire entity-extraction taxonomy for every
   group in the system.

The MCP server (`vendor/graphiti/mcp_server/src/config/schema.py`) loads config via
`pydantic-settings`. `GraphitiConfig.settings_customise_sources()` (`schema.py:304`) returns
`(init_settings, env_settings, yaml_settings, dotenv_settings)`, with exactly **one** YAML source —
`YamlSettingsSource`, pointed at whatever path `CONFIG_PATH` holds. `CONFIG_PATH` is set from the
`--config` CLI flag (`graphiti_mcp_server.py:1500-1501`). There is no second source and no merge
between a "shared" and a "local" file — each machine's `config-local.yaml` is a fully independent,
hand-copied snapshot of the whole schema.

There is also a git-tracked `config/config.yaml`, which already holds the correct, current
`entity_types` list (it was the source both machines were manually copied from) — but nothing
enforces that the copies stay in sync; it is discipline only.

## Approaches Considered

### Approach E — Stop gitignoring the per-machine files (considered, not chosen for this branch)

Track `config-mac.yaml` / `config-pioneer10.yaml` directly in git instead of gitignoring them.
Nothing in `config-local.yaml` today is a real secret (API keys are `${VAR}` references, never
literal values) — only ports and hostnames, which are not sensitive. This would make drift
*visible* (any PR touching one file's `entity_types` block would show it in review) without
touching the config-loading code at all.

- **Pro:** zero code risk — nothing about server startup changes. The fastest, safest way to make
  drift visible instead of invisible, which is the actual problem that almost bit us today.
- **Con:** does not eliminate the duplication itself, only makes it reviewable. `entity_types` would
  still be typed twice; a careless PR could still edit one and miss the other, just with a better
  chance of being caught in review instead of never.
- **Explicitly discussed with the user and not chosen**: the user has code-quality reasons for
  preferring a single source of truth over "duplication that's merely visible," and accepted the
  larger, riskier change to get it. Recorded here so a future reader knows this was a deliberate
  trade-off, not an oversight — if Approach A ever proves too risky in practice, E is the fallback.

### Approach 0 — Documentation/discipline only (rejected)

Leave the code and file layout as-is; just document more clearly that `config.yaml` is the source
of truth to copy from by hand. Rejected: this is exactly the discipline that already almost failed
today — the investigator could not reliably tell whether the two gitignored copies matched, and
came within one incorrect report of "fixing" a drift that did not exist. Documentation does not
close a gap that exists because the two files are structurally incomparable.

### Approach A — A second, lower-priority YAML source via `pydantic-settings` (chosen)

`pydantic-settings`'s `settings_customise_sources` is the framework's own designed extension point
for exactly this: composing multiple config sources with a defined priority order. Verified by
reading the installed library's source, not assumed:

- The merge across all returned sources is a genuine **recursive deep merge**
  (`pydantic/_internal/_utils.py:131`, function `deep_update`) — it merges nested dict keys instead
  of one source's dict wholesale-replacing another's. Confirmed this is not a shallow
  `dict.update()`: if source A provides `graphiti.entity_types` and source B provides
  `graphiti.group_id`, both survive in the final merged `graphiti` block.
- Priority order is confirmed by reading `pydantic_settings/main.py` (`_settings_build_values`,
  around line 427): sources **earlier** in the tuple returned by `settings_customise_sources` win
  over later ones for any key both provide.
- `YamlSettingsSource.__call__` (`schema.py:64-73`) already returns `{}` when its `config_path`
  does not exist, rather than raising — a missing shared file degrades to "contributes nothing,"
  not a crash. This is existing behavior, unchanged by this design.

Chosen because it is the only option that makes `entity_types` live in exactly one place — the
duplication itself stops existing, not just its visibility. Accepted trade-off: it is the only
approach that touches the code path the whole server's startup depends on, which is why this spec
exists at all and why the testing section below is unusually deliberate for a change this small.

## Design

### D.1 — Add a second `YamlSettingsSource` in `settings_customise_sources`

In `vendor/graphiti/mcp_server/src/config/schema.py`, `GraphitiConfig.settings_customise_sources`
(currently `schema.py:303-316`):

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
    # Shared, git-tracked config living alongside whichever file CONFIG_PATH points at —
    # e.g. config-local.yaml's sibling config.yaml. Lower priority than local_settings, so a
    # machine-specific override (should one ever be needed) still wins; contributes nothing
    # if it doesn't exist (YamlSettingsSource already returns {} for a missing file) or if
    # config_path already IS config.yaml (reads the same file twice, harmless).
    shared_settings = YamlSettingsSource(settings_cls, config_path.parent / 'config.yaml')
    # Priority: CLI args (init) > env vars > local yaml > shared yaml > defaults
    return (init_settings, env_settings, local_settings, shared_settings, dotenv_settings)
```

Only the body of this one method changes. No other function, class, or call site in `schema.py` is
touched.

### D.2 — Remove `entity_types` from both machines' `config-local.yaml`

`Fail-AIMC-Graphiti/config/config-local.yaml` (gitignored, edited directly on each machine, not via
PR):

- Mac: delete the `entity_types` block (currently listing all 13 types).
- pioneer10: same, via SSH.

After this, both machines resolve `entity_types` exclusively from the git-tracked `config.yaml`
(D.1's `shared_settings` source). `config-local.yaml` on both machines keeps only what's genuinely
per-machine: `server.port`, `database.providers.falkordb` host/port, the `small_model` line.

### D.3 — Nothing else changes

`config/config.yaml` already has the correct 13 entity types (confirmed current as of today's
Location/Service fix) — it is not modified by this branch. No other config section
(`server`/`llm`/`embedder`/`database`) is touched; this design is scoped to `graphiti.entity_types`
only, per D.2. Extending the shared source to cover other fields is a future decision, not part of
this change.

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Shared `config.yaml` missing at the derived path | `YamlSettingsSource.__call__` returns `{}` for that source (existing behavior, unchanged) — `entity_types` falls back to the Pydantic model's own default (empty list, per `GraphitiAppConfig`), not a crash. Verified manually as part of D.4 before touching pioneer10. |
| `config_path` already points directly at `config.yaml` (no separate local file) | `shared_settings` reads the same file a second time; identical result, no conflict. |
| Someone leaves `entity_types` in a machine's `config-local.yaml` by mistake (e.g. this migration's step D.2 is skipped for one machine) | Local wins over shared per the priority order (D.1) — that machine silently keeps using its own (possibly stale) list instead of the shared one. This is the one way the fix can be silently defeated; the manual verification in D.4 explicitly checks for this by diffing the resolved `entity_types` count/content against `config.yaml`, not just checking that the server starts. |
| Shared `config.yaml` has a YAML syntax error | Same as today's single-source failure mode — `yaml.safe_load` raises, `GraphitiConfig()` construction fails, server does not start. Not a new risk introduced by this change; unchanged from the current single-file behavior. |

---

## Testing

### Unit tests (required) — `vendor/graphiti/mcp_server/tests/test_configuration.py`

Follow the file's existing style (see `test_config_loading`, which builds a real `GraphitiConfig()`
and asserts on it) but use `tmp_path` + `monkeypatch` for isolation instead of relying on the repo's
real `config/config.yaml`, since these tests must not be coupled to — or broken by — future edits to
the real entity-type list.

1. **Merge combines both sources**: write a temp "local" YAML with only `server.port` set and a
   temp "shared" YAML (named `config.yaml`, same temp directory) with only `graphiti.entity_types`
   set. Point `CONFIG_PATH` at the local file. Assert the resulting `GraphitiConfig()` has both the
   local port AND the shared entity types — proves the deep merge actually combines sub-keys of the
   same nested block instead of one file replacing the other's `graphiti` section wholesale.
2. **Local wins on conflict**: both temp files define `graphiti.entity_types` (different lists).
   Assert the resolved config uses the *local* file's list — proves the priority order in D.1 is
   correct, not accidentally reversed.
3. **Missing shared file degrades safely**: local file exists, no `config.yaml` sibling present.
   Assert `GraphitiConfig()` still constructs without raising (entity_types falls back to the
   model's default, empty list) — proves the "missing shared file" row of the Error Handling table.

### Manual verification (required before touching pioneer10)

Run on the Mac first, after D.1's code change and D.2's local-file edit are both in place:

1. Start the MCP server locally (`uv run main.py --config config/config-local.yaml`, matching how
   it's actually invoked in production).
2. Confirm it starts without error.
3. Confirm the resolved `entity_types` count and content match `config/config.yaml`'s 13 entries
   exactly (not empty, not the machine's old local copy if one was accidentally left behind — this
   is the check that specifically catches the "local file still has entity_types" failure mode from
   the Error Handling table).

Only after this passes on the Mac: apply D.2's `config-local.yaml` edit on pioneer10 and repeat step
2-3 there before considering the branch done.

---

## Global Constraints

- Branch: `feat/shared-config-source` only. No commits to `main` without explicit approval.
- No new dependencies — `pydantic-settings`'s existing multi-source mechanism is sufficient.
- Scope is `graphiti.entity_types` only (D.3) — do not extend the shared source to other config
  sections as part of this branch.
- Do not remove or restructure `YamlSettingsSource` itself — reuse it as-is for the second source.
- `config/config.yaml`'s content is not modified by this branch.
- Mac must be verified (D.4) before pioneer10 is touched — no skipping straight to production.
