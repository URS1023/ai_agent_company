"""标准向量协议与数值稳定性回归，全程纯函数/HTTP替身，不发真实请求。"""

import math
from pathlib import Path
import sys

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_platform.core.embedding_vectors import ordered_vectors, validate_vectors
from agent_platform.core.embeddings import EmbeddingClient, cosine
from agent_platform.core.errors import DomainError


def test_standard_response_restores_exact_input_order():
    payload = {'data': [{'index': 2, 'embedding': [3, 30]}, {'index': 0, 'embedding': [1, 10]},
        {'index': 1, 'embedding': [2, 20]}]}
    assert ordered_vectors(payload, 3) == [[1.0, 10.0], [2.0, 20.0], [3.0, 30.0]]


@pytest.mark.parametrize('data', [
    [{'embedding': [1]}, {'embedding': [2]}],
    [{'index': 0, 'embedding': [1]}, {'index': 0, 'embedding': [2]}],
    [{'index': 0, 'embedding': [1]}],
    [{'index': -1, 'embedding': [1]}, {'index': 1, 'embedding': [2]}],
    [{'index': 0, 'embedding': [1]}, {'index': 2, 'embedding': [2]}],
    [{'index': True, 'embedding': [1]}, {'index': 0, 'embedding': [2]}],
    [{'index': '0', 'embedding': [1]}, {'index': 1, 'embedding': [2]}],
    [{'index': 0.0, 'embedding': [1]}, {'index': 1, 'embedding': [2]}],
    [None, {'index': 1, 'embedding': [2]}],
])
def test_missing_duplicate_or_invalid_index_never_guesses_positions(data):
    with pytest.raises(DomainError) as error:
        ordered_vectors({'data': data}, 2)
    assert error.value.code == 'embedding_invalid_response' and error.value.status == 502


@pytest.mark.parametrize('vectors', [
    [[True]], [['1.2']], [[None]], [[float('nan')]], [[float('inf')]], [[-float('inf')]],
    [[]], [[0, -0.0]], [[10 ** 1000]], [[1], [1, 2]], ['invalid'],
])
def test_invalid_numeric_coordinates_and_dimensions_are_rejected(vectors):
    with pytest.raises(DomainError) as error:
        validate_vectors(vectors, len(vectors))
    assert error.value.code == 'embedding_invalid_response'


def test_dimension_is_fixed_across_batches_without_rejecting_finite_large_values():
    first, dimension = validate_vectors([[1e308, 1e308, 1e308]], 1)
    assert dimension == 3 and first[0][0] == 1e308
    with pytest.raises(DomainError):
        validate_vectors([[1, 2]], 1, expected_dimension=dimension)


@pytest.mark.parametrize('scale', [1e308, 1e-300, 1.0])
def test_cosine_has_no_square_overflow_or_underflow(scale):
    assert cosine([scale, scale, scale], [scale, scale, scale]) == pytest.approx(1.0)
    assert cosine([scale, scale], [-scale, -scale]) == pytest.approx(-1.0)
    assert cosine([scale, 0], [0, scale]) == pytest.approx(0.0)


@pytest.mark.parametrize('left,right', [([0], [1]), ([float('nan')], [1]), ([True], [1]),
    (['1'], [1]), ([1], [1, 2]), ([], []), ([float('inf')], [1])])
def test_legacy_invalid_cache_returns_finite_zero_instead_of_crashing(left, right):
    score = cosine(left, right)
    assert math.isfinite(score) and score == 0.0


class FakeStore:
    def read(self):
        return {'embedding': {'base_url': 'http://127.0.0.1:19099/v1',
            'api_key': 'synthetic-test-only-key', 'model': 'fake-embedding'}}


@pytest.mark.parametrize('timeout,expected', [(None, 60.0), (7.5, 7.5), (90.0, 60.0)])
def test_default_and_flow_remaining_timeout_use_same_bounded_transport(monkeypatch, timeout, expected):
    calls = []
    def response(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json={'data': [
            {'index': 1, 'embedding': [2, 20]}, {'index': 0, 'embedding': [1, 10]}]})
    monkeypatch.setattr('agent_platform.core.bounded_http.post_json', response)
    result = EmbeddingClient(FakeStore()).embed(['第一条', '第二条'], timeout_seconds=timeout)
    assert result == [[1.0, 10.0], [2.0, 20.0]]
    assert len(calls) == 1 and calls[0][1]['timeout_seconds'] == expected
    assert calls[0][1]['payload']['input'] == ['第一条', '第二条']


def test_model_timeout_has_one_attempt_and_safe_error(monkeypatch):
    calls = []
    def timeout(*args, **kwargs):
        calls.append(1)
        raise httpx.ReadTimeout('never expose synthetic internal detail')
    monkeypatch.setattr('agent_platform.core.bounded_http.post_json', timeout)
    with pytest.raises(DomainError) as error:
        EmbeddingClient(FakeStore()).embed(['test'])
    assert error.value.code == 'embedding_timeout' and len(calls) == 1
    assert 'internal detail' not in error.value.message


@pytest.mark.parametrize('budget', [0, -1, float('inf'), float('nan'), True, '10'])
def test_invalid_budget_never_dispatches_a_request(monkeypatch, budget):
    calls = []
    monkeypatch.setattr('agent_platform.core.bounded_http.post_json', lambda *a, **kw: calls.append(1))
    with pytest.raises(DomainError) as error:
        EmbeddingClient(FakeStore()).embed(['test'], timeout_seconds=budget)
    assert error.value.code == 'embedding_timeout' and not calls
