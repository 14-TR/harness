"""Opt-in public research boundary. Never publish private reports or raw state.

Only independently retrieved public titles/IDs and privacy-checked model
statements cross this boundary. Unknown scientific vocabulary is not sensitive.
Heuristics do not prove absence of every secret or personal statement.
"""
from datetime import date
import asyncio
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from urllib.parse import urlencode

import httpx

from .research_analysis import KEYS
from .research_sources import Arxiv, paper_id, parse_atom

TARGET = 'https://github.com/14-TR/agentic-research.git'
OMITTED = 'Statement withheld by the public content policy.'


class PublicError(ValueError):
    """Messages are controller-owned codes, never exception or input strings."""


def checked_day(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except (ValueError, TypeError):
        raise PublicError('invalid_public_date') from None
    return value


def checked_id(value):
    try:
        if not isinstance(value, str) or not value.isascii() or len(value) > 40:
            raise ValueError
        paper_id(value)
    except (ValueError, TypeError):
        raise PublicError('invalid_public_id') from None
    return value


def checked_title(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 400
            or value != ' '.join(value.split())
            or any(not (c.isalnum() or c in " -:,.?!()'’–—+%=αβγ") for c in value)
            or prose(value) == OMITTED or '[redacted]' in prose(value)):
        raise PublicError('unsafe_public_title')
    return value


def prose(value, quotations=(), public_words=frozenset()):
    # public_words is a compatibility argument, not an authorization mechanism.
    from .research_privacy import sanitize
    return sanitize(value, quotations)[0]


def eligible(state):
    return (isinstance(state, dict) and type(state.get('target')) is int
            and 1 <= state['target'] <= 10 and state.get('completed') == state['target']
            and state.get('shortfall') == 0 and isinstance(state.get('synthesis'), dict)
            and isinstance(state.get('items'), list) and len(state['items']) == state['target']
            and all(i.get('status') == 'complete' for i in state['items'])
            and (not state.get('error') or state.get('error', '').startswith('Export failed:')))


def build_document(state, verified_titles):
    if not eligible(state):
        raise PublicError('research_not_complete')
    day = checked_day(state['day'])
    papers = []
    for item in state['items']:
        identity = checked_id(item['paper']['id'])
        if identity not in verified_titles:
            raise PublicError('public_metadata_unverified')
        papers.append({'id': identity, 'title': checked_title(verified_titles[identity]),
                       'sections': {key: [prose(v.get('statement'))
                            for v in item.get('analysis', {}).get(key, [])[:3]] for key in KEYS}})
    allowed = {p['id'] for p in papers}
    if len(allowed) != len(papers):
        raise PublicError('duplicate_public_paper')
    connections = []
    for connection in state['synthesis'].get('connections', [])[:5]:
        refs = connection.get('papers', [])
        if isinstance(refs, list) and 1 <= len(refs) <= 5 and all(p in allowed for p in refs):
            connections.append({'statement': prose(connection.get('statement')), 'papers': refs})
    return {'schema': 2, 'day': day, 'papers': papers, 'connections': connections,
            'proposal': prose(state['synthesis'].get('next_experiment'))}


def validate_document(doc):
    if isinstance(doc, dict) and type(doc.get('schema')) is int and doc['schema'] == 1:
        from .research_public_v1 import validate_document as legacy_validate
        return legacy_validate(doc)
    if (not isinstance(doc, dict) or set(doc) != {'schema', 'day', 'papers', 'connections', 'proposal'}
            or type(doc['schema']) is not int or doc['schema'] != 2):
        raise PublicError('invalid_public_document')
    checked_day(doc['day'])
    if not isinstance(doc['papers'], list) or not 1 <= len(doc['papers']) <= 10:
        raise PublicError('invalid_public_document')
    ids = set()
    for paper in doc['papers']:
        if not isinstance(paper, dict) or set(paper) != {'id', 'title', 'sections'}:
            raise PublicError('invalid_public_document')
        checked_id(paper['id'])
        checked_title(paper['title'])
        if paper['id'] in ids or not isinstance(paper['sections'], dict) or set(paper['sections']) != set(KEYS):
            raise PublicError('invalid_public_document')
        ids.add(paper['id'])
    for paper in doc['papers']:
        for values in paper['sections'].values():
            if not isinstance(values, list) or len(values) > 3 or any(prose(v) != v for v in values):
                raise PublicError('unsafe_public_prose')
    if not isinstance(doc['connections'], list) or len(doc['connections']) > 5 or prose(doc['proposal']) != doc['proposal']:
        raise PublicError('invalid_public_document')
    for item in doc['connections']:
        if (not isinstance(item, dict) or set(item) != {'statement', 'papers'} or prose(item['statement']) != item['statement']
                or not isinstance(item['papers'], list) or not 1 <= len(item['papers']) <= 5
                or any(not isinstance(p, str) or p not in ids for p in item['papers'])):
            raise PublicError('invalid_public_document')
    # Structural validity is not enough: never deliver a bibliography or an
    # all-withheld digest. This is a minimum coverage floor, not a quality proof.
    analytical = set(KEYS) - {'experiments'}
    coverage = []
    for paper in doc['papers']:
        useful = {key for key, values in paper['sections'].items()
                  if any(v != OMITTED and len(re.findall(r'[A-Za-z]+', v)) >= 5 for v in values)}
        if not useful:
            raise PublicError('insufficient_public_content')
        coverage.append(useful & analytical)
    proposal = doc['proposal']
    if (len(set().union(*coverage)) < 3
            or sum(len(keys) >= 2 for keys in coverage) * 2 < len(coverage)
            or proposal == OMITTED or len(proposal.split()) < 20
            or not all(label in proposal.lower() for label in ('comparator:', 'metric:', 'trial budget:'))
            or not re.search(r'\b(?:[1-9]|1[0-9]|20) (?:tasks|trials|runs|examples) per condition; stop after 20 minutes\.', proposal)):
        raise PublicError('insufficient_public_content')
    return doc


def render_document(doc):
    if isinstance(doc, dict) and type(doc.get('schema')) is int and doc['schema'] == 1:
        from .research_public_v1 import render_document as legacy_render
        return legacy_render(doc)
    validate_document(doc)
    lines = ['# Agentic research — ' + doc['day'], '',
             'Model-generated interpretation and proposals; not independently verified.',
             'No experiments were executed. Read the original papers before acting.',
             'Titles and pinned identifiers were checked against arXiv metadata.',
             'Excerpts may omit figures, equations, tables and appendices. This is not a full-paper review.',
             'Privacy checks mask identifiable private spans and omit ambiguous sensitive statements. Quotation fields and private artifacts are not exported; model summaries may repeat public source wording.', '']
    names = {'claims': 'Claims — model summary, not independent evidence', 'methods': 'Methods — model summary',
             'reported_evidence': 'Author-reported evidence — not replicated', 'limitations': 'Limitations — model interpretation',
             'experiments': 'Experiments — model proposals, not executed'}
    for p in doc['papers']:
        lines += ['## ' + p['title'], '', 'https://arxiv.org/abs/' + p['id'], '']
        for key in KEYS:
            values = [s for s in p['sections'][key] if s != OMITTED]
            if values:
                lines += ['### ' + names[key], ''] + ['- ' + s for s in values] + ['']
    connections = [c for c in doc['connections'] if c['statement'] != OMITTED]
    if connections:
        lines += ['## Connections — model interpretation', '']
    for c in connections:
        lines += ['- ' + c['statement'], '  ' + ' '.join('https://arxiv.org/abs/' + p for p in c['papers'])]
    lines += ['', '## Next experiment — model proposal, not executed', '', doc['proposal'], '']
    omitted = sum(s == OMITTED for p in doc['papers'] for values in p['sections'].values() for s in values)
    omitted += sum(c['statement'] == OMITTED for c in doc['connections'])
    if omitted:
        lines += [str(omitted) + ' statements omitted by privacy checks.', '']
    return '\n'.join(lines).encode('utf-8')


def validate_artifact(data, document):
    if not isinstance(data, bytes) or len(data) > 100000 or data != render_document(document):
        raise PublicError('public_artifact_mismatch')
    return document


async def verify_metadata(ids, *, client=None, resolver=None):
    if not isinstance(ids, list) or not 1 <= len(ids) <= 10 or len(set(ids)) != len(ids):
        raise PublicError('invalid_public_ids')
    for identity in ids:
        checked_id(identity)

    async def fetch(web):
        source = Arxiv(web, max_bytes=1000000, total_bytes=1000000, retries=0, resolver=resolver)
        raw = await source.get('https://export.arxiv.org/api/query?' + urlencode({'id_list': ','.join(ids)}))
        papers = parse_atom(raw)
        if len(papers) != len(ids) or {p['id'] for p in papers} != set(ids) or any(p['withdrawn'] for p in papers):
            raise PublicError('public_metadata_unverified')
        return {p['id']: checked_title(p['title']) for p in papers}

    try:
        if client is not None:
            return await asyncio.wait_for(fetch(client), 25)
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as web:
            return await asyncio.wait_for(fetch(web), 25)
    except (ValueError, OSError, RuntimeError, httpx.HTTPError, asyncio.TimeoutError):
        raise PublicError('public_metadata_unavailable') from None


def validate_config(config):
    if config.public_repository == '' and config.public_staging_dir == '':
        return
    if config.public_repository != TARGET or not isinstance(config.public_staging_dir, str) or not config.public_staging_dir:
        raise PublicError('invalid_public_destination')
    from .research_public_git import safe_path
    stage = Path(config.public_staging_dir)
    if not stage.is_absolute() or stage != stage.resolve():
        raise PublicError('unsafe_public_staging')
    safe_path(stage)
    private = [Path(config.data_dir).absolute().resolve(), Path(__file__).resolve().parent.parent]
    if config.obsidian_dir:
        vault = Path(config.obsidian_dir).expanduser().absolute()
        private.append(vault)
        try:
            private.append(vault.resolve())
        except OSError:
            # Vault availability cannot gate this independent channel. The stage
            # still has to be canonical, disjoint from state/code and exclusive.
            pass
    if any(stage == p or stage in p.parents or p in stage.parents for p in private):
        raise PublicError('public_staging_must_be_separate')


def _queue(db):
    with db:
        db.execute('''create table if not exists public_delivery(
            day text primary key, target text not null, payload text, sha256 text,
            status text not null, attempts integer not null default 0,
            last_attempt text not null default '', error text not null default '',
            phase text not null default 'prepare', commit_sha text not null default '')''')
        db.execute('''create table if not exists public_revisions(
            day text not null, revision integer not null, previous_sha256 text not null,
            payload text not null, sha256 text not null, report_sha256 text not null,
            source_sha256 text not null, created_at text not null, status text not null,
            attempts integer not null default 0, error text not null default '',
            commit_sha text not null default '', primary key(day,revision))''')


def _statuses(db):
    if not db.execute("select 1 from sqlite_master where name='public_delivery'").fetchone():
        return []
    names = ('day', 'status', 'attempts', 'error', 'phase', 'commit_sha')
    result = [dict(zip(names, row)) for row in db.execute(
        'select day,status,attempts,error,phase,commit_sha from public_delivery order by day')]
    revisions = _revisions(db)
    for item in result:
        if any(r['day'] == item['day'] and r['status'] == 'delivered' for r in revisions):
            item['status'] = 'superseded'
    result += [{key: r[key] for key in ('day', 'revision', 'status', 'attempts', 'error', 'commit_sha')} for r in revisions]
    return result


def delivery_status(config):
    path = Path(config.data_dir).absolute() / 'research.sqlite3'
    if not path.exists():
        return []
    from contextlib import closing
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        return _statuses(db)


def _revisions(db):
    if not db.execute("select 1 from sqlite_master where name='public_revisions'").fetchone():
        return []
    cursor = db.execute('select * from public_revisions order by day,revision')
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor]


