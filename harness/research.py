"""Resumable daily research. Deterministic orchestration, not an autonomous shell.

No chat tools are registered or exposed here. Models only return validated data.
State is local SQLite plus immutable source/analysis artifacts; no vector store.
"""
import argparse
import asyncio
from contextlib import contextmanager, AsyncExitStack
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import tempfile
import uuid
from urllib.parse import urlencode, urlsplit

import httpx

from .research_sources import Arxiv, DEFAULT_QUERY, TOPICS, parse_atom, select_papers, extract_html, excerpts
from .research_analysis import ResearchModel, KEYS
from . import research_public

DELIVERY_DRAIN_LIMIT = 7


@dataclass
class Config:
    data_dir: str = '.harness/research'
    topic: str = 'agentic-system-engineering'
    query: str = DEFAULT_QUERY
    topics: dict = field(default_factory=lambda: dict(TOPICS))
    count: int = 5
    fresh_days: int = 30
    search_limit: int = 80
    search_pages: int = 3
    model: str = 'qwen2.5:7b'
    host: str = 'http://127.0.0.1:11434'
    context_chars: int = 22000
    model_context: int = 12288
    max_tokens: int = 2000
    max_model_calls: int = 14
    model_timeout: int = 180
    runtime_seconds: int = 1800
    download_bytes: int = 20 * 1024 * 1024
    total_download_bytes: int = 100 * 1024 * 1024
    history_count: int = 10
    obsidian_dir: str = ''
    public_repository: str = ''
    public_staging_dir: str = ''

    def validate(self):
        limits = {'count': (1, 10), 'fresh_days': (1, 365), 'search_limit': (5, 100), 'search_pages': (1, 5),
                  'context_chars': (2000, 48000), 'model_context': (4096, 32768), 'max_tokens': (300, 4000),
                  'max_model_calls': (1, 30), 'model_timeout': (1, 600), 'runtime_seconds': (1, 7200),
                  'download_bytes': (1000, 40 * 1024 * 1024), 'total_download_bytes': (1000, 300 * 1024 * 1024),
                  'history_count': (0, 10)}
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError('Invalid configuration: ' + name)
        u = urlsplit(self.host)
        if u.scheme != 'http' or u.hostname not in {'127.0.0.1', 'localhost', '::1'} or u.username or u.password or u.path not in ('', '/') or u.query or u.fragment:
            raise ValueError('Research requires a credential-free local Ollama host')
        if not self.model or not isinstance(self.query, str) or not 1 <= len(self.query) <= 2000:
            raise ValueError('Invalid model or query')
        if not isinstance(self.topics, dict) or not self.topics or any(not isinstance(k, str) or not isinstance(v, list) or not v or any(not isinstance(t, str) or not t for t in v) for k, v in self.topics.items()):
            raise ValueError('Invalid topic terms')
        if self.topic != 'agentic-system-engineering':
            raise ValueError('This configuration supports agentic-system-engineering')
        research_public.validate_config(self)
        return self


def stamp():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify_artifacts(state):
    for filename, sha in state.get('artifact_hashes', {}).items():
        path = Path(filename)
        if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != sha:
            raise ValueError('Artifact integrity check failed: ' + filename)
    if state.get('export'):
        path = Path(state['export'])
        if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != state['export_sha256']:
            raise ValueError('Export integrity changed; preserving human edits: ' + str(path))


def paper_artifacts(item):
    directory = Path(item['report']).parent
    return [directory / name for name in ('metadata.json', 'source.json', 'analysis.json', 'report.md')] + [
        directory / name for name in item['source']['hashes']]


def verify_completed(state):
    trusted = state.get('artifact_hashes', {})
    for item in state['items']:
        if item['status'] != 'complete':
            continue
        paths = paper_artifacts(item)
        if any(str(p) not in trusted for p in paths):
            raise ValueError('Completed paper integrity hashes missing; manual recovery required')
        for name, sha in item['source']['hashes'].items():
            if trusted[str(Path(item['report']).parent / name)] != sha:
                raise ValueError('Completed source integrity ledger disagrees with provenance')
        verify_artifacts({'artifact_hashes': {str(p): trusted[str(p)] for p in paths}})


