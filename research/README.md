# Daily agentic-system-engineering research

A separate `harness-research` command in the Qwen harness. It leaves the existing
chat tools, runner and TUI unchanged. One runtime dependency remains `httpx`;
PDF fallback is the optional `research` extra (`pypdf`). macOS/Linux, Python 3.9+.
No paid provider, vector database, framework, dashboard or Discord integration.

## Setup and commands

```sh
.venv/bin/python -m pip install -e '.[research]'
cp research/agentic-system-engineering.json .harness/research-config.json
# Edit this private config if desired; paths are relative to the working directory.
.venv/bin/harness-research --config .harness/research-config.json search
.venv/bin/harness-research --config .harness/research-config.json run
.venv/bin/harness-research --config .harness/research-config.json status
.venv/bin/harness-research --config .harness/research-config.json retry --date YYYY-MM-DD
```

Create `.harness/` first if it does not already exist. `search` saves real metadata
and previews unread selection, but never marks a paper read or calls a model.
`run` uses the **local calendar date**, freezes its selection, resumes pending
stages and skips completed papers. A failed paper needs explicit `retry` on that
day; the next day's run may select it again because it remains unread. A
successful same-day rerun verifies artifact hashes without network/model calls
or rewriting reports. `retry` also repairs a failed synthesis/export without
reanalyzing successful papers. A completed report changed by a human fails its
integrity check; preserve/resolve the edit rather than overwriting it.

Delivery has its own durable SQLite queue, independent of whether papers are
already read. Every normal `run`/`retry` attempts at most **seven prior pending
digests**, oldest-attempt first, without discovery or model calls for those days.
It also retries a delivery-only current day without regenerating its digest.
Remaining failures appear in `pending_deliveries` and in new reports' warnings;
the CLI exits **2** while deliveries remain pending even if today's research is
complete. A completed-current-day invocation can drain prior deliveries but
otherwise leaves today's artifacts unchanged. Retries use each report's original
configured vault and exact recorded bytes; restore that destination rather than
changing historical configuration. Historical report text reflects its generation
time; current delivery status is authoritative in SQLite/`status`.

Exit codes: **0** complete/successful read-only command, **2** partial research
(including a shortfall), **1** configuration/lock/integrity error, **130** interrupt.
All commands emit JSON. `status` reads SQLite without taking the run lock so it
can observe an active run. A run's counts are final only when it finishes.

The default model is the already-installed `qwen2.5:7b`; the server must be
running. The research command does not start Ollama, download models, install
dependencies, execute experiments or enable scheduling. Configuring a nonlocal
model host is rejected. The model name is recorded in each report; provider raw
receipts retain the model and usage fields returned by Ollama. Tags are mutable;
keep the Ollama model digest with any independently reproducible experiment.

## Selection and provenance

Default engineering topic groups (overridable as a JSON `topics` mapping):

- architectures, orchestration and tool use;
- memory, retrieval and context;
- planning, delegation and multi-agent systems;
- evaluation, observability and debugging;
- reliability, recovery, permissions and sandboxing;
- latency, cost and resource efficiency.

The arXiv Atom query finds recent agent/agentic titles in cs.AI, cs.CL, cs.SE and
cs.CR. Deterministic keyword overlap screens title/abstract, not model opinion.
Five **unread base IDs** are selected: fresh (published within 30 days) before
backlog; within each group rank by engineering-topic overlap, publication date,
then pinned ID. Application papers can qualify when their agent architectures
or methods overlap these topics; this is a heuristic, not a quality score.
Pagination continues only when the bounded candidate pool cannot fill five.
A bounded search cannot establish that no other relevant papers exist.

Each selection records its overlap, fresh/backlog label, authors, categories,
published/updated timestamps, abstract and literal versioned ID. Duplicate
versions collapse to the newest discovered version; an already-read base ID is
not automatically reread when revised. Withdrawn/retracted entries are excluded.
At new discovery, the latest metadata for up to 20 previously read base IDs is
rechecked, and withdrawal warnings exclude those papers from subsequent history.
Older history outside this window is not continuously monitored. A completed
same-day no-op does not refresh metadata; it preserves the historical report.

## Source reading and analysis boundaries

1. Save original version-pinned arXiv HTML, extract article text with stable
   `H0001`-style block locators. Ignore scripts/navigation; never execute scripts.
2. If unavailable/unusable, save the pinned PDF and extract its text layer in a
   fixed parser subprocess. `P0001B001` means page 1, extracted block 1. Missing
   optional PDF support, scanned/encrypted PDFs, or oversized papers fail visibly.
3. Save all extracted text and JSON, plus a deterministic, head-and-spread sampled
   context. Reports state exact extracted/included character and block counts,
   truncation, retrieval time and SHA-256 hashes. These counts **do not prove
   visual/full semantic coverage**; equations, tables, figures and appendices can
   be lost. OCR is not silently substituted.
