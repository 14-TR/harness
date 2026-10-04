"""Private candidates + independent exact-byte review, never heuristic approval.

This module deliberately does not infer privacy from vocabulary. The independent
reviewer is a trusted operational role, not an authenticated cryptographic signer.
An approval is evidence of that review, not proof that the prose is true or safe.
"""
import hashlib
import html
import json
from datetime import datetime
from pathlib import Path
import re
import tempfile
import time

from .research_public_git import GitPublisher
from .research_public import PublicError, TARGET, checked_day, checked_id, eligible

SECTIONS = {
    'claims': 'Claims — model summary, not independent evidence',
    'methods': 'Methods — model summary',
    'reported_evidence': 'Author-reported evidence — not replicated',
    'limitations': 'Limitations — model interpretation',
    'experiments': 'Experiments — model proposals, not executed',
}
README = b'''# Agentic research

Dated digests of public arXiv papers: model-generated interpretations, not
independently verified evidence. Consult the linked original papers.

Reports distinguish author-reported findings from model interpretations. All
experiment proposals are unexecuted. Extraction can omit equations, figures,
tables and appendices; these digests are not full-paper critical reviews.

New publication requires independent review of the exact report bytes for
privacy, usefulness, provenance and honest experiment labeling. Review does not
establish scientific correctness. Raw private research state is not published.
'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def text(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 8000
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise PublicError('invalid_review_candidate_text')
    # Render model content as inert prose, not author-controlled Markdown/HTML.
    return re.sub(r'([\\`*{}_\[\]#!|])', r'\\\1', html.escape(value, quote=False))


def render(state, titles):
    if not eligible(state):
        raise PublicError('research_not_complete')
    day = checked_day(state['day'])
    lines = ['# Agentic research — ' + day, '',
             'Model-generated interpretation and proposals; not independently verified.',
             'No experiments were executed. Read the original papers before acting.',
             'Titles and pinned identifiers were checked against arXiv metadata.',
             'Extraction can omit equations, figures, tables and appendices; this is not a full-paper review.', '']
    ids = {checked_id(i['paper']['id']) for i in state['items']}
    if len(ids) != len(state['items']) or set(titles) != ids:
        raise PublicError('public_metadata_unverified')
    for item in state['items']:
        identity = item['paper']['id']
        lines += ['## ' + text(titles[identity]), '', 'https://arxiv.org/abs/' + identity, '']
        found = 0
        for key, label in SECTIONS.items():
            values = item['analysis'].get(key, [])
            if not isinstance(values, list) or len(values) > 3:
                raise PublicError('invalid_review_candidate_analysis')
            if values:
                lines += ['### ' + label, ''] + ['- ' + text(v['statement']) for v in values] + ['']
                found += key != 'experiments'
        if found < 2:
            raise PublicError('insufficient_review_candidate_content')
    # A syntactically current ID does not prove the synthesis names that paper.
    # Historical analyses have misattributed another paper to a current ID. Keep
    # per-paper analysis; do not turn these unchecked mappings into source links.
    lines += ['Cross-paper synthesis links are omitted because their model-supplied source mapping is not independently verified.', '']
    lines += ['## Next experiment — model proposal, not executed', '',
              text(state['synthesis']['next_experiment']), '']
    result = '\n'.join(lines).encode()
    if len(result) > 100000:
        raise PublicError('review_candidate_too_large')
    return result


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + '\n').encode()


def parse(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PublicError('duplicate_review_json_key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique)


def file_name(name):
    if name == 'README.md':
        return name
    if not isinstance(name, str) or not re.fullmatch(r'reports/\d{4}-\d{2}-\d{2}\.md', name):
        raise PublicError('invalid_review_public_path')
    checked_day(name[8:-3])
    return name


def hashes(value):
    if not isinstance(value, dict) or len(value) > 3661:
        raise PublicError('invalid_review_file_map')
    for name, digest in value.items():
        file_name(name)
        if not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            raise PublicError('invalid_review_hash')
    return value


def load_approved(bundle, approval, expected_approval):
    """Snapshot approved public bytes into memory before any Git/network action."""
    from .research_public_git import regular_bytes, safe_path
    root = safe_path(bundle)
    raw_approval = regular_bytes(Path(approval), 100000)
    if not isinstance(expected_approval, str) or sha(raw_approval) != expected_approval:
        raise PublicError('review_approval_changed')
    approved = parse(raw_approval)
    if (not isinstance(approved, dict) or set(approved) != {
            'schema', 'target', 'branch', 'bundle_sha256', 'decision', 'reviewer', 'reviewed_at', 'checks'}
            or type(approved['schema']) is not int or approved['schema'] != 1
            or approved['target'] != TARGET or approved['branch'] != 'main'
            or approved['decision'] != 'approve'
            or not isinstance(approved['reviewer'], str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{3,120}', approved['reviewer'])
            or not isinstance(approved['checks'], dict)
            or set(approved['checks']) != {'privacy', 'usefulness', 'provenance', 'unexecuted_proposals'}
            or any(v is not True for v in approved['checks'].values())):
        raise PublicError('independent_review_required')
    if not isinstance(approved['reviewed_at'], str) or datetime.fromisoformat(approved['reviewed_at']).utcoffset() is None:
        raise PublicError('invalid_review_timestamp')
    raw = regular_bytes(root / 'manifest.json', 2000000)
    if sha(raw) != approved['bundle_sha256']:
        raise PublicError('review_bundle_manifest_changed')
    manifest = parse(raw)
    if (not isinstance(manifest, dict) or set(manifest) != {
            'schema', 'target', 'branch', 'base_commit', 'base_files', 'files', 'sources'}
            or type(manifest['schema']) is not int or manifest['schema'] != 1
            or manifest['target'] != TARGET or manifest['branch'] != 'main'
            or not isinstance(manifest['base_commit'], str)
            or not re.fullmatch(r'(?:[a-f0-9]{40})?', manifest['base_commit'])):
        raise PublicError('invalid_review_manifest')
    hashes(manifest['base_files'])
    hashes(manifest['files'])
    if (not manifest['base_commit'] and manifest['base_files']) or 'README.md' not in manifest['files']:
        raise PublicError('invalid_review_manifest')
    sources = manifest['sources']
    days = {name[8:-3] for name in manifest['files'] if name != 'README.md'}
    if not days or not isinstance(sources, dict) or set(sources) != days:
        raise PublicError('invalid_review_provenance')
    for day, source in sources.items():
        checked_day(day)
        if (not isinstance(source, dict) or set(source) != {'state_sha256', 'papers'}
                or not isinstance(source['state_sha256'], str)
                or not re.fullmatch('[a-f0-9]{64}', source['state_sha256'])
                or not isinstance(source['papers'], list) or not 1 <= len(source['papers']) <= 10):
            raise PublicError('invalid_review_provenance')
        for identity in source['papers']:
            checked_id(identity)
        if len(set(source['papers'])) != len(source['papers']):
            raise PublicError('invalid_review_provenance')
    actual = {str(p.relative_to(root)) for p in root.rglob('*') if not p.is_dir()}
    if actual != set(manifest['files']) | {'manifest.json'}:
        raise PublicError('unexpected_review_bundle_file')
    files = {name: regular_bytes(root / name) for name in manifest['files']}
    if any(sha(data) != manifest['files'][name] for name, data in files.items()):
        raise PublicError('review_bundle_content_changed')
    if files['README.md'] != README:
        raise PublicError('invalid_review_readme')
    return manifest, files, approved


class ReviewedGit(GitPublisher):
    """Reuse only hardened fixed-target transport; no heuristic content approval."""

    def snapshot(self):
        self.deadline = time.monotonic() + 120
        self._initialize()
        head = self._remote_head()
        if not head:
            return '', {}
        self._git('fetch', '--quiet', '--no-tags', self._remote(), 'refs/heads/main')
        if self._text('rev-parse', 'FETCH_HEAD') != head:
            raise PublicError('review_remote_changed')
        return head, self.contents(head)

    def contents(self, ref):
        result = {}
        for item in self._git('ls-tree', '-r', '-z', ref)[1].split(b'\0'):
            if not item:
                continue
            header, name = item.split(b'\t')
            mode, kind, blob = header.decode('ascii').split(' ')
            name = file_name(name.decode('ascii'))
            if mode != '100644' or kind != 'blob' or not re.fullmatch('[a-f0-9]{40}', blob):
                raise PublicError('unsafe_review_remote_tree')
            size = self._text('cat-file', '-s', blob)
            if not size.isdigit() or int(size) > 100000 or len(result) >= 3661:
                raise PublicError('review_remote_size_limit')
            result[name] = self._git('cat-file', 'blob', blob)[1]
        return result

    def deliver(self, manifest, files):
        from .research import atomic
        remote, prior = self.snapshot()
        prior_hashes = {name: sha(data) for name, data in prior.items()}
        expected = dict(manifest['base_files'], **manifest['files'])
        # Lost acknowledgement / crash after push: same approved bytes are already
        # present. Do not create another commit or trust a local receipt instead.
        if prior_hashes == expected:
            if manifest['base_commit'] and self._git('merge-base', '--is-ancestor',
                    manifest['base_commit'], remote, allow_failure=True)[0]:
                raise PublicError('review_remote_diverged')
            if self._remote_head() != remote:
                raise PublicError('review_remote_changed')
            return remote
        if remote != manifest['base_commit'] or prior_hashes != manifest['base_files']:
            raise PublicError('review_remote_changed')
        if remote:
            self._git('read-tree', remote)
            self._git('checkout-index', '-a')
            self._git('update-ref', 'refs/heads/main', remote)
        for name, data in files.items():
            atomic(self.repo / name, data)
        self._git('add', '--', *sorted(files))
        tree = self._text('write-tree')
        if {name: sha(data) for name, data in self.contents(tree).items()} != expected:
            raise PublicError('review_index_mismatch')
        day = max(manifest['sources'])
        self._git('commit', '--quiet', '--no-gpg-sign', '-m', 'Publish reviewed research through ' + day)
        head = self._head()
        self._check_git_metadata()
        if {name: sha(data) for name, data in self.contents(head).items()} != expected:
            raise PublicError('review_commit_mismatch')
        try:
            self._git('push', '--porcelain', '--no-verify', self._remote(), 'HEAD:refs/heads/main')
        except PublicError:
            pass  # The server may have accepted a write whose response was lost.
        if self._remote_head() != head:
            raise PublicError('review_push_unconfirmed')
        self._git('fetch', '--quiet', '--no-tags', self._remote(), 'refs/heads/main')
        if (self._text('rev-parse', 'FETCH_HEAD') != head
                or {name: sha(data) for name, data in self.contents('FETCH_HEAD').items()} != expected
                or self._remote_head() != head):
            raise PublicError('review_readback_mismatch')
        return head


def publish(bundle, approval, expected_approval, receipt):
    from .research import atomic, locked, stamp
    from .research_public_git import regular_bytes, safe_path
    manifest, files, approved = load_approved(bundle, approval, expected_approval)
    receipt = safe_path(receipt).resolve()
    root = safe_path(bundle).resolve()
    if root == receipt or root in receipt.parents or receipt == safe_path(approval).resolve():
        raise PublicError('review_receipt_must_be_separate')
    # Serialize local attempts; remote fast-forward checks arbitrate other hosts.
    with locked(receipt.parent):
        if receipt.exists():
            old = parse(regular_bytes(receipt, 2000000))
            if (old.get('bundle_sha256') != approved['bundle_sha256']
                    or old.get('approval_sha256') != expected_approval):
                raise PublicError('review_receipt_conflict')
        with tempfile.TemporaryDirectory(prefix='harness-reviewed-git-') as tmp:
            commit = ReviewedGit(Path(tmp).resolve(), TARGET).deliver(manifest, files)
        result = {'schema': 1, 'status': 'confirmed', 'target': TARGET, 'branch': 'main',
                  'commit': commit, 'bundle_sha256': approved['bundle_sha256'],
                  'approval_sha256': expected_approval, 'files': manifest['files'],
                  'verified_at': stamp()}
        atomic(receipt, json_bytes(result))
        return result


async def prepare(config, output, *, day=None, client=None, resolver=None):
    """Produce a PRIVATE unapproved bundle; no model, enqueue, commit or push."""
    import asyncio
    import shutil
    import sqlite3
    from contextlib import closing
    from dataclasses import replace
    from urllib.parse import urlencode
    import httpx
    from .research import atomic, encode, locked, verify_completed
    from .research_public import validate_config
    from .research_public_git import safe_path
    from .research_sources import Arxiv, parse_atom
    output = safe_path(output)
    validate_config(replace(config, public_repository=TARGET, public_staging_dir=str(output)))
    if output.exists():
        raise PublicError('review_output_must_be_new')
    if day is not None:
        checked_day(day)
    # Read from a private WAL-aware copy to preserve the original SQLite/SHM bytes.
    with tempfile.TemporaryDirectory(prefix='harness-reviewed-source-') as tmp:
        copied = Path(tmp) / 'snapshot.sqlite3'
        database = Path(config.data_dir).absolute() / 'research.sqlite3'
        with locked(config.data_dir):
            for suffix in ('', '-wal'):
                source = safe_path(Path(str(database) + suffix))
                if source.exists():
                    if not source.is_file() or source.stat().st_size > 1000000000:
                        raise PublicError('unsafe_review_source')
                    shutil.copyfile(source, Path(str(copied) + suffix))
        if not copied.exists():
            raise PublicError('review_source_missing')
        with closing(sqlite3.connect(copied)) as db:
            sql = 'select payload from runs' + (' where day=?' if day else '') + ' order by day limit 3661'
            states = [json.loads(r[0]) for r in db.execute(sql, (day,) if day else ())]
    if len(states) > 3660:
        raise PublicError('review_source_limit')
    states = [s for s in states if eligible(s)]
    if not states:
        raise PublicError('no_completed_review_sources')
    files, sources = {'README.md': README}, {}
    async def titles_for(state, web):
        ids = [checked_id(i['paper']['id']) for i in state['items']]
        source = Arxiv(web, max_bytes=1000000, total_bytes=1000000, retries=0, resolver=resolver)
        raw = await source.get('https://export.arxiv.org/api/query?' + urlencode({'id_list': ','.join(ids)}))
        papers = parse_atom(raw)
        if len(papers) != len(ids) or {p['id'] for p in papers} != set(ids) or any(p['withdrawn'] for p in papers):
            raise PublicError('public_metadata_unverified')
        return {p['id']: p['title'] for p in papers}
    async def build(web):
        for state in states:
            verify_completed(state)
            for item in state['items']:
                saved = Path(item['report']).parent / 'analysis.json'
                if json.loads(saved.read_bytes()) != item['analysis']:
                    raise PublicError('review_analysis_provenance_mismatch')
            titles = await asyncio.wait_for(titles_for(state, web), 25)
            identity = checked_day(state['day'])
            files['reports/' + identity + '.md'] = render(state, titles)
            sources[identity] = {'state_sha256': sha(encode(state).encode()),
                                 'papers': [i['paper']['id'] for i in state['items']]}
    if client is not None:
        await build(client)
    else:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as web:
            await build(web)
    with tempfile.TemporaryDirectory(prefix='harness-reviewed-remote-') as tmp:
        base, prior = await asyncio.to_thread(ReviewedGit(Path(tmp).resolve(), TARGET).snapshot)
    manifest = {'schema': 1, 'target': TARGET, 'branch': 'main', 'base_commit': base,
                'base_files': {name: sha(data) for name, data in prior.items()},
                'files': {name: sha(data) for name, data in files.items()}, 'sources': sources}
    output.mkdir(mode=0o700, parents=True)
    for name, data in files.items():
        atomic(output / name, data)
    raw = json_bytes(manifest)
    atomic(output / 'manifest.json', raw)
    return {'prepared': sorted(sources), 'bundle_sha256': sha(raw), 'uploaded': False}


def main():
    import argparse
    import asyncio
    from .research import load_config
    parser = argparse.ArgumentParser(description='Exact-byte independent-reviewed public research')
    commands = parser.add_subparsers(dest='command', required=True)
    prepare_parser = commands.add_parser('prepare', help='Private unapproved candidate; reads remote, never pushes')
    prepare_parser.add_argument('--config', required=True, type=Path)
    prepare_parser.add_argument('--output', required=True, type=Path)
    prepare_parser.add_argument('--date')
    for name in ('check', 'publish'):
        command = commands.add_parser(name)
        command.add_argument('--bundle', required=True, type=Path)
        command.add_argument('--approval', required=True, type=Path)
        command.add_argument('--approval-sha256', required=True)
        if name == 'publish':
            command.add_argument('--receipt', required=True, type=Path)
            command.add_argument('--execute', required=True, action='store_true', help='Explicit authorization to commit/push approved bytes')
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            result = asyncio.run(prepare(load_config(args.config), args.output, day=args.date))
        elif args.command == 'check':
            manifest, files, approval = load_approved(args.bundle, args.approval, args.approval_sha256)
            result = {'approved': True, 'bundle_sha256': approval['bundle_sha256'], 'files': manifest['files'], 'uploaded': False}
        else:
            result = publish(args.bundle, args.approval, args.approval_sha256, args.receipt)
        print(json_bytes(result).decode(), end='')
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception:
        # Errors must never echo raw source, Git stderr, filesystem paths or secrets.
        parser.exit(1, 'Error: reviewed_publication_failed; preserve candidate and retry only after review\n')


if __name__ == '__main__':
    main()
