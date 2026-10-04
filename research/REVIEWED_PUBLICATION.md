# Reviewed public research publication

This is the supported publication path. Daily `run`/`retry` **never publish**,
even if legacy public configuration fields remain present. Legacy `public-retry`
and `public-revise` CLI commands fail closed. Keep the old private outbox, staging
area, source hashes and reports intact; they are history, not a queue to drain.
The old internal publisher APIs remain only for compatibility tests and forensic
reproduction. Do not call them from jobs or operators.

## Authority and limits

The target is fixed in code: `https://github.com/14-TR/agentic-research.git`, `main`.
There is no CLI/config/environment target override and no automatic approval.
The public tree contains only `README.md` and `reports/YYYY-MM-DD.md`. A private
candidate manifest, independent approval and confirmation receipt never enter
Git. Source history, raw downloads, source quotations, private reports, model
receipts, metrics, configuration and vault files are not copied to the public
repository.

**The independent reviewer is the privacy boundary.** Preparation selects only
accepted per-paper analysis fields and the unexecuted proposal, but it does not
claim arbitrary model prose is safe. It does not run the broken heuristic privacy
scanner or censor scientific vocabulary. A trusted reviewer must inspect every
exact byte to be published for credentials, personal/private information, local
paths and private identifiers, useful research, source provenance, and correct
labeling of unexecuted experiments. If anything is uncertain, withhold approval.
Scientific correctness is not established by privacy review. Reports explicitly
identify author-reported evidence, model interpretation, incomplete extraction,
and unexecuted proposals.

A model-generated synthesis can cite a valid current-paper ID while actually
naming a different historical paper. The renderer omits these unverified
cross-paper mappings with one concise note; it does not relabel them as evidence.
Per-paper claims/methods/evidence/limitations/proposals remain intact. The final
proposal is explicitly model-generated and unexecuted, not a verified result.

The approval manifest is a **hash-bound operational attestation**, not a digital
signature or proof of reviewer identity. A process that controls the trusted
reviewer's files/CLI can forge it. Use a genuinely independent Hermes reviewer
context; never let the candidate producer generate its own approval. Treat model
and paper text as data, not instructions. The gate prevents accidental publication
without an explicit approval and prevents changing approved bytes; it is not a
sandbox against a compromised operator, account or concurrent filesystem writer.

## 1. Prepare, without publication

From the harness checkout:

```sh
.venv/bin/python -m harness.research_reviewed prepare \
  --config .harness/research-config.json \
  --output /absolute/private/new-candidate-directory
```

Optional `--date YYYY-MM-DD` limits the input to one completed run. Without it,
all completed historical runs are candidates, including corrections of published
dates. Review the full set deliberately. The output must not exist and must be
outside source/state/vault directories. It is created private (directory 0700,
files 0600). A failed preparation is not a publishable bundle.

Preparation verifies completed artifact hashes and agreement of the accepted
analysis with its saved artifact. It reads a WAL-aware private database snapshot
under the research lock, without changing the source database/SHM. It verifies
pinned IDs/titles against current arXiv metadata, does not call the model, and
never opens the vault or reads `daily.md` as publication content. It fetches the
fixed remote read-only to bind the candidate to the exact base commit and the
SHA-256 of every existing public file. No Git commit or push occurs.

## 2. Independently review and approve exact content

Review `manifest.json`, `README.md`, and **every** report named by `files`. Check
the report's public paper links and source identities; inspect original public
sources where a claim looks doubtful. The private `sources` map records the
canonical completed-state SHA-256 and pinned paper IDs. Source metadata has been
checked, but claims are not independently replicated. A content correction may
edit a private report and update its entry in `files`; preserve the original
candidate, explain the correction, and independently review the new full bundle.
Any change invalidates prior approval.

The separate reviewer writes an approval JSON **outside the bundle**, with exactly
these fields (replace placeholders only after successful independent review):