def atomic(path, data):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in [path] + list(path.parents)):
        raise ValueError('Refusing symlink artifact: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data if isinstance(data, bytes) else data.encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def json_file(path, value):
    atomic(path, encode(value))


def immutable_json(path, value):
    # The run lock serializes writers. Never replace an existing call event.
    if path.exists() or path.is_symlink():
        if path.is_symlink() or path.read_bytes() != encode(value).encode():
            raise ValueError('Call receipt integrity conflict: ' + str(path))
        return
    json_file(path, value)


@contextmanager
def locked(root):
    root = Path(root).absolute()
    for p in [root] + list(root.parents):
        if p.is_symlink():
            raise ValueError('Research state must not use symlink directories')
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root / 'run.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another research command holds the lock')
        yield root
    finally:
        os.close(fd)


class Store:
    def __init__(self, root):
        self.root = root
        self.db = sqlite3.connect(root / 'research.sqlite3')
        self.db.execute('pragma journal_mode=WAL')
        self.db.execute('create table if not exists runs(day text primary key, payload text not null)')
        self.db.execute('create table if not exists catalog(base_id text primary key, metadata text not null, read_version text, analysis text)')
        migrate_delivery = not self.db.execute("select 1 from sqlite_master where name='delivery_queue'").fetchone()
        self.db.execute('create table if not exists delivery_queue(day text primary key, last_attempt text not null, error text not null)')
        self.db.execute('create table if not exists call_events(day text, attempt text, number integer, phase text, payload text not null, primary key(day,attempt,number,phase))')
        if migrate_delivery:
            # Import old undelivered reports using their established hashes, never
            # infer provenance from current bytes or mark their papers unread.
            for (payload,) in self.db.execute('select payload from runs').fetchall():
                state = json.loads(payload)
                if state.get('report') and state['config'].get('obsidian_dir') and (not state.get('export') or state.get('error', '').startswith('Export failed:')):
                    self.db.execute('insert into delivery_queue values(?,?,?)', (state['day'], '', state.get('error', 'Pending delivery')))
        self.db.commit()

    def load(self, day):
        row = self.db.execute('select payload from runs where day=?', (day,)).fetchone()
        return json.loads(row[0]) if row else None

    def _write_state(self, state):
        state['updated_at'] = stamp()
        self.db.execute('insert or replace into runs values(?,?)', (state['day'], encode(state)))
        delivery = state.get('delivery', {})
        if delivery.get('status') == 'pending':
            self.db.execute('insert or replace into delivery_queue values(?,?,?)',
                            (state['day'], delivery.get('last_attempt', ''), delivery.get('error', 'Pending delivery')))
        elif delivery.get('status') == 'delivered':
            self.db.execute('delete from delivery_queue where day=?', (state['day'],))

    def save(self, state):
        with self.db:
            self._write_state(state)
        json_file(self.root / 'runs' / state['day'] / 'state.json', state)

    def complete_paper(self, state, paper, analysis):
        with self.db:
            self._write_state(state)
            self.mark_read(paper, analysis)
        # The ordinary following save mirrors this checkpoint. A mirror failure
        # must not turn an already committed paper back into a failed item.

    def call_event(self, day, attempt, number, value):
        phase = value['status']
        payload = encode(value)
        with self.db:
            self.db.execute('insert into call_events values(?,?,?,?,?)', (day, attempt, number, phase, payload))
        immutable_json(self.root / 'runs' / day / ('call-%s-%02d-%s.json' % (attempt, number, phase)), value)

    def reconcile_calls(self, state):
        calls = {}
        for attempt, number, phase, payload in self.db.execute('select attempt,number,phase,payload from call_events where day=?', (state['day'],)):
            value = json.loads(payload)
            immutable_json(self.root / 'runs' / state['day'] / ('call-%s-%02d-%s.json' % (attempt, number, phase)), value)
            calls.setdefault((attempt, number), {})[phase] = value
        metrics = dict(state['legacy_model_metrics'])
        for events in calls.values():
            final = next((events[k] for k in ('accepted', 'rejected', 'interrupted', 'response', 'started') if k in events), {})
            response = events.get('response', {}).get('response', {})
            for key, amount in {'model_calls': 1, 'input_tokens': response.get('prompt_eval_count', 0),
                'output_tokens': response.get('eval_count', 0), 'model_seconds': final.get('elapsed_seconds', 0),
                'validation_failures': int(final.get('validation_failure', False)),
                'model_failures': int(final.get('status') == 'rejected'),
                'known_response_calls': int('response' in events), 'unknown_usage_calls': int('response' not in events)}.items():
                metrics[key] = metrics.get(key, 0) + amount
        state['metrics'].update(metrics)

    def pending_deliveries(self):
        return [dict(day=day, error=error) for day, error in self.db.execute('select day,error from delivery_queue order by last_attempt,day')]

    def catalog(self, papers):
        with self.db:
            for p in papers:
                self.db.execute('insert into catalog(base_id,metadata) values(?,?) on conflict(base_id) do update set metadata=excluded.metadata',
                                (p['base_id'], encode(p)))

    def read_ids(self):
        return {r[0] for r in self.db.execute('select base_id from catalog where read_version is not null')}

    def history(self, limit):
        rows = self.db.execute('select metadata,read_version,analysis from catalog where analysis is not null order by rowid desc limit ?', (limit,))
        result = []
        for metadata, version, analysis in rows:
            p = json.loads(metadata)
            if not p['withdrawn']:
                a = json.loads(analysis)
                # Bounded history for model context; retains version and provenance.
                result.append({'id': version, 'title': p['title'], 'claims': a['claims'][:1], 'experiments': a['experiments'][:1]})
        return result

    def mark_read(self, p, analysis):
        # Only called inside complete_paper's transaction with the run checkpoint.
        self.db.execute('update catalog set read_version=?,analysis=? where base_id=?', (p['id'], encode(analysis), p['base_id']))


