# Independent public research delivery

> **Superseded: historical implementation notes below.** The supported recovery
> path is [exact-byte independent-reviewed publication](REVIEWED_PUBLICATION.md).
> `run`/`retry` no longer publish, even with legacy opt-in fields. The old
> `public-retry` and `public-revise` CLI commands are disabled. Do not follow the
> activation instructions below; retain them and the old outbox as provenance.
> New reports require a separately produced, SHA-bound independent approval.

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
public-only documents, a fixed README, retention counts and a local manifest. It never initializes
Git, commits, enqueues, pushes, calls a model, edits historical reports, or touches
vault contents. It verifies completed source artifacts and makes a bounded fresh
arXiv metadata request for each eligible run. It copies SQLite plus its WAL under
the research lock into a private temporary snapshot, outside the preview, so even
SQLite SHM read marks are preserved. The snapshot is deleted on normal exit.

Review retained prose, any inline redactions, and the omission counts. Previews do not
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

- **Privacy, not vocabulary censorship.** New documents use schema 2. Ordinary
  scientific prose, names of methods/models, years, metrics and unfamiliar words
  survive. Nothing learns an allowlist from private data. Known public arXiv titles
  and pinned IDs are fetched independently; arXiv links are constructed from those
  IDs. Exact credential-free pinned arXiv abstract links may also occur in prose.
- Only completed current-paper statements, current-paper synthesis references and
  the bounded proposal are considered. History-only connections are omitted, not
  relabeled. No authors/contact lists, local filenames/paths, configs, logs, errors,
  provider receipts, private metrics/state, abstracts, source blocks, quotation
  fields or daily Markdown are copied. There is no model rewrite or new research.
- **Cycle-2 candidate is blocked; public publishing must remain disabled.** The
  conservative candidate withholds an entire field on recognized credential,
  contact, telephone, path/filename or other private-span context. It never emits
  disconnected filename/path fragments around inline masks. Unicode whitespace
  and punctuation are examined lexically; Unicode letters are not separators.
  Phone declarations do not depend on national digit-group boundaries. Existing
  `[redacted]` placeholders remain readable, but new private spans are not masked.
  The historical useful reports are an acceptance fixture, not proof of safety.
- **Unresolved semantic boundary:** bare-label recognition still cannot reliably
  distinguish personal contact declarations from ordinary scientific subjects.
  `please contact jane q. smith` escapes the current guard, while ordinary contact
  mechanics, API-key rotation and password-hashing sentences can be withheld.
  Tests retain these failures rather than weakening expectations or expanding a
  scientific-vocabulary allowlist. The known review inputs passing is insufficient
  to authorize publication. Further changes require a new authorized design/review
  decision, not another punctuation-specific repair cycle.
- Mixed alphanumeric credential shapes are not split into separately approved words. Percent signs, multiplication
  signs, curly apostrophes and dashes have explicit typography normalizations.
  Compatibility folding is used **only as a rejection probe**, never to publish a
  decoded secret. Percent/entity/escape encoded inputs are not decoded and emitted.
- Public-source phrase overlap is **not** a privacy signal. Quotation fields are not
  copied, but a model summary can repeat public source language. Earlier whole-field
  eight-token overlap censorship is removed for new documents. Summaries are still
  attributed to their public papers, not independent evidence or guaranteed original
  phrasing. This is not a copyright/quotation-length classifier.
- Omitted fields and their empty section headings are absent from the new Markdown.
  At most one concise omission summary appears at the end. Inline masks explicitly
  show where a private span was removed; no fabricated replacement prose is used.
- The final validator and Git sink enforce a coverage floor: each paper needs a
  retained statement of five alphabetic words; at least half the papers need two
  analytical section types, and the digest needs three of claims/methods/evidence/
  limitations. It needs a retained proposal of at least 20 words with comparator,
  metric, 1–20 tasks/trials/runs/examples per condition and a 20-minute stop bound.
  Empty or insufficient digests stay pending and unfrozen. This is not a scientific
  quality guarantee. No experiment is run and no author code is executed.
