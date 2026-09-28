# Independent public research delivery

**Disabled by default.** This channel publishes only to the literal destination
`https://github.com/14-TR/agentic-research.git`, branch `main`. It does not publish
research into the harness source repository. No remote is created by the code.
Do not enable it until the candidate and initial previews have been reviewed.

## Preview without publishing

```sh
.venv/bin/python -m harness.research --config .harness/research-config.json \
  public-prepare --output /absolute/private/new-preview-directory
```

Optional `--date YYYY-MM-DD`; otherwise all eligible historical runs are considered
(up to 3,660). The output must be empty and outside the code, research state and
vault directories. This command writes sanitized report Markdown, structured
public-only documents, a fixed README and a local manifest. It never initializes
Git, commits, enqueues, pushes, calls a model, edits historical reports, or touches
vault contents. It verifies completed source artifacts and makes a bounded fresh
arXiv metadata request for each eligible run. It copies SQLite plus its WAL under
the research lock into a private temporary snapshot, outside the preview, so even
SQLite SHM read marks are preserved. The snapshot is deleted on normal exit.

Review both the retained content and the withheld statements. Previews do not
opt in to publication and are **not** directly used as a Git source. Publication
prepares and durably freezes its own validated public-only document. No command
accepts arbitrary Markdown to upload.

## Enable only after independent review

1. Check the exact repository with `gh repo view 14-TR/agentic-research --json
   nameWithOwner,visibility,isEmpty,url`. It must be the intended public research
   repository. If absent and creation is authorized, use `gh repo create
   14-TR/agentic-research --public --description "Agentic engineering research digests"`.
   Do not initialize it with unrelated files or clone the private state into it.
2. Configure an appropriate GitHub credential in macOS Keychain for HTTPS host
   `github.com`, path `14-TR/agentic-research.git`. Prefer a repository-scoped
   credential with contents-write permission. The publisher uses only the fixed
   `osxkeychain` helper with `credential.useHttpPath=true`, disables interactive
   prompting, and deliberately ignores global Git configs and environment Git
   overrides. `gh auth status` alone does not prove this helper is configured;
   `gh auth setup-git`'s global shell helper is not used. Never put credentials in
   config, URLs, command-line arguments, logs, or the public staging repository.
3. Add **both** fields to the private research configuration, retaining every
   existing selection, model, budget, state and Obsidian setting:

   ```json
   {
     "public_repository": "https://github.com/14-TR/agentic-research.git",
     "public_staging_dir": "/absolute/dedicated/research-publisher"
   }
   ```

   This is a snippet, not a replacement for the complete configuration. The
   staging path must be canonical, absolute, without symlinks, and disjoint from
   code/state/vault. Use a new private directory. Only this publisher may manage
   it. Never manually add a remote, hooks, credentials, reports or other files.
   Even URL case changes, suffixes, credentials, alternate hosts/repos and SSH
   spellings are rejected. The two public fields do not change the research
   configuration hash, so existing completed runs do not need reanalysis.
4. Explicitly publish/retry only completed research:

   ```sh
   .venv/bin/python -m harness.research --config .harness/research-config.json public-retry
   .venv/bin/python -m harness.research --config .harness/research-config.json public-status
   ```

   This is the first command above that **can commit and push**. Read back remote
   `main` and inspect the exact files after it succeeds. Public commits use
   `TR Ingram <14-TR@users.noreply.github.com>` for both author and committer, UTC,
   and a fixed date-only message. No local hostname, username or private email
   is taken from Git configuration.

Once enabled, the existing `run`/`retry` entrypoints automatically enroll completed
runs and drain at most **seven public deliveries total per invocation**, oldest
attempt first. No schedule changes are needed. Enrollment includes eligible
historical runs; do not enable if that backfill is unwanted. To stop future
publishing, remove **both** public config fields; retained outbox entries remain
local and dormant. No scheduled job is installed, activated or changed by this
feature.

## Independent durable retry

The `public_delivery` table is separate from the Obsidian queue. It records target,
phase, attempts, safe error code, public document/hash, and acknowledged commit.
A public payload is committed to SQLite **before** any Git write. Retries re-use
that frozen document, without reanalysis, redownloads, new metadata requests or
reading the vault. Failed preparation retries metadata only, not the model.

Public delivery runs before Obsidian work, including the completed-run preflight
that reads a prior export. An inaccessible or edited Obsidian export can still
make the research command exit with an error, but cannot undo a successful public
delivery. Inspect `public-status`, or use `public-retry` to bypass Obsidian entirely.
A failed public write does not downgrade research completion or mark papers unread.
Normal run/retry and public-retry exit **2** while public rows remain pending;
public-prepare exits 2 if any eligible preview failed. Public command errors use a
fixed safe message, never exception text or Git stderr. Outbox `phase` distinguishes
preparation from publication failure without disclosing private diagnostics.