async def discover(config, fetch, store, run_dir, today):
    read_ids = store.read_ids()
    papers, selected = [], []
    warnings = []
    # Recheck known recent papers for withdrawal/revision notices; don't reread revisions.
    known = list(store.db.execute('select base_id from catalog where read_version is not null order by rowid desc limit 20'))
    if known:
        raw = await fetch.get('https://export.arxiv.org/api/query?' + urlencode({'id_list': ','.join(r[0] for r in known)}))
        atomic(run_dir / 'history-metadata.xml', raw)
        refreshed = parse_atom(raw)
        store.catalog(refreshed)
        warnings = [p['id'] + ' is now marked withdrawn; exclude its evidence.' for p in refreshed if p['withdrawn']]
    for page in range(config.search_pages):
        url = 'https://export.arxiv.org/api/query?' + urlencode({'search_query': config.query, 'start': page * config.search_limit,
                  'max_results': config.search_limit, 'sortBy': 'submittedDate', 'sortOrder': 'descending'})
        raw = await fetch.get(url)
        atomic(run_dir / ('discovery-%d.xml' % page), raw)
        batch = parse_atom(raw)
        papers.extend(batch)
        store.catalog(batch)
        selected = select_papers(papers, read_ids, today, config.count, config.fresh_days, config.topics)
        if len(selected) >= config.count or len(batch) < config.search_limit:
            break
    return selected, warnings, len({p['base_id'] for p in papers})


async def acquire(config, fetch, directory, paper):
    cached = directory / 'source.json'
    if cached.exists():
        source = json.loads(cached.read_text())
        for filename, sha in source['hashes'].items():
            if digest((directory / filename).read_bytes()) != sha:
                raise ValueError('Cached source hash mismatch: ' + filename)
        return source
    directory.mkdir(parents=True, exist_ok=True)
    html_error = ''
    try:
        raw = await fetch.get('https://arxiv.org/html/' + paper['id'])
        atomic(directory / 'original.html', raw)
        blocks = extract_html(raw.decode('utf-8', errors='replace'))
        original, kind = 'original.html', 'html'
    except (httpx.HTTPError, ValueError) as exc:
        html_error = str(exc)[:300]
        raw = await fetch.get('https://arxiv.org/pdf/' + paper['id'])
        if not raw.startswith(b'%PDF-'):
            raise ValueError('arXiv returned a non-PDF body')
        atomic(directory / 'original.pdf', raw)
        blocks = await extract_pdf(directory / 'original.pdf')
        original, kind = 'original.pdf', 'pdf'
    context, coverage = excerpts(blocks, config.context_chars)
    json_file(directory / 'extracted.json', blocks)
    json_file(directory / 'context.json', context)
    atomic(directory / 'extracted.txt', '\n\n'.join('[' + b['locator'] + '] ' + b['text'] for b in blocks))
    source = {'format': kind, 'url': 'https://arxiv.org/' + kind + '/' + paper['id'], 'retrieved_at': stamp(),
              'coverage': coverage, 'html_fallback_reason': html_error,
              'hashes': {name: digest((directory / name).read_bytes()) for name in (original, 'extracted.json', 'context.json', 'extracted.txt')}}
    json_file(cached, source)
    return source


