"""
Copyright 2024, Zep Software, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

# Regression/feature tests for _extract_and_resolve_edges accepting
# already-extracted edges (the combined-extraction path for add_episode),
# instead of always calling extract_edges() itself.
#
# These tests are database-free: they exercise Graphiti._extract_and_resolve_edges
# directly with a fake driver and mocked LLM-facing helpers.

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.driver.driver import GraphDriver, GraphProvider
from graphiti_core.edges import EntityEdge
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.graphiti import Graphiti
from graphiti_core.graphiti_types import GraphitiClients
from graphiti_core.llm_client import LLMClient
from graphiti_core.nodes import EntityNode, EpisodeType, EpisodicNode
from graphiti_core.tracer import Tracer

pytest_plugins = ('pytest_asyncio',)


class FakeDriver(GraphDriver):
    provider = GraphProvider.NEO4J

    def __init__(self, database: str = 'default_db'):
        self._database = database

    def clone(self, database: str) -> 'FakeDriver':
        return FakeDriver(database=database)

    async def execute_query(self, cypher_query_: str, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError

    def session(self, database: str | None = None):  # pragma: no cover
        raise NotImplementedError

    def close(self):  # pragma: no cover
        raise NotImplementedError

    def delete_all_indexes(self):  # pragma: no cover
        raise NotImplementedError

    async def build_indices_and_constraints(self, delete_existing: bool = False):  # pragma: no cover
        raise NotImplementedError


def _make_graphiti() -> Graphiti:
    driver = FakeDriver()
    clients = GraphitiClients(  # type: ignore[call-arg]
        driver=driver,
        llm_client=Mock(spec=LLMClient),
        embedder=Mock(spec=EmbedderClient),
        cross_encoder=Mock(spec=CrossEncoderClient),
        tracer=Mock(spec=Tracer),
    )
    graphiti = Graphiti.__new__(Graphiti)
    graphiti.driver = driver
    graphiti.clients = clients
    return graphiti


def _episode() -> EpisodicNode:
    return EpisodicNode(
        name='ep',
        group_id='test',
        labels=[],
        source=EpisodeType.text,
        content='hello',
        source_description='',
        created_at=datetime.now(timezone.utc),
        valid_at=datetime.now(timezone.utc),
    )


def _node(name: str) -> EntityNode:
    return EntityNode(name=name, group_id='test', labels=['Entity'])


@pytest.mark.asyncio
async def test_pre_extracted_edges_skip_extract_edges_call():
    """When extracted_edges is supplied, extract_edges() must never be called —
    this is the combined-extraction path where nodes+edges already came from a
    single prior LLM call."""
    graphiti = _make_graphiti()
    episode = _episode()
    node_a, node_b = _node('a'), _node('b')
    pre_extracted = [
        EntityEdge(
            source_node_uuid=node_a.uuid,
            target_node_uuid=node_b.uuid,
            name='RELATES_TO',
            fact='a relates to b',
            group_id='test',
            created_at=datetime.now(timezone.utc),
        )
    ]

    with (
        patch('graphiti_core.graphiti.extract_edges', new_callable=AsyncMock) as mock_extract,
        patch(
            'graphiti_core.graphiti.resolve_extracted_edges',
            new_callable=AsyncMock,
            return_value=(pre_extracted, [], pre_extracted),
        ) as mock_resolve,
    ):
        resolved, invalidated, new = await graphiti._extract_and_resolve_edges(
            episode=episode,
            extracted_nodes=[node_a, node_b],
            previous_episodes=[],
            edge_type_map={},
            group_id='test',
            edge_types=None,
            nodes=[node_a, node_b],
            uuid_map={},
            extracted_edges=pre_extracted,
        )

    mock_extract.assert_not_called()
    mock_resolve.assert_awaited_once()
    assert resolved == pre_extracted
    assert new == pre_extracted
    assert invalidated == []


@pytest.mark.asyncio
async def test_no_pre_extracted_edges_calls_extract_edges_as_before():
    """Without extracted_edges, behavior is unchanged: extract_edges() runs."""
    graphiti = _make_graphiti()
    episode = _episode()
    node_a = _node('a')

    with (
        patch(
            'graphiti_core.graphiti.extract_edges', new_callable=AsyncMock, return_value=[]
        ) as mock_extract,
        patch(
            'graphiti_core.graphiti.resolve_extracted_edges',
            new_callable=AsyncMock,
            return_value=([], [], []),
        ) as mock_resolve,
    ):
        await graphiti._extract_and_resolve_edges(
            episode=episode,
            extracted_nodes=[node_a],
            previous_episodes=[],
            edge_type_map={},
            group_id='test',
            edge_types=None,
            nodes=[node_a],
            uuid_map={},
        )

    mock_extract.assert_awaited_once()
    mock_resolve.assert_awaited_once()
