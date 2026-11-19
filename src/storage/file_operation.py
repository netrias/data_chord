"""Keep temporary files available until a blocking operation has finished using them."""

import asyncio
from collections.abc import Callable


async def run_file_operation[**P, R](operation: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
    worker = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Cancelling the waiter cannot stop a thread. Drain it before its caller releases the files.
        finished = asyncio.gather(worker, return_exceptions=True)
        while not finished.done():
            try:
                await asyncio.shield(finished)
            except asyncio.CancelledError:
                continue
        raise