async def extract_pdf(path):
    # Isolated bounded parser process, never paper-supplied code. See worker module.
    process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'harness.research_pdf', str(path),
                                                  stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 45)
        if process.returncode:
            raise RuntimeError('PDF extraction failed: ' + stderr.decode(errors='replace')[-500:])
        if len(stdout) > 3 * 1024 * 1024:
            raise ValueError('PDF extraction output budget exceeded')
        return json.loads(stdout)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def safe_text(value):
    # Do not export active HTML, wikilinks, embeds or source-supplied URLs.
    return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('[', '&#91;').replace(']', '&#93;')


def paper_report(p, analysis, source, config):
    coverage = source['coverage']
    lines = ['# ' + safe_text(p['title']), '', p['url'], '',
             '- Pinned ID: ' + p['id'], '- Published: ' + p['published'] + '; updated: ' + p['updated'],
             '- Authors: ' + safe_text(', '.join(p['authors'])),
             '- Selection: ' + p['freshness'] + '; topics: ' + ', '.join(p['topics']),
             '- Local model: ' + config.model,
             '- Source: ' + source['format'] + '; included %d / %d extracted characters; truncated: %s' %
             (coverage['included_chars'], coverage['extracted_chars'], coverage['truncated']),
             '- Coverage caveat: ' + coverage['caveat'],
             '- Locators refer to extracted.txt (H = HTML block, P = PDF page/block).',
             '- Quote checks: exact whitespace-normalized substring at locator; NOT semantic entailment or replication.',
             '', '## Independently verified', '', 'None. No experiments or author code were executed.', '']
    names = {'claims': 'Author claims (model summary)', 'methods': 'Author methods (model summary)',
             'reported_evidence': 'Author-reported evidence (not replicated)',
             'limitations': 'Limitations (model interpretation; see attribution)', 'experiments': 'Practical experiments (model proposals, not executed)'}
    for key in KEYS:
        lines += ['## ' + names[key], '']
        for item in analysis[key]:
            lines += ['- ' + safe_text(item['statement']),
                      '  - Source ' + item['locator'] + ': “' + safe_text(item['quote']) + '”']
        lines.append('')
    return '\n'.join(lines)