def _documents(db):
    docs, hashes, numbers, pending = {}, {}, {}, set()
    for day, target, payload, sha in db.execute('select day,target,payload,sha256 from public_delivery where payload is not null'):
        if target != TARGET or hashlib.sha256(payload.encode()).hexdigest() != sha:
            raise PublicError('public_outbox_integrity_failed')
        doc = validate_document(json.loads(payload))
        if doc['day'] != checked_day(day):
            raise PublicError('public_outbox_integrity_failed')
        docs[day], hashes[day] = doc, sha
    for row in _revisions(db):
        day = row['day']
        if (day not in docs or day in pending or row['revision'] != numbers.get(day, 0) + 1
                or row['previous_sha256'] != hashes[day] or row['status'] not in ('pending', 'delivered')
                or hashlib.sha256(row['payload'].encode()).hexdigest() != row['sha256']
                or not re.fullmatch('[a-f0-9]{64}', row['source_sha256'])):
            raise PublicError('public_revision_integrity_failed')
        doc = validate_document(json.loads(row['payload']))
        if (doc['schema'] != 2 or doc['day'] != day
                or hashlib.sha256(render_document(doc)).hexdigest() != row['report_sha256']):
            raise PublicError('public_revision_integrity_failed')
        numbers[day], hashes[day] = row['revision'], row['sha256']
        if row['status'] == 'delivered':
            docs[day] = doc
        else:
            pending.add(day)
    return docs


