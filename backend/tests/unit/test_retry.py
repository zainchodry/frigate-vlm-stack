from unittest.mock import AsyncMock

import pytest

from app.services.retry import retry_async


@pytest.mark.asyncio
async def test_retry_async_uses_exponential_delays_and_recovers():
    operation = AsyncMock(side_effect=[ConnectionError("one"), ConnectionError("two"), "ok"])
    sleep = AsyncMock()

    result = await retry_async(operation, max_attempts=3, base_delay=0.5, max_delay=10, sleep=sleep)

    assert result == "ok"
    assert operation.await_count == 3
    assert [call.args[0] for call in sleep.await_args_list] == [0.5, 1.0]