def daily_report(state):
    lines = ['# Agentic system engineering — ' + state['day'], '',
             '- Status: ' + state['status'], '- Completed: %d / %d; shortfall: %d' % (state['completed'], state['target'], state['shortfall']),
             '- Selection: fresh first, then explicitly labeled backlog; bounded arXiv candidate pool.',
             '- Research only: no code execution, external actions, or autonomous changes.',
             '', '## Papers', '']
    for item in state['items']:
        p = item['paper']
        lines += ['- [%s](%s) — %s; %s' % (safe_text(p['title']), p['url'], p['freshness'], item['status'])]
        if item.get('source'):
            c = item['source']['coverage']
            lines.append('  - Coverage: %s; %d/%d extracted characters; truncated=%s.' % (item['source']['format'], c['included_chars'], c['extracted_chars'], c['truncated']))
        if item.get('error'):
            lines.append('  - Failure: ' + safe_text(item['error']))
    lines += ['', '## Cross-paper synthesis — model interpretation', '']
    if state.get('synthesis'):
        for item in state['synthesis']['connections']:
            lines.append('- ' + safe_text(item['statement']) + ' ' + ' '.join('[%s](https://arxiv.org/abs/%s)' % (p, p) for p in item['papers']))
        lines += ['', '### Proposed bounded experiment (not executed)', '', safe_text(state['synthesis']['next_experiment'])]
    else:
        lines += ['Synthesis unavailable: ' + safe_text(state.get('synthesis_error', 'no completed papers'))]
    lines += ['', '## Historical context', '', 'Previously read analyses supplied: ' + str(len(state.get('history', []))) + '.']
    for p in state.get('history', []):
        lines.append('- [%s](https://arxiv.org/abs/%s)' % (p['id'], p['id']))
    lines += ['', '## Independently verified', '', 'None. Quotes/locators were mechanically checked; evidence and causal claims were not independently replicated.',
              'Text extraction and sampled context can omit figures, equations, tables, sections and appendices. Do not infer absence or novelty from this digest.',
              '', '## Metrics', '', '```json', encode(state['metrics']), '```', '', '## Warnings', '']
    lines += [safe_text(w) for w in state.get('warnings', [])] or ['None.']
    if state.get('error'):
        lines.append(safe_text(state['error']))
    return '\n'.join(lines) + '\n'


def export_report(config, state, text):
    if not config.obsidian_dir:
        return
    vault = Path(config.obsidian_dir).expanduser().resolve(strict=True)
    parent = vault / 'Projects' / 'Agent Harness' / 'Daily Runs'
    parent.mkdir(parents=True, exist_ok=True)
    if parent.resolve() != parent:
        raise ValueError('Export destination must not be redirected by symlinks')
    target = parent / ('Agentic Research ' + state['day'] + '.md')
    data = ('<!-- Generated by qwen-harness research; edit other notes instead. -->\n'
            '[[Projects/Agent Harness/Home|Agent Harness]]\n\n' + text).encode()
    if target.exists():
        current = digest(target.read_bytes())
        if current == digest(data):
            state['export'], state['export_sha256'] = str(target), current
            return
        if current != state.get('export_sha256'):
            raise ValueError('Obsidian export was edited or already exists; preserving it: ' + str(target))
    atomic(target, data)
    if target.read_bytes() != data:
        raise OSError('Obsidian export readback mismatch')
    state['export'], state['export_sha256'] = str(target), digest(data)


def deliver(store, state):
    delivery = state.setdefault('delivery', {'status': 'pending'})
    delivery.update(last_attempt=stamp(), attempts=delivery.get('attempts', 0) + 1)
    try:
        verify_completed(state)
        # Verify the frozen local digest, but let export_report protect a changed
        # or unavailable previous export instead of treating it as source evidence.
        verify_artifacts({'artifact_hashes': state['artifact_hashes']})
        export_report(Config(**state['config']), state, Path(state['report']).read_text())
        delivery.update(status='delivered', delivered_at=stamp())
        delivery.pop('error', None)
        if state.get('error', '').startswith('Export failed:'):
            state.pop('error')
            state['status'] = 'complete' if not state['shortfall'] and state.get('synthesis') else 'partial'
    except (ValueError, OSError) as exc:
        delivery.update(status='pending', error=str(exc))
        state.update(status='partial', error='Export failed: ' + str(exc))
    store.save(state)


def drain_deliveries(store, current_day):
    pending = [p for p in store.pending_deliveries() if p['day'] != current_day]
    for entry in pending[:DELIVERY_DRAIN_LIMIT]:
        deliver(store, store.load(entry['day']))
    return store.pending_deliveries()


