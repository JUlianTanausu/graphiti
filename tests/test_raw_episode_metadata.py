import pytest
from datetime import datetime, timezone
from graphiti_core.nodes import EpisodeType
from graphiti_core.utils.bulk_utils import RawEpisode


def test_raw_episode_accepts_episode_metadata():
    ep = RawEpisode(
        name='Test',
        content='Texto de prueba.',
        source_description='sensor',
        source=EpisodeType.text,
        reference_time=datetime(2026, 1, 15, 9, 30, tzinfo=timezone.utc),
        episode_metadata={'device': 'reloj', 'tags': ['rutina']},
    )
    assert ep.episode_metadata == {'device': 'reloj', 'tags': ['rutina']}


def test_raw_episode_episode_metadata_defaults_to_none():
    ep = RawEpisode(
        name='Test',
        content='Texto.',
        source_description='sensor',
        source=EpisodeType.text,
        reference_time=datetime(2026, 1, 15, 9, 30, tzinfo=timezone.utc),
    )
    assert ep.episode_metadata is None
