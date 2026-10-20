"""向量化：直接调用本机已配置的向量模型服务，供平台自己的知识库使用。"""

from __future__ import annotations

import math

import httpx

from .errors import DomainError
from .embedding_vectors import ordered_vectors, stable_cosine
from .provider_settings import LocalProviderStore


class EmbeddingClient:
    """按批请求向量模型；密钥只在本进程内使用，不返回给前端。"""

    def __init__(self, store: LocalProviderStore | None = None) -> None:
        self.store = store or LocalProviderStore()

    def _settings(self) -> tuple[str, str, str]:
        try:
            embedding = self.store.read().get('embedding', {})
        except (RuntimeError, FileNotFoundError, ValueError) as exc:
            raise DomainError(503, 'embedding_unconfigured', '尚未配置向量模型，请先完成模型安装') from exc
        base_url, api_key, model = embedding.get('base_url', ''), embedding.get('api_key', ''), embedding.get('model', '')
        if not base_url or not api_key or not model:
            raise DomainError(503, 'embedding_unconfigured', '尚未配置向量模型，请先完成模型安装')
        return base_url.rstrip('/'), api_key, model

    def embed(self, texts: list[str], *, timeout_seconds: float | None = None) -> list[list[float]]:
        """一次请求内完成整批向量化；返回顺序与入参一致。"""
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise DomainError(422, 'embedding_text_empty', '存在空文本，无法向量化')
        if timeout_seconds is not None and (isinstance(timeout_seconds, bool)
                or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise DomainError(504, 'embedding_timeout', '向量请求的剩余时限已耗尽')
        base_url, api_key, model = self._settings()
        try:
            from .bounded_http import post_json
            # 默认入口也采用绝对时限、不继承代理、不重试；流程传入的剩余预算只能收紧。
            budget = 60.0 if timeout_seconds is None else min(timeout_seconds, 60.0)
            response = post_json(f'{base_url}/embeddings', headers={'Authorization': f'Bearer {api_key}'},
                payload={'model': model, 'input': texts}, timeout_seconds=budget)
        except httpx.TimeoutException as exc:
            raise DomainError(504, 'embedding_timeout', '向量模型响应超时，请稍后重试') from exc
        except httpx.RequestError as exc:
            raise DomainError(503, 'embedding_unavailable', '无法连接向量模型服务，请检查网络与模型配置') from exc
        if response.status_code >= 400:
            raise DomainError(502, 'embedding_upstream_failed', '向量模型返回错误，请检查模型配置与额度')
        try:
            payload = response.json()
        except ValueError as exc:
            raise DomainError(502, 'embedding_invalid_response', '向量模型返回了无法识别的响应') from exc
        return ordered_vectors(payload, len(texts))


def cosine(left: list[float], right: list[float]) -> float:
    """沿用旧异常缓存零相关口径，使用稳定计算避免非有限分数打断整批检索。"""
    return stable_cosine(left, right)
