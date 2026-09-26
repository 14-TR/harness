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
| `harness/builtin.py` | Workspace file listing, search, reading, and exact edits |
| `harness/commands.py` | Configured tests and read-only Git views |
| `harness/web.py` | Bounded HTTP text retrieval |
| `harness/notes.py` | Small persistent project notes |
| `harness/cli.py` | Wires everything together and prints events |
| `harness/tracing.py` | Saves timestamped run events as JSONL |

State is a normal list of messages owned by the caller. The runner appends
completed turns. Reuse that list for a conversation, or save it outside the loop.
The CLI intentionally handles one prompt per invocation.

## Built-in tools

All ten tools are available in the CLI. They use the standard library and the
existing `httpx` dependency; Git tools also require the `git` executable.

| Tool | Arguments and behavior |
| --- | --- |
| `list_files` | Optional `path`, `pattern`, `limit`; recursively list workspace files. |
| `search_workspace` | Literal `query`, optional `path`, `pattern`, `context`, `limit`, `case_sensitive`; return paths, line numbers, and nearby lines. |
| `read_file` | `path`, optional inclusive, 1-based `start_line` and `end_line`; read later sections of large files. |
| `apply_patch` | `path`, `old_text`, `new_text`; replace one exact, unique match and return a diff. Empty `old_text` creates a new file or fills an empty one. |
| `run_tests` | No arguments; run the test command configured by the caller and report the exit code and output. |
| `git_status` | No arguments; show short status for the workspace repository. |
| `git_diff` | Optional `staged` and `path`; show unstaged or staged changes. Untracked files appear in status; read them with `read_file`. |
| `fetch_url` | `url`; retrieve readable HTML, plain text, JSON, or XML over HTTP(S). |
| `save_note` | `name`, `content`; save or replace a project note. |
| `read_notes` | Optional `name`; list note names, or read one note. |

For example:

```sh
.venv/bin/harness "Find the cancellation code and explain how it works" --stats
.venv/bin/harness "Fix the failing tests, run them again, then review the diff" --max-turns 16
.venv/bin/harness "Remember that this project should keep one runtime dependency"
```

File paths stay inside `--root`, and Git metadata cannot be read or edited through
the file tools. Listing and search skip symlinks and common generated, credential,
and local-state files, including virtual environments, traces, and notes. These
are fixed exclusions, not a full `.gitignore` interpreter. Glob patterns are
relative to `path`; `*` can match across directories, so `*.py` searches Python
files recursively. Search reads UTF-8 files up to 1 MiB, with a 16 MiB total scan
budget. Navigation also limits directory traversal; narrow `path` on large trees.
File reads scan up to 16 MiB characters to reach the requested lines. Output is
normally capped at 16,000 characters and truncation is marked.

Patches require an exact unique match, preserve existing file permissions, and
replace files atomically. They accept files up to 1 MiB. Read the relevant lines
first, and include enough surrounding text to identify one location. Edits take
effect immediately; Git diff provides the review view.

By default, `run_tests` runs the harness's current Python interpreter with
`-m unittest discover -s tests -v` in the workspace, with a 120-second timeout.
Choose another command explicitly:

```sh
.venv/bin/harness "Run the tests and explain any failures" \
  --test-command '.venv/bin/python -m pytest -q' --test-timeout 180
```

The command is split into arguments without a shell; pipes, redirection, and shell
expansion are not supported. The model cannot change its arguments. Test output
is capped at 16,000 bytes while the remainder is drained. Cancellation and timeout
kill and reap the child; on POSIX, the process group is killed too. Tests execute
workspace code with your user permissions. Git views require `--root` to be the
repository's top-level directory.

`fetch_url` uses a separate HTTP client, validates up to five redirects, rejects
URL credentials, and reads at most 256 KiB of response content. It does not run
JavaScript. Local network addresses are allowed; this is a local tool, not a
network sandbox.

Notes persist in `.harness/notes/<name>.md`, which this repository ignores in Git.
Names use letters, digits, underscores, or hyphens; each note holds up to 16,000
characters. Notes are retrieved through the tools, not automatically added to
every prompt. When using another workspace, ignore its `.harness/` directory too.
Notes and traces are plaintext; avoid storing credentials in them.

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
  The CLI preserves order because edits, tests, and notes can depend on each other.
- Eight model turns, 1024 output tokens per turn, and 30 seconds per tool by default
  (120 seconds for tests and 20 seconds for web retrieval).
  Turn/token limits are CLI options. Hitting either model limit is an error.
- Ctrl-C cancels the run. Async tools should clean up when cancelled; thread-backed
  operations such as file reads cannot be forcibly stopped once started.
- `--stats` reports first visible text and total wall time, including model time.
  These are not measurements of harness overhead alone.

This is a learning harness, not an OS sandbox. Filesystem containment checks do
not protect against another local process changing symlinks during an operation.
It has no arbitrary shell tool, automatic context compaction, persistent chat
sessions, or plugins. Saved notes provide explicit memory across CLI runs.

Protocol references: [Ollama streaming tool calls](https://docs.ollama.com/capabilities/tool-calling)
and [chat API](https://docs.ollama.com/api/chat).

## Verify

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Tests use temporary workspaces, subprocesses, fake providers, and HTTP transports.
They do not need a model or network access; Git cases skip if Git is unavailable.