```json
{
  "schema": 1,
  "target": "https://github.com/14-TR/agentic-research.git",
  "branch": "main",
  "bundle_sha256": "<SHA-256 of exact manifest.json bytes>",
  "decision": "approve",
  "reviewer": "<independent-review-session-id>",
  "reviewed_at": "<ISO-8601 timestamp including timezone>",
  "checks": {
    "privacy": true,
    "usefulness": true,
    "provenance": true,
    "unexecuted_proposals": true
  }
}
```

The reviewer returns the SHA-256 of the exact approval JSON via the trusted task
channel, not merely a file path. The caller supplies that digest explicitly:

```sh
.venv/bin/python -m harness.research_reviewed check \
  --bundle /absolute/private/candidate-directory \
  --approval /absolute/private/approval.json \
  --approval-sha256 <reviewer-returned-approval-sha256>
```

`check` makes no Git/network/state writes. It verifies fixed target, complete
checks, provenance structure, exact manifest/report hashes, permitted paths,
regular files, no extra files, and the fixed README. Hashes bind **all** bytes,
including whitespace. It does not perform or certify the independent review.

## 3. Explicitly publish, then verify receipt

Only after independent code/content review and publication authorization:

```sh
.venv/bin/python -m harness.research_reviewed publish \
  --bundle /absolute/private/candidate-directory \
  --approval /absolute/private/approval.json \
  --approval-sha256 <reviewer-returned-approval-sha256> \
  --receipt /absolute/private/receipts/unique-candidate.json \
  --execute
```

Credentials use the same bounded HTTPS transport and fixed `osxkeychain` helper
as the historical publisher: no interactive prompts, global Git overrides,
redirects, hooks, source repository config, or private Git identity. Configure a
repository-scoped credential separately; successful read-only preparation does
not establish write access. The transport has per-command/group deadlines and
output bounds, but no hard disk/network-transfer quota.

The publisher loads the approved bytes into memory before Git, then uses a fresh
private Git staging repository containing only approved public files and the
already-public base tree. It checks the entire base against the reviewed manifest,
appends a fixed-message commit with the fixed public identity, and uses an ordinary
fast-forward push. It never force-pushes, merges, or rewrites history. Existing
published corrections remain accessible in their old commits.

A successful receipt records `status: confirmed`, the fetched remote commit,
exact file hashes, bundle/approval digests and readback time. A push response alone
is not success: the remote main ref and fetched blobs are checked. The receipt is
written only after confirmation. Repeating the **same command and approval** after
a lost response or a crash re-reads the remote and confirms the existing commit
without duplication. A conflicting/newer remote tree fails closed, rather than
rolling it back. Prepare and review a new candidate when the base has changed.
An existing receipt for another approval/bundle is never overwritten.

## Daily continuation

A separately authorized Hermes reviewer job may prepare completed missing dates,
review their exact candidate contents in an independent context, write/return an
approval hash, then call this explicit publisher under the standing publication
authorization. The job must maintain/read its receipt ledger, reconcile the live
remote before claiming current, and fail closed on review or publication errors.
Retry confirmed content without reanalysis. Obsidian failure must not block this
separate path. Scheduling is intentionally outside this module; preparation or
installation does not activate a job or change research configuration.

## Verification and known historical failures

```sh
.venv/bin/python -m unittest discover -s tests -p test_research_reviewed.py -v
.venv/bin/python -m unittest discover -s tests -v
```

New-path tests use real isolated local bare Git fixtures for publication,
correction/history preservation, exact readback, post-push crash recovery,
idempotence and stale-approval rejection. They also cover tampering, symlinks,
extra files, rejected review, CLI gates, source preservation and disabled legacy
automatic publication. They do not exercise live GitHub write credentials.

Four known failures in `test_research_privacy_final` remain visible: three benign
scientific sentences are censored and one ambiguous personal-contact sentence
escapes. They are why heuristic auto-publication is disabled, not tests to delete
or assertions to relax. The new reviewed path never calls that sanitizer. Three
legacy integration tests now exercise old outbox compatibility explicitly or
assert no automatic publication, matching the new authority contract; their
historical artifact/retry assertions remain in place.
