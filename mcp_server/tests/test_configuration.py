#!/usr/bin/env python3
"""Test script for configuration loading and factory patterns."""

import asyncio
import os
import sys
from pathlib import Path

# Add the current directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from config.schema import GraphitiConfig
from services.factories import DatabaseDriverFactory, EmbedderFactory, LLMClientFactory


def test_config_loading():
    """Test loading configuration from YAML and environment variables."""
    print('Testing configuration loading...')

    # Test with default config.yaml
    config = GraphitiConfig()

    print('✓ Loaded configuration successfully')
    print(f'  - Server transport: {config.server.transport}')
    print(f'  - LLM provider: {config.llm.provider}')
    print(f'  - LLM model: {config.llm.model}')
    print(f'  - Embedder provider: {config.embedder.provider}')
    print(f'  - Database provider: {config.database.provider}')
    print(f'  - Group ID: {config.graphiti.group_id}')

    # Test environment variable override
    os.environ['LLM__PROVIDER'] = 'anthropic'
    os.environ['LLM__MODEL'] = 'claude-3-opus'
    config2 = GraphitiConfig()

    print('\n✓ Environment variable overrides work')
    print(f'  - LLM provider (overridden): {config2.llm.provider}')
    print(f'  - LLM model (overridden): {config2.llm.model}')

    # Clean up env vars
    del os.environ['LLM__PROVIDER']
    del os.environ['LLM__MODEL']

    assert config is not None
    assert config2 is not None

    # Return the first config for subsequent tests
    return config


def test_llm_factory(config: GraphitiConfig):
    """Test LLM client factory creation."""
    print('\nTesting LLM client factory...')

    # Test OpenAI client creation (if API key is set)
    if (
        config.llm.provider == 'openai'
        and config.llm.providers.openai
        and config.llm.providers.openai.api_key
    ):
        try:
            client = LLMClientFactory.create(config.llm)
            print(f'✓ Created {config.llm.provider} LLM client successfully')
            print(f'  - Model: {client.model}')
            print(f'  - Temperature: {client.temperature}')
        except Exception as e:
            print(f'✗ Failed to create LLM client: {e}')
    else:
        print(f'⚠ Skipping LLM factory test (no API key configured for {config.llm.provider})')

    # Test switching providers
    test_config = config.llm.model_copy()
    test_config.provider = 'gemini'
    if not test_config.providers.gemini:
        from config.schema import GeminiProviderConfig

        test_config.providers.gemini = GeminiProviderConfig(api_key='dummy_value_for_testing')
    else:
        test_config.providers.gemini.api_key = 'dummy_value_for_testing'

    try:
        client = LLMClientFactory.create(test_config)
        print('✓ Factory supports provider switching (tested with Gemini)')
    except Exception as e:
        print(f'✗ Factory provider switching failed: {e}')


def test_embedder_factory(config: GraphitiConfig):
    """Test Embedder client factory creation."""
    print('\nTesting Embedder client factory...')

    # Test OpenAI embedder creation (if API key is set)
    if (
        config.embedder.provider == 'openai'
        and config.embedder.providers.openai
        and config.embedder.providers.openai.api_key
    ):
        try:
            _ = EmbedderFactory.create(config.embedder)
            print(f'✓ Created {config.embedder.provider} Embedder client successfully')
            # The embedder client may not expose model/dimensions as attributes
            print(f'  - Configured model: {config.embedder.model}')
            print(f'  - Configured dimensions: {config.embedder.dimensions}')
        except Exception as e:
            print(f'✗ Failed to create Embedder client: {e}')
    else:
        print(
            f'⚠ Skipping Embedder factory test (no API key configured for {config.embedder.provider})'
        )


async def test_database_factory(config: GraphitiConfig):
    """Test Database driver factory creation."""
    print('\nTesting Database driver factory...')

    # Test Neo4j config creation
    if config.database.provider == 'neo4j' and config.database.providers.neo4j:
        try:
            db_config = DatabaseDriverFactory.create_config(config.database)
            print(f'✓ Created {config.database.provider} configuration successfully')
            print(f'  - URI: {db_config["uri"]}')
            print(f'  - User: {db_config["user"]}')
            print(
                f'  - Password: {"*" * len(db_config["password"]) if db_config["password"] else "None"}'
            )

            # Test actual connection would require initializing Graphiti
            from graphiti_core import Graphiti

            try:
                # This will fail if Neo4j is not running, but tests the config
                graphiti = Graphiti(
                    uri=db_config['uri'],
                    user=db_config['user'],
                    password=db_config['password'],
                )
                await graphiti.driver.client.verify_connectivity()
                print('  ✓ Successfully connected to Neo4j')
                await graphiti.driver.client.close()
            except Exception as e:
                print(f'  ⚠ Could not connect to Neo4j (is it running?): {type(e).__name__}')
        except Exception as e:
            print(f'✗ Failed to create Database configuration: {e}')
    else:
        print(f'⚠ Skipping Database factory test (no configuration for {config.database.provider})')


