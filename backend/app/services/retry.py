from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar


T = TypeVar("T")


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    base_delay: float,
    max_delay: float,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    logger: logging.Logger | None = None,
) -> T:
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least one")

    for attempt in range(1, max_attempts + 1):
        try:
            return await operation()
        except Exception:
            if attempt >= max_attempts:
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            if logger:
                logger.warning("operation failed; retrying", extra={"attempt": attempt, "delay_seconds": delay})
            await sleep(delay)

    raise AssertionError("unreachable")