async def sync(store, config, *, attempted=None, client=None, resolver=None):
    """At most seven deliveries/invocation. No model or Obsidian work here.

    Only the new outbox table changes: historical raw runs, mirrors and reports
    are untouched. A frozen public payload survives network and process failure.
    """
    if not config.public_repository:
        return []
    validate_config(config)
    from .research import stamp, verify_completed
    from .research_public_git import GitPublisher
    db = store.db
    _queue(db)
    attempted = attempted if attempted is not None else set()
    with db:
        for (payload,) in db.execute('select payload from runs order by day').fetchall():
            state = json.loads(payload)
            if eligible(state):
                checked_day(state['day'])
                db.execute("insert or ignore into public_delivery(day,target,status) values(?,?,'pending')", (state['day'], TARGET))
    pending = db.execute("select day,payload from public_delivery where status='pending' and day not in (select day from public_revisions) order by last_attempt,day").fetchall()
    for day, payload in pending:
        if day in attempted or len(attempted) >= 7:
            continue
        attempted.add(day)
        with db:
            db.execute('update public_delivery set attempts=attempts+1,last_attempt=? where day=?', (stamp(), day))
        try:
            if payload is None:
                state = store.load(day)
                verify_completed(state)  # Never verify or open its Obsidian export.
                titles = await verify_metadata([i['paper']['id'] for i in state['items']], client=client, resolver=resolver)
                document = build_document(state, titles)
                validate_artifact(render_document(document), document)
                payload = json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
                with db:
                    db.execute("update public_delivery set payload=?,sha256=?,phase='publish' where day=?",
                               (payload, hashlib.sha256(payload.encode()).hexdigest(), day))
            documents = _documents(db)
            publisher = GitPublisher(config.public_staging_dir, config.public_repository)
            commit = await asyncio.to_thread(publisher.publish, day, documents)
            with db:
                db.execute("update public_delivery set status='delivered',error='',phase='confirmed',commit_sha=? where day=?", (commit, day))
        except Exception as error:
            # Only a recognized controller-owned code may leave this boundary.
            # Never forward exception text, filenames or subprocess output.
            code = ('insufficient_public_content' if isinstance(error, PublicError)
                    and error.args == ('insufficient_public_content',) else 'public_delivery_failed')
            # Cancellation/interrupts (BaseException) still propagate; row stays pending.
            with db:
                db.execute('update public_delivery set error=? where day=?', (code, day))
    return _statuses(db)


