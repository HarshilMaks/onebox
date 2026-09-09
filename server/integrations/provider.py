"""Shared bounded worker support for synchronous provider SDKs.

Python cannot safely kill a running SDK thread.  The runner therefore limits
admission, applies a deadline to callers, and keeps a slot reserved until a
timed-out or cancelled synchronous operation actually exits.  Provider-specific
HTTP transports must also have a finite socket timeout.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import Any, TypeVar


T = TypeVar("T")


class ProviderOperationTimeout(RuntimeError):
    """A provider operation exceeded its caller-visible deadline."""


class ProviderCapacityExceeded(RuntimeError):
    """All bounded provider worker slots are occupied."""


@dataclass
class OperationLifetime:
    """Expose whether a caller detached before its synchronous work finished."""

    detached: bool = False


class BoundedSyncRunner:
    """Run complete synchronous SDK operations without using the default executor."""

    def __init__(self, *, name: str, max_workers: int, default_timeout: float) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        if default_timeout <= 0:
            raise ValueError("default_timeout must be positive")
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=name)
        self._slots = asyncio.BoundedSemaphore(max_workers)
        self._default_timeout = default_timeout
        self._closed = False

    async def run(
        self,
        operation: Callable[..., T],
        *args: Any,
        timeout: float | None = None,
        deadline: float | None = None,
        lifetime: OperationLifetime | None = None,
        on_detached_completion: Callable[[], None] | None = None,
        **kwargs: Any,
    ) -> T:
        """Await one synchronous operation with bounded admission and a deadline.

        A cancelled or timed-out caller releases its worker admission slot only
        after the thread exits, preventing abandoned SDK calls from allowing an
        unbounded number of replacement threads.
        """
        if self._closed:
            raise ProviderCapacityExceeded("provider worker pool is closed")
        loop = asyncio.get_running_loop()
        if deadline is None:
            operation_timeout = self._default_timeout if timeout is None else timeout
            if operation_timeout <= 0:
                raise ValueError("timeout must be positive")
            deadline = loop.time() + operation_timeout

        admission_timeout = deadline - loop.time()
        if admission_timeout <= 0:
            raise ProviderOperationTimeout("provider operation timed out before admission")
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=admission_timeout)
        except TimeoutError as exc:
            raise ProviderCapacityExceeded("provider worker capacity is exhausted") from exc

        execution_timeout = deadline - loop.time()
        if execution_timeout <= 0:
            self._slots.release()
            raise ProviderOperationTimeout("provider operation timed out before execution")

        future = loop.run_in_executor(self._executor, partial(operation, *args, **kwargs))
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=execution_timeout)
        except TimeoutError as exc:
            raise ProviderOperationTimeout("provider operation timed out") from exc
        except asyncio.CancelledError:
            raise
        finally:
            if future.done():
                self._slots.release()
            else:
                if lifetime is not None:
                    lifetime.detached = True

                def release_when_finished(_future) -> None:
                    self._slots.release()
                    if on_detached_completion is not None:
                        on_detached_completion()

                future.add_done_callback(release_when_finished)

    async def aclose(self) -> None:
        """Reject new work and cancel queued work during application shutdown."""
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
