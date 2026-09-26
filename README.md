# Qwen harness

A small async Python agent harness for local Ollama. One runtime dependency
(`httpx`), one CLI entry point, no script directory, and no agent framework.
Defaults to the `qwen2.5:7b` model.

## Run

From the repository directory (Python 3.9+), with Ollama running and
`qwen2.5:7b` installed:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
.venv/bin/harness "Hello" --stats
```

Once installed:

```sh
.venv/bin/harness "Read README.md and explain this project in two sentences" --stats
```

Choose another installed model with `--model NAME`, another server with
`--host http://localhost:11434`, or a workspace with `--root PATH`.
`OLLAMA_MODEL` and `OLLAMA_HOST` provide the same defaults.
Use `--no-think` only with a model that supports the thinking option.

## Read in this order

| Module | Responsibility |
| --- | --- |
| `harness/runner.py` | The model → tools → model loop |
| `harness/types.py` | Messages, calls, deltas, events, and the provider protocol |
| `harness/tools.py` | A tool's schema, function, timeout, and recoverable errors |
| `harness/ollama.py` | Streaming HTTP and Ollama-specific serialization |
| `harness/builtin.py` | A workspace-scoped file-reading tool |
| `harness/cli.py` | Wires everything together and prints events |
| `harness/tracing.py` | Saves timestamped run events as JSONL |

State is a normal list of messages owned by the caller. The runner appends
completed turns. Reuse that list for a conversation, or save it outside the loop.
The CLI intentionally handles one prompt per invocation.

## Agent traces

Every CLI run saves a separate `.traces/<run-id>.jsonl` file under the selected
workspace (`--root`, or the current directory). The path is printed to stderr.
Each line is a JSON object with a run ID, sequence number, UTC timestamp,
elapsed seconds, event kind, and event data.

Traces include the initial messages and model settings, tool schemas, model-turn
events, streamed text and any provider-supplied thinking, tool arguments/results,
the final answer, and a completed/failed/cancelled status. Errors retain partial
output. A forced process kill may leave a trace without a final status.

```sh
.venv/bin/harness "Read README.md" --stats
.venv/bin/harness "Hello" --trace-dir ./my-traces
.venv/bin/harness "Hello" --no-trace
```

These are local plaintext files containing prompts and any file contents returned
by tools. The default `.traces/` directory and all `*.jsonl` trace files are
ignored by Git, including traces saved in custom directories. Review and redact
traces before sharing them in issues or elsewhere. Nothing is uploaded by the
recorder. Recording uses small synchronous, line-buffered writes in the
CLI consumer; the runner stays independent of storage. Trace I/O failures stop
the CLI rather than silently running without a trace.

Event times reflect when the consumer receives them. `tool_start` means queued,
and tool results arrive after the batch finishes, so these timestamps are not
individual tool execution durations. Token usage and costs are not recorded.

## Extend by composition

**Add a tool:** write an async function returning a string, wrap it in `Tool`,
and add it to the dictionary passed to `run`. Schemas are explicit so there is
no decorator or reflection system to learn. Functions must validate inputs;
schemas guide the model but are not a runtime validation layer.

```python
from harness.tools import Tool

async def add(a: int, b: int) -> str:
    if type(a) is not int or type(b) is not int:
        raise TypeError("a and b must be integers")
    return str(a + b)

tools["add"] = Tool(
    description="Add two integers",
    parameters={
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
        "additionalProperties": False,
    },
    execute=add,
)
```

**Replace the provider:** implement `stream(messages, tools)` as an async
iterator yielding `Delta`. No inheritance required. The current contracts
cover text and named tools; add call IDs or multimodal fields when a new
provider actually needs them.

**Add a UI, metrics, or logging:** consume `run(...)` events: `turn`, `text`,
`thinking`, `tool_start`, `tool_result`, and `done`. Keep slow logging off the
consumer path because an async iterator naturally applies backpressure.
`tool_start` announces a queued call; tool results are emitted after the batch.

**Add policy:** wrap a tool's execute function, or wrap a provider. Storage,
approval checks, retries, and context trimming can live at those boundaries.
Automatic retries are intentionally absent because tools may have side effects.

## Speed and limits

- One pooled async HTTP client for all model turns.
- Text is emitted as it arrives; the whole response is not buffered before display.
- Ollama is asked to keep the model loaded for ten minutes.
- Set `parallel_tools=True` in `run` only for tools safe to execute independently.
  The default preserves order for future tools that write or depend on each other.
- Eight model turns, 1024 output tokens per turn, and 30 seconds per tool by default.
  Turn/token limits are CLI options. Hitting either model limit is an error.
- Ctrl-C cancels the run. Async tools should clean up when cancelled; thread-backed
  operations such as file reads cannot be forcibly stopped once started.
- `--stats` reports first visible text and total wall time, including model time.
  These are not measurements of harness overhead alone.

The first tool reads UTF-8 files within the selected workspace and caps output
at 16000 characters. This is a learning harness, not an OS sandbox. It has no
shell execution, automatic context compaction, persistent sessions, or plugins.
Those can be added without making the initial loop harder to understand.

Protocol references: [Ollama streaming tool calls](https://docs.ollama.com/capabilities/tool-calling)
and [chat API](https://docs.ollama.com/api/chat).

## Verify

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests use fake providers and HTTP transports, so they do not need a model.