The dedicated Git working tree contains only fixed `README.md` and generated
`reports/YYYY-MM-DD.md` files. No `git add .`, raw report copying, source repository
history, arbitrary remotes, shell commands, force-push, merge or automatic rebase.
Only exact generated paths are added. Worktree, index, commit trees and new
ancestor metadata are validated before pushing. Unexpected files, staging,
symlinks, special files, hardlinks, changed bytes/configs and diverged history
fail closed. The remote ref **and fetched report blobs** must match before a row
is marked delivered. A crash or lost acknowledgement after push retries the same
commit rather than creating a duplicate. Preserve a conflicted stage and outbox
for review; do not repair by force-pushing or deleting the delivery ledger.

Metadata requests use the existing arXiv HTTPS/DNS allowlist, no proxies, no
source-chosen links, at most 1 MB and a 25-second deadline per prepared run.
Git calls use fixed argv, a minimal environment, disabled hooks/redirects, no
interactive credentials, 30 seconds per command and 120 seconds per publication
attempt. Timed-out subprocess groups, including transport/helpers, are killed.
Captured output is suppressed and size-checked. Git object transfer is bounded
by time, not a hard network-byte/disk quota. At most 32 unpushed ancestors and
3,660 public documents are supported; larger histories need operator review.

## Content policy and limitations

- Only the day, complete current-paper IDs, independently fetched arXiv titles,
  allowlisted analysis statements, current-paper synthesis references and next
  proposal are considered. Source URLs are constructed from literal validated
  pinned IDs. History-only synthesis connections are omitted, not rewritten to
  suggest different support. Public titles are fetched anew rather than trusted
  from private state/model prose. Withdrawn, mismatched or unsafe metadata blocks
  preparation.
- No authors/contact lists, local filenames/paths, configuration, warnings,
  errors, provider receipts, metrics, abstracts, source blocks, quotes or private
  daily Markdown are copied. Whole model fields are withheld on unsupported
  characters, unknown vocabulary, suspicious credential/contact/path/link syntax,
  encoded forms, or substantial overlap with attached source quotations. The
  overlap check rejects full equality or an eight-token overlap, not every short
  phrase; this is a substantial-copy heuristic, not a guarantee of zero quotation.
  Curly apostrophes, en/em dashes, percent and multiplication signs have explicit
  ASCII research-prose normalizations; arbitrary Unicode folding/decoding is not
  performed. Bounded decimals up to 100 with at most three fractional digits and
  possessives are supported. Mixed alphanumeric lexemes are never split into safe
  words/numbers; the exact metric `F1` is a reviewed exception. Credential labels
  are checked across punctuation, possessives and plural forms before lexical
  acceptance. The research compound `token-budget` is not a credential assignment.
  Whole-field rejection is retained; suspicious fragments are not removed to
  launder the remaining field into publishable text. Title
  terms are allowed only from the verified public titles. The vocabulary is a
  reviewed static list, never automatically learned from private/model text.
- All retained prose is explicitly model-generated interpretation or an unexecuted
  proposal, **not independent evidence**. Metadata verification establishes title
  and ID provenance, not scientific validity or semantic entailment. No experiment
  is performed and no author code is run.
- A minimum content gate is enforced by the final document validator, before
  outbox freezing and at the Git sink. Every paper needs at least one retained
  statement of five alphabetic words; at least half the papers need two analytical
  section types (claims/methods/evidence/limitations), and the digest must cover at
  least three of those four types. It also needs a retained next-experiment proposal
  of at least 20 words, comparator/metric/trial-budget labels, 1–20 trials per
  condition (tasks, trials, runs or examples), and the 20-minute stop bound.
  This is a coverage floor, not proof of scientific quality or novelty. Empty,
  bibliography-only, proposal-only or insufficiently retained reports remain
  pending, unfrozen, with the constant code `insufficient_public_content`; no Git
  stage is created. Private originals are preserved for review, not rewritten.
- This conservative policy is intentionally lossy: many valid research statements
  and uncommon technical terms will be withheld. It can substantially reduce the
  usefulness of a digest. Review representative previews before opting in; do not
  weaken the boundary just to remove redaction notices.
- **Neither regex nor a word allowlist proves absence of every possible secret or
  personal fact.** Common-word passphrases, contextual inference and covert channels
  are not solved. An adversarial model with access to private data is outside the
  automatic publisher's safe-use assumptions. Do not feed private material into
  this research pipeline; disable automatic publication if that assumption changes.
  A compromised local account, concurrent malicious filesystem writer or tampered
  trusted SQLite ledger is not sandboxed. Existing public metadata and TLS/DNS are
  trusted for provenance. Previously frozen documents do not continuously refresh
  withdrawal notices. No perfect secrecy, sandbox or exactly-once network claim is
  made.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Publication tests use **local bare Git fixtures only**, including actual commits,
pushes, fetched-blob readback, abrupt process crashes, scoped staging rejection,
process-group deadlines, outbox reopen/retry and independent Obsidian failures.
They do not verify a live GitHub credential or remote write. Live publication is a
separate explicitly authorized acceptance step after independent candidate review.