4. Qwen returns structured claims, methods, reported evidence, limitations and
   proposed practical experiments. It selects source locators from an enumerated
   set of substantive blocks; the controller attaches the **exact quoted source block**, rather than
   asking a small model to reproduce quotes from memory. Discovery-only abstract
   text is not included as uncitable model context. Proposed experiments require
   a change, comparator, metric and an integer 1–20 task/run/example trial count;
   rendered proposals include a 20-minute stop condition. They target a small
   local harness, not reproducing an author's entire training/data collection.
   Unknown locators,
   invented/modified quotes, extra keys, oversized items and incomplete output
   are rejected. A rejected call gets one bounded retry, not silent acceptance.
5. Synthesis receives current summaries and up to ten prior nonwithdrawn analyses
   from SQLite. Its references must be in that supplied set. Its next experiment
   uses the **same exact operational proposal schema and runtime validator** as
   per-paper proposals (without a block locator), including the controller's
   20-minute stop condition. Legacy free-text/statement/quote proposals, missing
   or extra fields, booleans/fractions as trial counts, counts outside 1–20 and
   unrecognized units are rejected before normalization. Summaries are
   explicitly compacted to bound context; they are not fresh full-paper reads.

Reports separate **author-reported claims/evidence**, **model interpretation and
experiment proposals**, and **independently verified results: none**. Matching a
source quote to a locator only verifies textual provenance, not that a model's
statement is entailed, novel, statistically sound or replicated. Read the linked
original and local per-paper report before acting. No code/benchmark from a paper
is run. Interpret absent evidence as "not established in the supplied excerpts,"
not "the paper never tested this."

## State, resumption and budgets

Default state is ignored by Git under `.harness/research/`:

```text
research.sqlite3                  runs, paper catalog, delivery queue, call events
run.lock                          advisory OS lock, released on process exit
search/<timestamp>/               preview metadata XML
runs/YYYY-MM-DD/
  discovery-*.xml                 original arXiv responses
  history-metadata.xml            when prior papers were refreshed
  selection.json                 frozen, deterministic selection
  state.json                     readable SQLite checkpoint mirror
  call-<uuid>-<number>-<phase>.json  immutable request/response/outcome events
  model-receipts-<attempt>.json    provider responses, including rejected outputs
  daily.md                       daily report, shortfall, failures and metrics
papers/<versioned-id>/
  metadata.json, source.json      metadata, coverage, source hashes
  original.html and/or original.pdf
  extracted.txt, extracted.json, context.json
  analysis.json, report.md
logs/                            launchd stdout/stderr (when configured)
```

SQLite is authoritative. A paper's catalog read/analysis record and run-item
completion (including its trusted artifact hashes) commit in **one transaction**.
Source caches and completed items are hash-checked before reuse in partial,
interrupted and completed runs. A mismatch fails closed before model/network work
for that run; it never refreshes trusted paper hashes from unchecked current bytes.
Only deliberately regenerated daily reports advance their hashes on retry.

Each invocation durably allocates a UUID before calls. Request-start, received
response, and accepted/rejected outcome events commit to SQLite before their
immutable JSON mirrors. Resume restores missing mirrors from these recorded bytes,
rejects altered mirrors, and reconciles known call/token metrics from the journal.
A failed readable-state mirror cannot downgrade committed paper completion.
Pre-journal completed histories retain their original metrics/provenance; they
are not silently relabeled as newly validated output. Historical complete runs
remain no-ops; old partial states need their established hashes to be reusable.

A crash can leave a pending stage, but the OS lock does not need stale-PID cleanup.
An uncommitted analysis may be repeated; a committed analysis is not. A hard kill
before a response is durably recorded can leave unknown consumed model usage.
`known_response_calls` and `unknown_usage_calls` distinguish journaled responses
from started requests without one; zero known tokens is not a claim of zero
unknown consumption. HTTP failures without a provider response are also in that
unknown-usage category. No exact-once external-call guarantee is made.

Defaults **per invocation**: 30-minute total runtime; 14 model calls including
retries; 2,000 output tokens/call; 12,288-token model context and 22,000 extracted
input characters per paper; 180 seconds/call; 20 MiB/download and 100 MiB total;
up to three pages of 80 metadata results; two network retries; at least 3.1
seconds between arXiv requests. PDF limits: 200 pages, 2 MiB extracted text,
30 CPU seconds, 45 wall seconds. Model output has a 100 KB wire limit. macOS
cannot reliably enforce the parser's address-space limit; it is **not an OS
sandbox**. Reports expose requests, downloaded bytes, retries, validation/model
failures, known token usage, model/wall seconds, completed count and shortfall.
Metrics accumulate over resumptions; failure/complete/shortfall counts describe
the latest state. Explicit retries receive fresh bounded invocation budgets.