async def run(config, *, day=None, retry=False, web_client=None, model_client=None, resolver=None, interval=3.1):
    config.validate()
    day = day or date.today().isoformat()
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError('Use an ISO calendar date')
    with locked(config.data_dir) as root:
        store = Store(root)
        try:
            async with AsyncExitStack() as stack:
                web = web_client or await stack.enter_async_context(httpx.AsyncClient(trust_env=False, follow_redirects=False))
                local = model_client or await stack.enter_async_context(httpx.AsyncClient(base_url=config.host, trust_env=False, follow_redirects=False))
                fetch = Arxiv(web, interval=interval, max_bytes=config.download_bytes, total_bytes=config.total_download_bytes, resolver=resolver)
                model = ResearchModel(local, config.model, max_calls=config.max_model_calls, max_tokens=config.max_tokens,
                                      context=config.model_context, timeout=config.model_timeout)
                # Delivery opt-in is independent of frozen research/model settings.
                research_config = {k: v for k, v in asdict(config).items() if not k.startswith('public_')}
                signature = digest(encode(research_config).encode())
                state = store.load(day)
                if state and state['config_sha256'] != signature:
                    raise ValueError('Configuration changed for existing day; use its original config or a separate state directory')
                public_attempted = set()
                public_deliveries = await research_public.sync(store, config, attempted=public_attempted,
                                                               client=web, resolver=resolver)
                if state:
                    # Preflight before any network/model work or checkpoint writes.
                    verify_completed(state)
                pending = drain_deliveries(store, day)
                if (state and any(p['day'] == day for p in pending)
                        and state.get('synthesis') and not state.get('shortfall')
                        and (not state.get('error') or state.get('error', '').startswith('Export failed:'))):
                    # A crash can occur on either side of the external write.
                    # Retry exactly the queued bytes, not a freshly rendered digest.
                    deliver(store, state)
                    return dict(state, idempotent=True, pending_deliveries=store.pending_deliveries(), public_deliveries=public_deliveries)
                if state and state['status'] == 'complete':
                    # A true no-op: verify, but do not redownload/rewrite outputs.
                    verify_artifacts(state)
                    return dict(state, idempotent=True, pending_deliveries=pending, public_deliveries=public_deliveries)
                if not state:
                    state = {'day': day, 'config_sha256': signature, 'config': asdict(config), 'created_at': stamp(), 'status': 'running',
                             'target': config.count, 'items': [], 'selected': False, 'history': [], 'warnings': [], 'metrics': {}, 'attempts': []}
                    store.save(state)
                start = time.monotonic()
                run_dir = root / 'runs' / day
                state.setdefault('legacy_model_metrics', {k: state['metrics'].get(k, 0) for k in model.metrics})
                store.reconcile_calls(state)
                for prior in state['attempts']:
                    if prior.get('status') == 'running':
                        prior['status'] = 'interrupted'
                attempt: dict = {'id': uuid.uuid4().hex, 'at': stamp(), 'status': 'running'}
                state['attempts'].append(attempt)
                store.save(state)  # Allocate the durable identity BEFORE a call.
                def call_event(n, value):
                    store.call_event(day, attempt['id'], n, value)
                    attempt.update(fetch.metrics, elapsed_seconds=round(time.monotonic() - start, 3))
                    store.save(state)
                model.event_sink = call_event
                async def work():
                    if not state['selected']:
                        selected, warnings, candidates = await discover(config, fetch, store, run_dir, date.fromisoformat(day))
                        state.update(items=[{'paper': p, 'status': 'pending'} for p in selected], selected=True,
                                     history=store.history(config.history_count), warnings=warnings, candidates=candidates)
                        json_file(run_dir / 'selection.json', selected)
                        store.save(state)
                    for item in state['items']:
                        if item['status'] == 'complete' or (item['status'] == 'failed' and not retry):
                            continue
                        p = item['paper']
                        directory = root / 'papers' / p['id'].replace('/', '_')
                        item['status'] = 'running'
                        item.pop('error', None)
                        store.save(state)
                        try:
                            json_file(directory / 'metadata.json', p)
                            source = await acquire(config, fetch, directory, p)
                            item['source'] = source
                            blocks = json.loads((directory / 'context.json').read_text())
                            result = await model.analyze(p, blocks, source['coverage'])
                            json_file(directory / 'analysis.json', result)
                            atomic(directory / 'report.md', paper_report(p, result, source, config))
                            item.update(status='complete', analysis=result, report=str(directory / 'report.md'))
                            state.setdefault('artifact_hashes', {}).update({str(p): digest(p.read_bytes()) for p in paper_artifacts(item)})
                            store.complete_paper(state, p, result)
                        except (ValueError, RuntimeError, OSError, httpx.HTTPError) as exc:
                            item.update(status='failed', error=type(exc).__name__ + ': ' + str(exc)[:500])
                        store.save(state)
                    current = [{'id': i['paper']['id'], 'title': i['paper']['title'], 'analysis': i['analysis']}
                               for i in state['items'] if i['status'] == 'complete']
                    current_ids = [p['id'] for p in current]
                    if current and state.get('synthesis_ids') != current_ids:
                        # A prior partial synthesis must never certify a new set.
                        state.pop('synthesis', None)
                        state.pop('synthesis_ids', None)
                        store.save(state)
                        try:
                            state['synthesis'] = await model.synthesize(current, state['history'])
                            state['synthesis_ids'] = current_ids
                            state.pop('synthesis_error', None)
                        except (ValueError, RuntimeError, OSError, httpx.HTTPError) as exc:
                            state['synthesis_error'] = str(exc)[:500]
                    store.save(state)
                try:
                    await asyncio.wait_for(work(), config.runtime_seconds)
                    state.pop('error', None)
                except asyncio.TimeoutError:
                    state['error'] = 'Total runtime budget exhausted; rerun resumes checkpoints'
                except (ValueError, RuntimeError, OSError, httpx.HTTPError) as exc:
                    state['error'] = type(exc).__name__ + ': ' + str(exc)[:500]
                finally:
                    for item in state['items']:
                        if item['status'] == 'running':
                            item['status'] = 'pending'
                    state['completed'] = sum(i['status'] == 'complete' for i in state['items'])
                    state['shortfall'] = config.count - state['completed']
                    state['status'] = 'complete' if not state['shortfall'] and state.get('synthesis') and not state.get('error') else 'partial'
                    attempt.update(fetch.metrics, **model.metrics, elapsed_seconds=round(time.monotonic() - start, 3), status='finished')
                    for key in list(fetch.metrics) + ['elapsed_seconds']:
                        state['metrics'][key] = sum(a.get(key, 0) for a in state['attempts'])
                    store.reconcile_calls(state)
                    state['metrics']['failures'] = sum(i['status'] == 'failed' for i in state['items'])
                    state['metrics']['completed'] = state['completed']
                    state['metrics']['shortfall'] = state['shortfall']
                    immutable_json(run_dir / ('model-receipts-%s.json' % attempt['id']), model.receipts)
                    state['pending_deliveries'] = [p for p in store.pending_deliveries() if p['day'] != day]
                    state['warnings'] = [w for w in state['warnings'] if not w.startswith('Pending delivery ')] + [
                        'Pending delivery ' + p['day'] + ': ' + p['error'] for p in state['pending_deliveries']]
                    state['report'] = str(run_dir / 'daily.md')
                    text = daily_report(state)
                    atomic(state['report'], text)
                    # Only the deliberately regenerated daily report advances here.
                    state.setdefault('artifact_hashes', {})[state['report']] = digest(Path(state['report']).read_bytes())
                    if config.obsidian_dir:
                        state['delivery'] = dict(state.get('delivery', {}), status='pending')
                    store.save(state)
                    # Public delivery must run before any vault access, including
                    # an export error. Its outbox never consumes daily.md.
                    public_deliveries = await research_public.sync(store, config, attempted=public_attempted,
                                                                   client=web, resolver=resolver)
                    if config.obsidian_dir:
                        deliver(store, state)
                return dict(state, public_deliveries=public_deliveries)
        finally:
            store.db.close()


