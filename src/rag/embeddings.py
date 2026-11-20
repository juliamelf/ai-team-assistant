"""Клиент API эмбеддингов cloud.ru и повторы при ошибках."""

from __future__ import annotations

import os
import time
from functools import wraps
from typing import Any, Callable

from dotenv import load_dotenv

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
    PermissionDeniedError,
    InternalServerError,
)

from src.rag.chunking import EMBEDDING_MODEL

load_dotenv()


class EmbeddingError(Exception):
    """Ошибка получения эмбеддинга.

    retryable=True — можно повторить (сеть, таймаут, 429, 5xx).
    retryable=False — повтор не поможет (401, 403, прочие 4xx).
    """

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


def _is_retryable_error(exc: Exception) -> bool:
    """Определяет, можно ли повторить запрос при данной ошибке."""
    if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError, InternalServerError)):
        return True
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return False
    # Ошибки с кодом 4xx — не повторяем, всё остальное — повторяем
    code = getattr(exc, "status_code", None)
    if code is not None:
        return not (400 <= code < 500)
    return True


def _wrap_openai_error(model: str) -> Callable[[Callable], Callable]:
    """Декоратор, преобразующий ошибки openai в EmbeddingError."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except EmbeddingError:
                raise
            except Exception as exc:
                retryable = _is_retryable_error(exc)
                raise EmbeddingError(
                    f"Ошибка API ({type(exc).__name__}): {exc}",
                    retryable=retryable,
                ) from exc

        return wrapper

    return decorator


@_wrap_openai_error(EMBEDDING_MODEL)
def _do_embed(
    client: Any,
    model: str,
    texts: list[str],
) -> list[list[float]]:
    """Вызывает embeddings.create и возвращает векторы."""
    response = client.embeddings.create(
        model=model,
        input=texts,
        dimensions=2048,
    )
    # Сортируем по index, чтобы порядок совпадал с input
    items = sorted(response.data, key=lambda x: x.index)
    return [item.embedding for item in items]


def make_embedder(
    model: str = EMBEDDING_MODEL,
    timeout: float = 60,
) -> Callable[[list[str]], list[list[float]]]:
    """Создаёт функцию embed для одного пакета текстов.

    Читает CLOUDRU_API_KEY и CLOUDRU_FM_BASE_URL из окружения.
    Выбрасывает EmbeddingError при ошибках API.
    """
    api_key = os.environ.get("CLOUDRU_API_KEY", "").strip()
    if not api_key:
        raise EmbeddingError("не задана переменная окружения CLOUDRU_API_KEY", retryable=False)

    base_url = os.environ.get("CLOUDRU_FM_BASE_URL", "").strip()
    if not base_url:
        base_url = "https://foundation-models.api.cloud.ru/v1"

    from openai import OpenAI

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
    )

    def embed(texts: list[str]) -> list[list[float]]:
        return _do_embed(client, model, texts)

    return embed


def embed_with_retry(
    embed: Callable[[list[str]], list[list[float]]],
    texts: list[str],
    max_retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> list[list[float]]:
    """Получает эмбеддинги для пакета текстов с повторами при временных ошибках.

    max_retries — общее число попыток (первая + повторы).
    Паузы: 2 с, 4 с перед повторами.
    При retryable=False выбрасывает сразу.
    """
    delays = [2, 4]
    attempt = 0

    while True:
        try:
            return embed(texts)
        except EmbeddingError as exc:
            if not exc.retryable or attempt >= max_retries - 1:
                raise
            delay = delays[attempt] if attempt < len(delays) else delays[-1]
            sleep(delay)
            attempt += 1
