"""A tool is a schema and an async function. The registry is just a dict."""
import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from .types import Call


@dataclass
class Tool:
    """Everything needed to describe a capability and run it locally.

    The model sees description + parameters, never the Python function.
    The harness receives the model's requested arguments and calls execute.
    """

    description: str
    # JSON Schema describes the input shape to the model. It is NOT runtime
    # validation: each execute function must check inputs it relies on.
    parameters: dict
    # Callable[..., Awaitable[str]] means "a function whose arguments vary,
    # and whose returned object can be awaited to obtain a string".
    execute: Callable[..., Awaitable[str]]
    timeout: float = 30

    def schema(self, name: str) -> dict:
        # Build the model-facing description only. Keep execute and timeout
        # local; neither belongs in a JSON request sent to the model.
        # This function-schema format is supported by Ollama. A future provider
        # can translate it into its own API's tool-description format.
        return {"type": "function", "function": {
            "name": name, "description": self.description,
            "parameters": self.parameters,
        }}


async def invoke(call: Call, tools: dict[str, Tool]) -> str:
    """Execute one request and turn ordinary failures into model-readable text."""
    # A model can invent a tool name. Lookup must fail gracefully instead of
    # treating model output as arbitrary Python code to evaluate.
    tool = tools.get(call.name)
    if tool is None:
        return f"Tool error: unknown tool {call.name!r}"
    try:
        # ** unpacks {"path": "a.txt"} into execute(path="a.txt").
        # wait_for bounds how long we await the tool and cancels it on timeout.
        # Cancellation is cooperative: tools must clean up any work they start.
        return await asyncio.wait_for(tool.execute(**call.arguments), tool.timeout)
    except asyncio.TimeoutError:
        return f"Tool error: {call.name} exceeded {tool.timeout:g}s"
    except Exception as error:
        # Feeding the error back lets the model fix arguments or explain failure.
        # On supported Python versions CancelledError inherits BaseException,
        # not Exception, so user cancellation still escapes this handler.
        # If tools later handle secrets, sanitize their error messages here.
        return f"Tool error: {type(error).__name__}: {error}"
