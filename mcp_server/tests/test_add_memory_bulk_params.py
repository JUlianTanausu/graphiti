#!/usr/bin/env python3
"""Tests for add_memory_bulk new parameters:
custom_extraction_instructions, excluded_entity_types, saga, and
per-episode episode_metadata.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import graphiti_mcp_server as mod


@pytest.fixture
def mock_client():
    client = MagicMock()
    result = MagicMock()
    result.episodes = []
    result.nodes = []
    result.edges = []
    client.add_episode_bulk = AsyncMock(return_value=result)
    client.driver = MagicMock()
    client.driver._database = 'test_group'
    client.driver.clone = MagicMock(return_value=client.driver)
    client.clients = MagicMock()
    return client


@pytest.fixture
def mock_service(mock_client):
    service = MagicMock()
    service.get_client = AsyncMock(return_value=mock_client)
    service.entity_types = {}
    service.edge_types = {}
    service.edge_type_map = {}
    return service


_BASE_EP = {
    'name': 'ep1',
    'episode_body': 'some content',
    'reference_time': '2026-09-16T00:00:00Z',
    'source_description': 'test source',
    'source': 'text',
}


_MOCK_CONFIG = MagicMock(graphiti=MagicMock(group_id='test_group'))


async def test_custom_extraction_instructions_passed_through(mock_service, mock_client):
    """custom_extraction_instructions is forwarded to add_episode_bulk."""
    with (
        patch.object(mod, 'graphiti_service', mock_service),
        patch.object(mod, 'config', _MOCK_CONFIG, create=True),
    ):
        await mod.add_memory_bulk(
            episodes=[_BASE_EP],
            custom_extraction_instructions='Extract only names.',
        )

    mock_client.add_episode_bulk.assert_called_once()
    kwargs = mock_client.add_episode_bulk.call_args.kwargs
    assert kwargs.get('custom_extraction_instructions') == 'Extract only names.'


async def test_excluded_entity_types_passed_through(mock_service, mock_client):
    """excluded_entity_types is forwarded to add_episode_bulk."""
    with (
        patch.object(mod, 'graphiti_service', mock_service),
        patch.object(mod, 'config', _MOCK_CONFIG, create=True),
    ):
        await mod.add_memory_bulk(
            episodes=[_BASE_EP],
            excluded_entity_types=['Person', 'Location'],
        )

    mock_client.add_episode_bulk.assert_called_once()
    kwargs = mock_client.add_episode_bulk.call_args.kwargs
    assert kwargs.get('excluded_entity_types') == ['Person', 'Location']


async def test_saga_passed_through(mock_service, mock_client):
    """saga is forwarded to add_episode_bulk."""
    with (
        patch.object(mod, 'graphiti_service', mock_service),
        patch.object(mod, 'config', _MOCK_CONFIG, create=True),
    ):
        await mod.add_memory_bulk(
            episodes=[_BASE_EP],
            saga='my_saga',
        )

    mock_client.add_episode_bulk.assert_called_once()
    kwargs = mock_client.add_episode_bulk.call_args.kwargs
    assert kwargs.get('saga') == 'my_saga'


async def test_episode_metadata_per_episode(mock_service, mock_client):
    """episode_metadata from each episode dict is passed to RawEpisode."""
    ep_with_meta = {**_BASE_EP, 'episode_metadata': {'author': 'alice', 'priority': 1}}
    with (
        patch.object(mod, 'graphiti_service', mock_service),
        patch.object(mod, 'config', _MOCK_CONFIG, create=True),
    ):
        await mod.add_memory_bulk(episodes=[ep_with_meta])

    mock_client.add_episode_bulk.assert_called_once()
    kwargs = mock_client.add_episode_bulk.call_args.kwargs
    raw_episodes = kwargs.get('bulk_episodes')
    assert raw_episodes is not None and len(raw_episodes) == 1
    assert raw_episodes[0].episode_metadata == {'author': 'alice', 'priority': 1}


async def test_episode_metadata_defaults_to_none(mock_service, mock_client):
    """episode_metadata is None when not provided in an episode dict."""
    with (
        patch.object(mod, 'graphiti_service', mock_service),
        patch.object(mod, 'config', _MOCK_CONFIG, create=True),
    ):
        await mod.add_memory_bulk(episodes=[_BASE_EP])

    mock_client.add_episode_bulk.assert_called_once()
    kwargs = mock_client.add_episode_bulk.call_args.kwargs
    raw_episodes = kwargs.get('bulk_episodes')
    assert raw_episodes is not None and len(raw_episodes) == 1
    assert raw_episodes[0].episode_metadata is None


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