Config is frozen by hash for an existing day, preventing accidental mixed-model
or mixed-budget resumptions. Restore its original config to resume, or use a
separate state directory for an intentionally different experiment. That new
state directory has a separate unread ledger. Source/report storage is retained;
there is no destructive automatic retention cleanup. Monitor disk usage and
archive whole state directories deliberately.

## Network and permission safety

Paper content cannot choose tools or targets: **no chat/file-edit/shell tools are
registered** with this model. The controller only requests arXiv HTTPS API and
version-pinned HTML/PDF endpoints. Credential URLs, arbitrary hosts, nonpublic
DNS results, endpoint/version-changing redirects and proxy environment settings
are rejected/disabled. Every redirect is revalidated. URLs inside papers are
never followed. This is an allowlist, not a transport-level DNS pinning sandbox;
local hostile DNS/other processes remain outside the threat model. Ollama uses a
separate loopback-only client. Generated Markdown escapes active HTML, embeds
and source-supplied link syntax. Local symlink redirection is rejected for state
writes, but a concurrent hostile local process is not sandboxed.

## Curated Obsidian export

Set `obsidian_dir` to an existing vault. For the designated setup:
`/Users/tr/Documents/obsidian/second brain` (the compatibility symlink is
resolved, not replaced). Only the curated daily digest is exported to:

`Projects/Agent Harness/Daily Runs/Agentic Research YYYY-MM-DD.md`

It links to the existing project Home; original papers, full extracted text,
per-paper analyses and raw model receipts stay outside iCloud in local state.
The exporter records an output hash, atomically writes, and reads back the exact
file. It refuses to overwrite unrelated existing files or human-edited exports.
Add annotations to other project notes. Local verification does not establish
arrival on an iPhone. It never changes `.obsidian`, core project notes or paused
conversation/bookmark jobs.

## Optional independent public research delivery

See [PUBLICATION.md](PUBLICATION.md) for the disabled-by-default, exact-target
GitHub research publisher, private preview command, content policy and setup.
It has a separate durable queue and can deliver completed research even when
Obsidian is inaccessible. Research idempotency remains unchanged; an opted-in
public delivery retry may still perform bounded metadata/Git network I/O.
No schedule or private configuration is enabled automatically.

## Daily launchd schedule (generate, review, then explicitly activate)

Default **08:15 in the Mac's current local timezone**. launchd handles calendar
and daylight-saving changes; missed wake/sleep intervals can coalesce into one
run on wake. It does not backfill every missed date, and Ollama must be available.
User agents require a logged-in user. RunAtLoad and KeepAlive are explicitly
false, avoiding surprise immediate execution or restart loops.

Generate from the repository with its virtualenv, validate, and inspect:

```sh
.venv/bin/harness-research --config .harness/research-config.json launchd \
  --output .harness/com.qwen-harness.agentic-research.plist
plutil -lint .harness/com.qwen-harness.agentic-research.plist
plutil -p .harness/com.qwen-harness.agentic-research.plist
```

**Generation never installs or activates anything.** After independent review
and explicit approval, activation commands are:

```sh
mkdir -p "$HOME/Library/LaunchAgents"
cp .harness/com.qwen-harness.agentic-research.plist "$HOME/Library/LaunchAgents/"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.qwen-harness.agentic-research.plist"
launchctl print "gui/$(id -u)/com.qwen-harness.agentic-research"
```

Optional manual verification after approval:
`launchctl kickstart "gui/$(id -u)/com.qwen-harness.agentic-research"`, then read
`status` and both log files. Do not use `kickstart -k` to kill an active run.
Disable with:

```sh
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.qwen-harness.agentic-research.plist"
```

If the label is already loaded, inspect it rather than blindly bootstrapping or
replacing it. Logs are local plaintext and not automatically rotated.

## Verify

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Offline tests cover selection/version/withdrawal rules, source budgets and
network boundaries, PDF fallback with an actual generated PDF, cited structured
validation, no-tool requests, checkpoint/retry/idempotency, integrity/human-edit
protection, runtime/locking and launchd generation. Mock transports in those
tests include real SQLite rollback/reopen and `os._exit` subprocess crashes before,
during and after paper commit, request/response/outcome receipts and export.
Cross-date delivery, queue drain bounds, all completed-artifact tamper paths and
both strict operational-proposal paths have regressions. Mock transports in these
tests are **not live arXiv/model evidence**. For live acceptance, run `search`,
`run`, inspect all five real analyses/coverage/receipts, then rerun the same day
and compare hashes and model/network counts. Preserve unsuccessful live receipts
instead of relabeling them as success.