def retention(document):
    """Count actual output fields, not guessed detector effectiveness."""
    groups = {key: [s for p in document['papers'] for s in p['sections'][key]] for key in KEYS}
    groups['connections'] = [c['statement'] for c in document['connections']]
    groups['proposal'] = [document['proposal']]
    def count(values):
        return {'total': len(values), 'retained': sum(s != OMITTED and '[redacted]' not in s for s in values),
                'redacted': sum(s != OMITTED and '[redacted]' in s for s in values),
                'omitted': sum(s == OMITTED for s in values)}
    return dict(count([s for values in groups.values() for s in values]),
                by_section={key: count(values) for key, values in groups.items()})


async def prepare(config, output, *, day=None, client=None, resolver=None):
    """Sanitized historical preview only: no Git, enqueue, state edits or uploads."""
    from contextlib import closing
    from dataclasses import replace
    from .research import atomic, json_file, verify_completed, locked
    from .research_public_git import README, safe_path
    output = safe_path(output)
    validate_config(replace(config, public_repository=TARGET, public_staging_dir=str(output)))
    if day is not None:
        checked_day(day)
    if output.exists() and list(output.iterdir()):
        raise PublicError('public_preview_must_be_empty')
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = Path(config.data_dir).absolute() / 'research.sqlite3'
    states, prior = [], {}
    if path.exists():
        # A mode=ro WAL reader can still mutate the original SHM read marks.
        # Copy DB + WAL under the research lock, then query a private disposable
        # snapshot OUTSIDE the sanitized output. Never ignore an outstanding WAL.
        with tempfile.TemporaryDirectory(prefix='harness-public-snapshot-') as scratch:
            copied = Path(scratch) / 'snapshot.sqlite3'
            with locked(config.data_dir):
                for suffix in ('', '-wal'):
                    source = safe_path(Path(str(path) + suffix))
                    if source.exists():
                        if not source.is_file() or source.stat().st_size > 1000000000:
                            raise PublicError('unsafe_public_source_state')
                        shutil.copyfile(source, Path(str(copied) + suffix))
            with closing(sqlite3.connect(copied)) as db:
                sql = 'select payload from runs' + (' where day=?' if day else '') + ' order by day limit 3661'
                states = [json.loads(row[0]) for row in db.execute(sql, (day,) if day else ())]
                if db.execute("select 1 from sqlite_master where name='public_delivery'").fetchone():
                    prior = _documents(db)
    if len(states) > 3660:
        raise PublicError('public_preview_limit')
    result = {'prepared': [], 'ineligible': [], 'failed': [], 'uploaded': False, 'retention': {}}
    hashes = {}
    atomic(output / 'README.md', README)
    for state in states:
        identity = checked_day(state['day'])
        if not eligible(state):
            result['ineligible'].append(identity)
            continue
        try:
            verify_completed(state)
            titles = await verify_metadata([i['paper']['id'] for i in state['items']], client=client, resolver=resolver)
            document = build_document(state, titles)
            data = render_document(document)
            validate_artifact(data, document)
            json_file(output / 'documents' / (identity + '.json'), document)
            atomic(output / 'reports' / (identity + '.md'), data)
            hashes[identity] = hashlib.sha256(data).hexdigest()
            result['retention'][identity] = retention(document)
            if identity in prior:
                result['retention'][identity]['previous'] = retention(prior[identity])
            result['prepared'].append(identity)
        except Exception:
            result['failed'].append(identity)
    lines = ['# Public preview retention', '', 'Counts describe actual retained fields, not a privacy guarantee.', '',
             '| Date | Version | Fields | Retained | Redacted | Omitted |', '|---|---|---:|---:|---:|---:|']
    for identity, counts in result['retention'].items():
        for label, value in [('previous frozen', counts.get('previous')), ('preview', counts)]:
            if value is not None:
                lines.append('| %s | %s | %d | %d | %d | %d |' % (identity, label, value['total'],
                    value['retained'], value['redacted'], value['omitted']))
    atomic(output / 'RETENTION.md', '\n'.join(lines) + '\n')
    json_file(output / 'manifest.json', dict(result, report_sha256=hashes))
    return result


