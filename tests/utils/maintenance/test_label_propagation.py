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

import signal

from graphiti_core.utils.maintenance.community_operations import Neighbor, label_propagation


class _Alarm:
    """Bounds a block to `seconds` wall-clock time, raising TimeoutError if exceeded.

    label_propagation is a synchronous, database-free function with no await
    points, so a hang in it blocks the whole process — this makes that failure
    mode safe to assert on in a test instead of hanging the suite forever.
    """

    def __init__(self, seconds: float):
        self.seconds = seconds

    def __enter__(self):
        def _handler(signum, frame):
            raise TimeoutError(f'label_propagation did not return within {self.seconds}s')

        signal.signal(signal.SIGALRM, _handler)
        signal.setitimer(signal.ITIMER_REAL, self.seconds)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        signal.setitimer(signal.ITIMER_REAL, 0)


def _ring_projection(n_nodes: int) -> dict[str, list[Neighbor]]:
    """A ring where every node has two equal-weight neighbors.

    Synchronous label propagation (all labels recomputed from the same
    snapshot, then swapped in at once) is known to oscillate forever on
    symmetric/tied structures like this: at n=154 (the size of a real
    production group this bug was found against) it does not converge in
    120,000+ iterations.
    """
    projection: dict[str, list[Neighbor]] = {}
    for i in range(n_nodes):
        left = f'n{(i - 1) % n_nodes}'
        right = f'n{(i + 1) % n_nodes}'
        projection[f'n{i}'] = [
            Neighbor(node_uuid=left, edge_count=2),
            Neighbor(node_uuid=right, edge_count=2),
        ]
    return projection


def test_label_propagation_terminates_on_oscillating_ring():
    """Regression test for an infinite loop: a symmetric ring never converges
    under unbounded synchronous label propagation. label_propagation must
    still return (a capped, best-effort clustering) within a bounded number
    of iterations rather than hang the process forever."""
    projection = _ring_projection(154)

    with _Alarm(5):
        clusters = label_propagation(projection)

    all_uuids = {uuid for cluster in clusters for uuid in cluster}
    assert all_uuids == set(projection.keys()), 'every node must end up in exactly one cluster'


def test_label_propagation_still_clusters_normal_graphs():
    """Non-regression check: two disconnected, internally-agreeing groups
    must still converge to two separate clusters, same as before any cap
    was introduced."""
    projection: dict[str, list[Neighbor]] = {
        'a0': [Neighbor(node_uuid='a1', edge_count=3), Neighbor(node_uuid='a2', edge_count=3)],
        'a1': [Neighbor(node_uuid='a0', edge_count=3), Neighbor(node_uuid='a2', edge_count=3)],
        'a2': [Neighbor(node_uuid='a0', edge_count=3), Neighbor(node_uuid='a1', edge_count=3)],
        'b0': [Neighbor(node_uuid='b1', edge_count=3), Neighbor(node_uuid='b2', edge_count=3)],
        'b1': [Neighbor(node_uuid='b0', edge_count=3), Neighbor(node_uuid='b2', edge_count=3)],
        'b2': [Neighbor(node_uuid='b0', edge_count=3), Neighbor(node_uuid='b1', edge_count=3)],
    }

    with _Alarm(5):
        clusters = label_propagation(projection)

    cluster_sets = [set(cluster) for cluster in clusters]
    assert {'a0', 'a1', 'a2'} in cluster_sets
    assert {'b0', 'b1', 'b2'} in cluster_sets