- **No perfect detector claim.** Unlabeled common-word passphrases, personal facts,
  extensionless filenames without path/context, unusual encodings and covert
  channels cannot reliably be distinguished from public research by these
  heuristics. Benign slash notation can resemble a path; short mathematical ratios and
  the literal research term CI/CD are recognized, not arbitrary uppercase paths. Review previews.
  Public papers and their no-tool analyses are the only safe-use inputs. If private
  context enters the pipeline, disable automatic publication; do not rely on this
  scanner to make arbitrary private text public. A compromised account, malicious
  concurrent filesystem writer or tampered trusted SQLite ledger is not sandboxed.
  Public metadata/TLS/DNS are trusted for provenance, not scientific validity.
  Frozen documents do not continuously refresh withdrawal notices.

## Explicit correction of frozen or published digests

Policy deployment does **not** silently regenerate old payloads or trust new hashes
for historical private artifacts. Schema-1 validation/rendering is frozen in
`research_public_v1.py` so the original public bytes remain reproducible. Daily
`run`/`retry` creates schema-2 documents for newly prepared deliveries, independently
of Obsidian. A previously frozen pending or delivered document needs explicit
correction authorization; public-retry alone does not revise it.

1. Run `public-prepare` into a new empty private directory. Inspect every report,
   `RETENTION.md` and `manifest.json`. Retention is counted from actual output fields
   by section (retained, inline-redacted, omitted), with the current frozen version
   as the before comparison where one exists. Previews make no state/Git writes.
2. Independently approve the exact candidate and report SHA-256. Obtain the current
   public payload SHA-256 from the trusted private ledger (`public_delivery.sha256`
   for an original, latest delivered `public_revisions.sha256` for a later revision).
   Do not derive approval from changed local report bytes or repair the ledger by
   recomputing its hashes.
3. Only with external publication authorization, run:

   ```sh
   .venv/bin/python -m harness.research --config .harness/research-config.json \
     public-revise --date YYYY-MM-DD \
     --expected-payload-sha256 <trusted-current-public-payload-sha256> \
     --expected-report-sha256 <reviewed-preview-report-sha256>
   .venv/bin/python -m harness.research --config .harness/research-config.json public-status
   ```

   This command **can push**. It verifies completed source artifacts against their
   existing hashes, fetches public metadata, rebuilds with the new policy and
   requires exact agreement with the approved report hash. Stale payload approval
   or changed preview bytes fail closed before a revision is frozen or Git is used.
   Arbitrary Markdown, replacement provenance hashes or upload paths are not accepted.
4. A new `public_revisions` row records the previous public-payload hash, immutable
   new payload/hash, report hash, original-state snapshot hash and creation time.
   The original `public_delivery` row and all private histories, source hashes,
   reports, catalog, receipts and vault files stay intact. Mutable attempt/status/
   error/confirmed-commit fields are separate from that immutable provenance.
   Revisions form a checked per-day hash chain. Delivery status shows superseded
   originals and revision rows separately; originals are not silently rewritten.
5. Git accepts only exact known old/new bytes for the corrected day, plus the fixed
   legacy/current README. It appends `Correct research YYYY-MM-DD` commits, never
   force-pushes or rewrites public history. Original public reports remain in old
   commits. Fixed public identities, tree/index/history validation and remote
   ref/fetched-blob readback all remain enforced. Other reports are not regenerated.
6. If a response is lost or a process crashes, repeat the **same command with the
   same two hashes**. The approved revision was frozen before Git and is reused
   without metadata/model work, including after a push accepted by the server.
   Only `public-revise` drains revisions: daily sync/public-retry never initiate or
   drain a correction. Pending revisions stay visible and can block a conflicted
   stage until explicitly retried. Preserve a conflict for review, never erase the
   outbox/stage, replace old hashes or force-push. A newer revision supersedes an
   earlier correction command; it cannot be used to roll back current output.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Publication tests use **local bare Git fixtures only**, including actual commits,
pushes, fetched-blob readback, abrupt process crashes, scoped staging rejection,
process-group deadlines, outbox reopen/retry and independent Obsidian failures.
They do not verify a live GitHub credential or remote write. Live publication is a
separate explicitly authorized acceptance step after independent candidate review.
