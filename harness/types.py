"""The vocabulary the rest of the harness shares.

Data travels through the system like this:
    Message history -> provider -> Delta chunks -> runner -> Event consumer
                                      |
                                      +-> Call -> tool -> Message history

These are ordinary Python objects, independent of HTTP and terminal output.
Keeping them here lets you change a provider or a UI without rewriting the loop.
"""
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol


@dataclass
class Call:
    """A request FROM the model to execute a named Python tool.

    Example: Call("read_file", {"path": "README.md"}).
    This is just data; constructing a Call does not execute anything.
    """

    # This name must match a key in the tool registry passed to run().
    name: str
    # Later expanded into keyword arguments: execute(**arguments).
    arguments: dict[str, Any]


@dataclass
class Message:
    """One item in the conversation sent back to the model on each turn."""

    # Typically "system", "user", "assistant", or "tool".
    role: str
    content: str = ""
    # Each instance needs its OWN list. A shared mutable default could leak
    # tool calls between unrelated messages; default_factory avoids that.
    calls: list[Call] = field(default_factory=list)
    # Tool-result messages identify which function produced their content.
    tool_name: str = ""
    # Some models return a separate thinking field. Retain it for replay even
    # if the UI chooses not to display it. Qwen2.5 normally leaves this empty.
    thinking: str = ""


@dataclass
class Delta:
    """One incremental piece from a provider, not a complete response.

    A chunk can contain text, thinking, tool calls, or a combination.
    Text chunks are not guaranteed to align with words or individual tokens.
    Providers must yield complete Call objects, assembling fragments if needed.
    """

    text: str = ""
    thinking: str = ""
    calls: list[Call] = field(default_factory=list)


@dataclass
class Event:
    """Something the runner wants a UI or observer to know happened.

    kind selects the meaning of data: "text" carries a string, "tool_start"
    carries a Call, and so on. This deliberately simple envelope avoids a
    separate class for every event. A typed union can replace it later.
    """

    kind: str
    data: Any = None


class Provider(Protocol):
    # Protocol means structural typing: any object with this method fits.
    # Your provider does NOT have to inherit from this class or register itself.
    # An implementation uses `async def` with `yield` to produce an async iterator.
    # The declaration is a normal def because its return type is the iterator,
    # not a coroutine that must first be awaited to obtain the iterator.
    def stream(self, messages: list[Message], tools: list[dict]) -> AsyncIterator[Delta]:
        # Protocols describe an interface; they contain no working provider here.
        ...
