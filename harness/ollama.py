"""Ollama's wire format stays here. Reuse one HTTP client across turns."""
import json
from typing import AsyncIterator, Optional

import httpx

from .types import Call, Delta, Message


def encode(message: Message) -> dict:
    """Translate our internal Message into the JSON shape Ollama expects."""
    value = {"role": message.role, "content": message.content}
    # Omit optional fields when empty. A user message, for example, has neither
    # a tool name nor calls. Preserving thinking matters for models that use it.
    if message.thinking:
        value["thinking"] = message.thinking
    if message.tool_name:
        value["tool_name"] = message.tool_name
    if message.calls:
        # Ollama nests each call's name and arguments under "function".
        # Keeping that detail here prevents API-specific dict access in run().
        value["tool_calls"] = [{"function": {
            "name": call.name, "arguments": call.arguments,
        }} for call in message.calls]
    return value


class Ollama:
    """A Provider implementation, matching the protocol without inheriting it."""

    def __init__(self, client: httpx.AsyncClient, model: str = "qwen2.5:7b",
                 *, options: Optional[dict] = None, think: Optional[bool] = None):
        # The caller owns the client's lifetime. Sharing it across turns keeps
        # HTTP connections pooled instead of reconnecting for each model call.
        self.client = client
        self.model = model
        self.options = options or {}
        # None = omit the option; False = explicitly disable thinking.
        # Those differ because not every model supports a thinking setting.
        self.think = think

    async def stream(self, messages: list[Message], tools: list[dict]) -> AsyncIterator[Delta]:
        # Ollama gets the full history on every request. There is no hidden
        # server-side conversation ID here. keep_alive asks Ollama to retain
        # the loaded model; it does not persist this conversation's messages.
        body = {"model": self.model, "messages": [encode(m) for m in messages],
                "tools": tools, "stream": True, "keep_alive": "10m",
                "options": self.options}
        if self.think is not None:
            body["think"] = self.think
        # This context releases the HTTP response on exit, including failures
        # and cancellation. Streaming avoids waiting for the entire body.
        async with self.client.stream("POST", "/api/chat", json=body) as response:
            if response.is_error:
                # Streamed bodies have not been read yet. Read an HTTP error's
                # body before accessing .text so the diagnostic is available.
                await response.aread()
                raise RuntimeError(f"Ollama HTTP {response.status_code}: {response.text}")
            # Ollama streams newline-delimited JSON (one JSON object per line).
            # aiter_lines handles network chunks splitting a JSON line in half.
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                # A stream can start with HTTP 200 and still report an error
                # later inside a JSON chunk. Check both layers.
                if chunk.get("error"):
                    raise RuntimeError(f"Ollama: {chunk['error']}")
                message = chunk.get("message", {})
                calls = []
                for item in message.get("tool_calls") or []:
                    function = item["function"]
                    arguments = function["arguments"]
                    if isinstance(arguments, str):
                        # Accept either an already-decoded object or JSON text.
                        arguments = json.loads(arguments)
                    if not isinstance(arguments, dict):
                        raise ValueError("Tool arguments must be a JSON object")
                    calls.append(Call(function["name"], arguments))
                # Convert API data to our own contract immediately. Neither the
                # runner nor its event consumer needs to know Ollama's format.
                yield Delta(message.get("content", ""), message.get("thinking", ""), calls)
                if chunk.get("done"):
                    # Process the final chunk BEFORE checking completion: it
                    # may contain useful data as well as the completion flag.
                    if chunk.get("done_reason") == "length":
                        # A truncated response is not a reliable final answer
                        # or a complete tool request, so fail explicitly.
                        raise RuntimeError("Model output limit reached; increase --max-tokens")
                    return
            # EOF alone does not prove success; a dropped connection could
            # otherwise silently masquerade as a complete model response.
            raise RuntimeError("Ollama stream ended before completion")