def load_config(path=None):
    if path is None:
        return Config().validate()
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict):
        raise ValueError('Configuration must be a JSON object')
    try:
        return Config(**value).validate()
    except TypeError as exc:
        raise ValueError('Unknown configuration key: ' + str(exc)) from exc


def status(config, day=None):
    path = Path(config.data_dir).absolute() / 'research.sqlite3'
    if not path.exists():
        return []
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        if day:
            rows = db.execute('select payload from runs where day=?', (day,))
        else:
            rows = db.execute('select payload from runs order by day desc limit 30')
        return [json.loads(r[0]) for r in rows]
    finally:
        db.close()


def launchd(config, config_path, output, hour=8, minute=15):
    import plistlib
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError('Invalid local schedule time')
    if config_path is None:
        raise ValueError('launchd requires an explicit --config file')
    logs = Path(config.data_dir).absolute() / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    value = {'Label': 'com.qwen-harness.agentic-research',
             'ProgramArguments': [os.path.abspath(sys.executable), '-m', 'harness.research', '--config', str(Path(config_path).absolute()), 'run'],
             'WorkingDirectory': str(Path.cwd()), 'StartCalendarInterval': {'Hour': hour, 'Minute': minute},
             'RunAtLoad': False, 'KeepAlive': False, 'ProcessType': 'Background', 'LowPriorityIO': True,
             'Umask': 0o077, 'StandardOutPath': str(logs / 'launchd.stdout.log'),
             'StandardErrorPath': str(logs / 'launchd.stderr.log')}
    atomic(Path(output), plistlib.dumps(value))
    return {'plist': str(Path(output).absolute()), 'installed': False, 'schedule': '%02d:%02d local system timezone' % (hour, minute)}