def test_cli_override():
    """Test CLI argument override functionality."""
    print('\nTesting CLI argument override...')

    # Simulate argparse Namespace
    class Args:
        config = Path('config.yaml')
        transport = 'stdio'
        host = '127.0.0.1'
        port = 9999
        llm_provider = 'anthropic'
        model = 'claude-3-sonnet'
        temperature = 0.5
        embedder_provider = 'voyage'
        embedder_model = 'voyage-3'
        database_provider = 'falkordb'
        group_id = 'test-group'
        user_id = 'test-user'

    config = GraphitiConfig()
    config.apply_cli_overrides(Args())

    assert config.server.host == '127.0.0.1', f'Expected host 127.0.0.1, got {config.server.host}'
    assert config.server.port == 9999, f'Expected port 9999, got {config.server.port}'

    print('✓ CLI overrides applied successfully')
    print(f'  - Transport: {config.server.transport}')
    print(f'  - Host: {config.server.host}')
    print(f'  - Port: {config.server.port}')
    print(f'  - LLM provider: {config.llm.provider}')
    print(f'  - LLM model: {config.llm.model}')
    print(f'  - Temperature: {config.llm.temperature}')
    print(f'  - Embedder provider: {config.embedder.provider}')
    print(f'  - Database provider: {config.database.provider}')
    print(f'  - Group ID: {config.graphiti.group_id}')
    print(f'  - User ID: {config.graphiti.user_id}')

    # Test that defaults are preserved when CLI flags are omitted
    class MinimalArgs:
        config = Path('config.yaml')
        transport = None
        host = None
        port = None
        llm_provider = None
        model = None
        temperature = None
        embedder_provider = None
        embedder_model = None
        database_provider = None
        group_id = None
        user_id = None

    default_config = GraphitiConfig()
    default_config.apply_cli_overrides(MinimalArgs())

    assert default_config.server.host == '0.0.0.0', 'Default host should be preserved'
    assert default_config.server.port == 8000, 'Default port should be preserved'
    print('✓ Defaults preserved when CLI flags are omitted')


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


def test_config_path_pointing_at_shared_file_reads_it_twice_harmlessly(tmp_path, monkeypatch):
    """When CONFIG_PATH already IS config.yaml (no separate local file), the
    shared source reads the same file a second time — same result, no error.
    """
    shared_yaml = tmp_path / 'config.yaml'
    shared_yaml.write_text(
        'server:\n  port: 7777\n'
        'graphiti:\n'
        '  entity_types:\n'
        '    - name: "Solo"\n'
        '      description: "Only file."\n'
    )
    monkeypatch.setenv('CONFIG_PATH', str(shared_yaml))

    config = GraphitiConfig()

    assert config.server.port == 7777
    assert len(config.graphiti.entity_types) == 1
    assert config.graphiti.entity_types[0].name == 'Solo'


async def main():
    """Run all tests.

    Note: the fixture-based tests added alongside the shared-config-source
    change (test_shared_config_source_merges_with_local,
    test_local_entity_types_win_over_shared,
    test_missing_shared_config_degrades_safely,
    test_config_path_pointing_at_shared_file_reads_it_twice_harmlessly) use
    the `tmp_path`/`monkeypatch` pytest fixtures and are not callable
    standalone, so they are not invoked here. Run `pytest` to exercise them.
    """
    print('=' * 60)
    print('Configuration and Factory Pattern Test Suite')
    print('=' * 60)

    try:
        # Test configuration loading
        config = test_config_loading()

        # Test factories
        test_llm_factory(config)
        test_embedder_factory(config)
        await test_database_factory(config)

        # Test CLI overrides
        test_cli_override()

        print('\n' + '=' * 60)
        print('✓ All tests completed successfully!')
        print('=' * 60)

    except Exception as e:
        print(f'\n✗ Test suite failed: {e}')
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
