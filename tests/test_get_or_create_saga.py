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

# Regression test for Graphiti._get_or_create_saga: when a saga already
# exists, its lookup query returned only uuid/name/group_id/created_at,
# silently dropping summary/first_episode_uuid/last_episode_uuid/
# last_summarized_at/last_summarized_episode_valid_at. Since add_episode()
# then calls saga_node.save() on that incomplete object at the end of every
# episode added to the saga, each new episode wiped the saga's prior
# summary and bookkeeping fields back to their defaults.
#
# Database-free: exercises Graphiti._get_or_create_saga directly with a
# fake driver whose execute_query is mocked.

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.driver.driver import GraphDriver, GraphProvider
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.graphiti import Graphiti
from graphiti_core.graphiti_types import GraphitiClients
from graphiti_core.llm_client import LLMClient
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


@pytest.mark.asyncio
async def test_get_or_create_saga_preserves_existing_fields_when_saga_exists():
    graphiti = _make_graphiti()
    existing_record = {
        'uuid': 'saga-uuid',
        'name': 'Viaje a Japon 2026',
        'group_id': 'test',
        'created_at': datetime(2026, 3, 1, tzinfo=timezone.utc),
        'summary': 'Resumen previo del viaje.',
        'first_episode_uuid': 'ep-1',
        'last_episode_uuid': 'ep-2',
        'last_summarized_at': datetime(2026, 3, 2, tzinfo=timezone.utc),
        'last_summarized_episode_valid_at': datetime(2026, 3, 2, tzinfo=timezone.utc),
    }
    graphiti.driver.execute_query = AsyncMock(return_value=([existing_record], None, None))

    saga = await graphiti._get_or_create_saga(
        'Viaje a Japon 2026', 'test', datetime.now(timezone.utc)
    )

    assert saga.uuid == 'saga-uuid'
    assert saga.summary == 'Resumen previo del viaje.'
    assert saga.first_episode_uuid == 'ep-1'
    assert saga.last_episode_uuid == 'ep-2'
    assert saga.last_summarized_at == datetime(2026, 3, 2, tzinfo=timezone.utc)
    assert saga.last_summarized_episode_valid_at == datetime(2026, 3, 2, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_get_or_create_saga_creates_new_saga_when_absent():
    graphiti = _make_graphiti()
    graphiti.driver.execute_query = AsyncMock(return_value=([], None, None))
    saved: list[Any] = []

    async def fake_save(self: Any, driver: GraphDriver):
        saved.append(driver)

    created_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
    import graphiti_core.nodes as nodes_module

    original_save = nodes_module.SagaNode.save
    nodes_module.SagaNode.save = fake_save  # type: ignore[method-assign]
    try:
        saga = await graphiti._get_or_create_saga('Nueva saga', 'test', created_at)
    finally:
        nodes_module.SagaNode.save = original_save  # type: ignore[method-assign]

    assert saga.name == 'Nueva saga'
    assert saga.group_id == 'test'
    assert saga.created_at == created_at
    assert saga.first_episode_uuid is None
    assert saga.last_episode_uuid is None
    assert len(saved) == 1