async def search(config):
    with locked(config.data_dir) as root:
        store = Store(root)
        try:
            directory = root / 'search' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as web:
                fetch = Arxiv(web, max_bytes=config.download_bytes, total_bytes=config.total_download_bytes)
                selected, warnings, candidates = await asyncio.wait_for(discover(config, fetch, store, directory, date.today()), config.runtime_seconds)
            return {'selected': selected, 'shortfall': config.count - len(selected), 'candidates': candidates,
                    'warnings': warnings, 'metrics': fetch.metrics, 'artifacts': str(directory)}
        finally:
            store.db.close()


def main():
    parser = argparse.ArgumentParser(description='Local, read-only agentic-system-engineering daily research')
    parser.add_argument('--config', type=Path, help='JSON config; relative paths resolve from the working directory')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('search', help='Preview unread selection; save metadata, do not mark papers read')
    for name in ('run', 'retry', 'status'):
        sub = commands.add_parser(name)
        sub.add_argument('--date', help='ISO local calendar day; default today (status lists recent runs)')
    commands.add_parser('public-status', help='Read public outbox status only')
    commands.add_parser('public-retry', help='Publish completed research only; requires explicit public opt-in')
    sub = commands.add_parser('public-prepare', help='Build private sanitized previews; never publish or enqueue')
    sub.add_argument('--output', required=True, type=Path)
    sub.add_argument('--date', help='Optional ISO date; otherwise all historical eligible runs')
    sub = commands.add_parser('launchd', help='Generate only; never install or activate')
    sub.add_argument('--output', required=True, type=Path)
    sub.add_argument('--hour', type=int, default=8)
    sub.add_argument('--minute', type=int, default=15)
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        if args.command == 'public-status':
            result = research_public.delivery_status(config)
        elif args.command == 'public-prepare':
            result = asyncio.run(research_public.prepare(config, args.output, day=args.date))
        elif args.command == 'public-retry':
            result = asyncio.run(research_public.retry(config))
        elif args.command == 'status':
            result = status(config, args.date)
        elif args.command == 'search':
            result = asyncio.run(search(config))
        elif args.command == 'launchd':
            result = launchd(config, args.config, args.output, args.hour, args.minute)
        else:
            if args.command == 'retry' and not status(config, args.date or date.today().isoformat()):
                raise ValueError('No existing run to retry for this day')
            result = asyncio.run(run(config, day=args.date, retry=args.command == 'retry'))
        print(encode(result))
        if args.command == 'public-prepare' and isinstance(result, dict) and result['failed']:
            raise SystemExit(2)
        if args.command == 'public-retry' and isinstance(result, list) and any(d['status'] == 'pending' for d in result):
            raise SystemExit(2)
        if args.command in ('run', 'retry') and isinstance(result, dict) and (result['status'] != 'complete' or result.get('pending_deliveries')
                or any(d['status'] == 'pending' for d in result.get('public_deliveries', []))):
            raise SystemExit(2)
    except KeyboardInterrupt:
        raise SystemExit(130)
    except (ValueError, RuntimeError, OSError, httpx.HTTPError, sqlite3.Error) as exc:
        if args.command.startswith('public-'):
            parser.exit(1, 'Error: public_command_failed\n')
        parser.exit(1, 'Error: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