async def revise(config, day, *, expected_payload, expected_report, client=None, resolver=None):
    """Explicit, hash-approved correction. Never mutate an original delivery.

    The approved report must be reproduced from completed trusted source state.
    A new immutable payload/provenance row is frozen before Git. A retry reuses it.
    Only this explicit command drains revisions; daily sync cannot enroll them.
    """
    checked_day(day)
    if (not config.public_repository or not isinstance(expected_payload, str)
            or not re.fullmatch('[a-f0-9]{64}', expected_payload)
            or not isinstance(expected_report, str) or not re.fullmatch('[a-f0-9]{64}', expected_report)):
        raise PublicError('invalid_public_revision_approval')
    validate_config(config)
    from .research import Store, locked, stamp, encode, verify_completed
    from .research_public_git import GitPublisher
    with locked(config.data_dir) as root:
        store = Store(root)
        try:
            db = store.db
            _queue(db)
            documents = _documents(db)  # Validate the entire immutable chain first.
            base = db.execute('select payload,sha256 from public_delivery where day=?', (day,)).fetchone()
            if base is None or base[0] is None:
                raise PublicError('public_revision_requires_frozen_original')
            rows = [r for r in _revisions(db) if r['day'] == day]
            row = next((r for r in rows if r['previous_sha256'] == expected_payload
                        and r['report_sha256'] == expected_report), None)
            history = {base[1]: base[0], **{r['sha256']: r['payload'] for r in rows}}
            if row is None:
                if (rows and rows[-1]['status'] == 'pending') or expected_payload != (rows[-1]['sha256'] if rows else base[1]):
                    raise PublicError('public_revision_stale_approval')
                state = store.load(day)
                if not eligible(state):
                    raise PublicError('research_not_complete')
                verify_completed(state)
                titles = await verify_metadata([i['paper']['id'] for i in state['items']], client=client, resolver=resolver)
                document = build_document(state, titles)
                if hashlib.sha256(render_document(document)).hexdigest() != expected_report:
                    raise PublicError('public_revision_preview_changed')
                payload = json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
                digest = hashlib.sha256(payload.encode()).hexdigest()
                if digest == expected_payload:
                    raise PublicError('public_revision_unchanged')
                row = dict(day=day, revision=len(rows)+1, previous_sha256=expected_payload,
                           payload=payload, sha256=digest, report_sha256=expected_report,
                           source_sha256=hashlib.sha256(encode(state).encode()).hexdigest(),
                           created_at=stamp(), status='pending')
                with db:
                    db.execute("""insert into public_revisions(day,revision,previous_sha256,payload,sha256,
                        report_sha256,source_sha256,created_at,status) values(:day,:revision,:previous_sha256,
                        :payload,:sha256,:report_sha256,:source_sha256,:created_at,:status)""", row)
            elif row != rows[-1]:
                raise PublicError('public_revision_superseded')
            document = json.loads(row['payload'])
            previous = json.loads(history[expected_payload])
            documents[day] = document
            with db:
                db.execute('update public_revisions set attempts=attempts+1 where day=? and revision=?', (day, row['revision']))
            try:
                commit = await asyncio.to_thread(GitPublisher(config.public_staging_dir, config.public_repository).publish,
                                                 day, documents, previous=previous)
            except Exception:
                with db:
                    db.execute("update public_revisions set error='public_revision_failed' where day=? and revision=?", (day, row['revision']))
                raise PublicError('public_revision_failed') from None
            with db:
                db.execute("update public_revisions set status='delivered',commit_sha=?,error='' where day=? and revision=?",
                           (commit, day, row['revision']))
            return dict(day=day, revision=row['revision'], status='delivered', commit_sha=commit,
                        sha256=row['sha256'], report_sha256=row['report_sha256'])
        finally:
            store.db.close()


async def retry(config):
    if not config.public_repository:
        raise PublicError('public_delivery_not_enabled')
    validate_config(config)
    from .research import Store, locked
    with locked(config.data_dir) as root:
        store = Store(root)
        try:
            return await sync(store, config)
        finally:
            store.db.close()
