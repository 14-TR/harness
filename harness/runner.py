"""The entire agent loop. No HTTP, CLI, file I/O, or provider imports."""
import asyncio
from typing import AsyncIterator

from .tools import Tool, invoke
from .types import Event, Message, Provider


async def run(
    provider: Provider,
    messages: list[Message],
    tools: dict[str, Tool],
    *,
    max_turns: int = 8,
    parallel_tools: bool = False,
) -> AsyncIterator[Event]:
    """Append completed turns to caller-owned history and yield live events.

    Enable parallel_tools only when all supplied tools can run independently.
    Cancellation propagates; a cancelled tool batch is not added to history.

    The caller supplies all three dependencies: provider, history, and tools.
    There is no global state or hidden registry. This is dependency injection
    using regular arguments, rather than a framework.

    Because this function contains yield inside async def, it is an async
    generator. Consume it with `async for event in run(...)`. Each yield lets
    the consumer update its UI before requesting the next event.
    """
    if max_turns < 1:
        raise ValueError("max_turns must be positive")
    # Describe tools once per run, not once per generated text chunk or turn.
    # Tool availability is assumed stable until this run finishes.
    schemas = [tool.schema(name) for name, tool in tools.items()]
    # A "turn" here means one model request. A single user prompt can require
    # several turns when the model requests tools before giving its answer.
    for turn in range(1, max_turns + 1):
        yield Event("turn", turn)
        # Assemble a complete assistant message while also streaming its text.
        # We need the complete version to replay the conversation next turn.
        reply = Message("assistant")
        async for delta in provider.stream(messages, schemas):
            reply.content += delta.text
            reply.thinking += delta.thinking
            reply.calls.extend(delta.calls)
            # Events keep this loop independent of print(), a web UI, or logs.
            # A slow consumer delays the next chunk: this is backpressure.
            if delta.text:
                yield Event("text", delta.text)
            if delta.thinking:
                yield Event("thinking", delta.thinking)
        # No tool requests means the model has finished this run. "done" repeats
        # the full answer for consumers that want it; a streaming UI should not
        # print it again after already printing the individual "text" events.
        if not reply.calls:
            messages.append(reply)
            yield Event("done", reply.content)
            return
        # Announce every queued call. Despite the name, "tool_start" does not
        # mean a sequential call is already executing at this exact moment.
        for call in reply.calls:
            yield Event("tool_start", call)
        if parallel_tools:
            # gather overlaps async I/O and returns results in INPUT order,
            # even when later calls finish sooner. It does not make CPU-bound
            # Python code parallel. Only enable this for independent tools.
            results = await asyncio.gather(*(invoke(call, tools) for call in reply.calls))
        else:
            # Awaiting inside this comprehension preserves execution order.
            # This matters if a future tool writes a file another tool reads.
            results = [await invoke(call, tools) for call in reply.calls]
        # Commit the request and all its results together after execution.
        # Cancellation before this point leaves no unanswered call in history.
        # This protects conversation consistency, NOT external side effects:
        # cancelling a run cannot undo work a tool already performed.
        messages.append(reply)
        messages.extend(Message("tool", result, tool_name=call.name)
                        for call, result in zip(reply.calls, results))
        # Emit completed tool results after the whole batch is in history.
        # The next loop iteration lets the model interpret these results.
        for call, result in zip(reply.calls, results):
            yield Event("tool_result", {"name": call.name, "result": result})
    # Reaching the limit is not success: the last model turn still wanted tools.
    # Completed history remains available to the caller for inspection or reuse.
    raise RuntimeError(f"Stopped after {max_turns} model turns without a final answer")
