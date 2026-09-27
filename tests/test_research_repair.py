"""Review regressions: offline transports, real SQLite/files/process deaths."""
import asyncio
import copy
from collections import Counter
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
from harness import research
from test_research_analysis import raw_analysis, proposal
from test_research_sources import atom, entry

DAY = '2026-09-27'


class Fixture:
    def __init__(self, root, vault=False, fail_synthesis=False):
        self.root = Path(root).resolve()
        self.config = research.Config(data_dir=str(self.root / 'state'), count=1, search_limit=5,
            obsidian_dir=str(self.root / 'vault') if vault else '')
        self.calls = Counter()
        self.fail_synthesis = fail_synthesis
        self.paper_id = '2609.12345v1'

    async def handler(self, request):
        if request.url.path == '/api/query':
            self.calls['search'] += 1
            return httpx.Response(200, content=atom([entry(id=self.paper_id)]))
        if request.url.path.startswith('/html/'):
            self.calls['html'] += 1
            return httpx.Response(200, text='<article><p>' + 'We evaluate agent memory with ten tasks. Accuracy is author-reported. ' * 35 + '</p></article>')
        payload = json.loads(json.loads(request.content)['messages'][1]['content'].split('\nPrevious output rejected:')[0])
        if 'source_blocks' in payload:
            self.calls['analysis'] += 1
            value = raw_analysis()
        else:
            self.calls['synthesis'] += 1
            if self.fail_synthesis:
                return httpx.Response(503)
            value = {'connections': [{'statement': 'Compare retrieval approaches.', 'papers': [p['id'] for p in payload['current']]}],
                     'next_experiment': proposal()}
        return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(value)},
            'prompt_eval_count': 200, 'eval_count': 100})

    async def run(self, day=DAY, retry=False):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.handler)) as web, httpx.AsyncClient(
                base_url=self.config.host, transport=httpx.MockTransport(self.handler)) as model:
            return await research.run(self.config, day=day, retry=retry, web_client=web, model_client=model,
                interval=0, resolver=lambda _: ['93.184.216.34'])


class RepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_subprocess_crash_around_export_reuses_queued_digest(self):
        for boundary in ('before_export', 'after_export'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as tmp:
                child = await asyncio.to_thread(subprocess.run,
                    [sys.executable, str(Path(__file__).resolve()), 'crash-child', tmp, boundary],
                    capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
                self.assertEqual(child.returncode, 91, child.stdout + child.stderr)
                f = Fixture(tmp, vault=True)
                before = research.status(f.config, DAY)[0]
                before_bytes = Path(before['report']).read_bytes()
                after = await f.run()
                self.assertEqual(after['delivery']['status'], 'delivered')
                self.assertTrue(Path(after['export']).is_file())
                self.assertFalse(f.calls)
                self.assertEqual(before['attempts'], after['attempts'])
                self.assertEqual(before_bytes, Path(after['report']).read_bytes())

    async def test_checkpoint_mirror_error_cannot_downgrade_committed_paper(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Fixture(tmp)
            original = research.json_file
            failed = False
            def mirror(path, value):
                nonlocal failed
                if not failed and Path(path).name == 'state.json' and any(i['status'] == 'complete' for i in value['items']):
                    failed = True
                    raise OSError('Injected mirror failure after DB commit')
                original(path, value)
            with patch.object(research, 'json_file', mirror):
                first = await f.run()
            self.assertTrue(failed)
            self.assertEqual(first['items'][0]['status'], 'complete')
            second = await f.run(retry=True)
            self.assertEqual(second['status'], 'complete')
            self.assertEqual(f.calls['analysis'], 1)

    async def test_subprocess_response_and_unknown_call_boundaries(self):
        for boundary in ('after_started', 'before_response', 'after_response', 'after_rejected'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as tmp:
                child = await asyncio.to_thread(subprocess.run,
                    [sys.executable, str(Path(__file__).resolve()), 'crash-child', tmp, boundary],
                    capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
                self.assertEqual(child.returncode, 91, child.stdout + child.stderr)
                f = Fixture(tmp)
                before = {p: p.read_bytes() for p in Path(f.config.data_dir).rglob('call-*.json')}
                state = await f.run()
                unknown = int(boundary == 'after_started')
                self.assertEqual(state['metrics']['model_calls'], 3)
                self.assertEqual(state['metrics']['known_response_calls'], 3 - unknown)
                self.assertEqual(state['metrics']['unknown_usage_calls'], unknown)
                self.assertEqual(state['metrics']['input_tokens'], (3 - unknown) * 200)
                self.assertEqual(state['metrics']['output_tokens'], (3 - unknown) * 100)
                self.assertEqual(state['metrics']['validation_failures'], int(boundary == 'after_rejected'))
                self.assertEqual(state['metrics']['model_failures'], int(boundary == 'after_rejected'))
                for path, data in before.items():
                    self.assertEqual(path.read_bytes(), data)

    async def test_subprocess_crash_boundaries_preserve_known_usage_and_receipts(self):
        for boundary in ('before_commit', 'during_commit', 'after_commit', 'before_receipt', 'after_receipt'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as tmp:
                child = await asyncio.to_thread(subprocess.run, [sys.executable, str(Path(__file__).resolve()), 'crash-child', tmp, boundary],
                    capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
                self.assertEqual(child.returncode, 91, child.stdout + child.stderr)
                f = Fixture(tmp)
                saved = research.status(f.config, DAY)[0]
                with sqlite3.connect(Path(f.config.data_dir) / 'research.sqlite3') as db:
                    read = db.execute('select read_version from catalog').fetchone()[0]
                self.assertEqual(bool(read), saved['items'][0]['status'] == 'complete', 'Read ledger and run completion must be atomic')
                before = {p: p.read_bytes() for p in Path(f.config.data_dir).rglob('call-*.json')}
                resumed = await f.run()
                expected_calls = 2 if boundary == 'after_commit' else 3
                self.assertEqual(f.calls['analysis'], 0 if boundary == 'after_commit' else 1)
                self.assertEqual(resumed['status'], 'complete')
                self.assertEqual(resumed['metrics']['model_calls'], expected_calls)
                self.assertEqual(resumed['metrics']['input_tokens'], expected_calls * 200)
                self.assertEqual(resumed['metrics']['output_tokens'], expected_calls * 100)
                for path, data in before.items():
                    self.assertEqual(path.read_bytes(), data, 'A precrash receipt was overwritten')
                self.assertEqual(len({a.get('id') for a in resumed['attempts']}), 2)
                self.assertTrue(all(a.get('id') for a in resumed['attempts']))

    async def test_next_day_drains_pending_digest_without_reanalysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Fixture(tmp, vault=True)
            first = await f.run()
            self.assertIn('Export failed', first['error'])
            old_hashes = dict(first['artifact_hashes'])
            (f.root / 'vault').mkdir()
            f.paper_id = '2609.12346v1'
            second = await f.run('2026-09-28')
            prior = research.status(f.config, DAY)[0]
            self.assertEqual(second['status'], 'complete')
            self.assertTrue(Path(prior.get('export', '/no-export')).is_file())
            self.assertEqual(prior['delivery']['status'], 'delivered')
            self.assertEqual(f.calls['analysis'], 2)
            self.assertEqual(f.calls['synthesis'], 2)
            self.assertEqual(old_hashes, prior['artifact_hashes'])
            self.assertFalse(second.get('pending_deliveries'))

    async def test_pending_delivery_failure_remains_visible_and_preserves_human_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Fixture(tmp, vault=True)
            first = await f.run()
            target = f.root / 'vault/Projects/Agent Harness/Daily Runs' / ('Agentic Research ' + DAY + '.md')
            target.parent.mkdir(parents=True)
            target.write_text('Human research annotations')
            f.paper_id = '2609.12346v1'
            second = await f.run('2026-09-28')
            self.assertEqual(second['status'], 'complete')
            self.assertIn(DAY, [d['day'] for d in second.get('pending_deliveries', [])])
            self.assertIn(DAY, '\n'.join(second['warnings']))
            self.assertEqual(target.read_text(), 'Human research annotations')
            self.assertEqual(f.calls['analysis'], 2)
            self.assertEqual(research.status(f.config, DAY)[0]['delivery']['status'], 'pending')

    async def test_same_day_delivery_retry_does_not_reanalyze(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Fixture(tmp, vault=True)
            await f.run()
            (f.root / 'vault').mkdir()
            second = await f.run(retry=True)
            self.assertEqual(second['status'], 'complete')
            self.assertEqual(f.calls['analysis'], 1)
            self.assertEqual(f.calls['synthesis'], 1)

    async def test_delivery_drain_is_bounded_and_legacy_failure_is_imported(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Fixture(tmp, vault=True)
            first = await f.run()
            store = research.Store(Path(f.config.data_dir))
            try:
                for n in range(1, 10):
                    prior = copy.deepcopy(first)
                    prior['day'] = '2026-09-%02d' % n
                    prior.pop('delivery')
                    store.save(prior)
                store.db.execute('drop table delivery_queue')
                store.db.commit()
            finally:
                store.db.close()
            (f.root / 'vault').mkdir()
            f.paper_id = '2609.12346v1'
            second = await f.run('2026-09-28')
            self.assertEqual(len(second['pending_deliveries']), 3)  # ten pending minus seven bounded deliveries
            self.assertEqual(f.calls['analysis'], 2)
            old = [s for s in research.status(f.config) if s['day'] != '2026-09-28']
            self.assertEqual(sum(s.get('delivery', {}).get('status') == 'delivered' for s in old), 7)
            # A completed-current-day invocation still drains without model calls.
            again = await f.run('2026-09-28')
            self.assertTrue(again['idempotent'])
            self.assertEqual(again['pending_deliveries'], [])
            self.assertEqual(f.calls['analysis'], 2)

    async def test_partial_resume_rejects_each_changed_completed_artifact_without_work(self):
        for name in ('original.html', 'extracted.json', 'context.json', 'extracted.txt', 'metadata.json', 'source.json', 'analysis.json', 'report.md'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                f = Fixture(tmp, fail_synthesis=True)
                first = await f.run()
                self.assertEqual((first['completed'], first['status']), (1, 'partial'))
                original_state = research.status(f.config, DAY)[0]
                path = Path(f.config.data_dir) / 'papers/2609.12345v1' / name
                path.write_bytes(path.read_bytes() + b' changed')
                changed = path.read_bytes()
                before = dict(f.calls)
                f.fail_synthesis = False
                with self.assertRaisesRegex(ValueError, 'integrity'):
                    await f.run(retry=True)
                self.assertEqual(dict(f.calls), before)
                self.assertEqual(research.status(f.config, DAY)[0], original_state)
                self.assertEqual(path.read_bytes(), changed)

    async def test_clean_partial_resume_preserves_trusted_paper_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Fixture(tmp, fail_synthesis=True)
            first = await f.run()
            trusted = {p: h for p, h in first['artifact_hashes'].items() if '/papers/' in p}
            f.fail_synthesis = False
            second = await f.run(retry=True)
            self.assertEqual(second['status'], 'complete')
            self.assertEqual(f.calls['analysis'], 1)
            self.assertTrue(trusted)
            self.assertEqual(trusted, {p: h for p, h in second['artifact_hashes'].items() if '/papers/' in p})
            research.verify_artifacts(second)


async def crash_child(root, boundary):
    original_connect = sqlite3.connect
    class CrashConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            result = super().execute(sql, *args, **kwargs)
            if boundary == 'during_commit' and sql.startswith('update catalog set read_version'):
                os._exit(91)
            return result

        def __exit__(self, *args):
            completing = self.in_transaction and self.execute('select 1 from catalog where read_version is not null').fetchone()
            if completing and boundary == 'before_commit':
                os._exit(91)
            result = super().__exit__(*args)
            if completing and boundary == 'after_commit':
                os._exit(91)
            return result
    research.sqlite3.connect = lambda *a, **k: original_connect(*a, factory=CrashConnection, **k)
    original_json = research.json_file
    def receipt(path, value):
        match = Path(path).name.startswith('call-') and isinstance(value, dict) and value.get('status') == 'accepted'
        phase = value.get('status') if isinstance(value, dict) and Path(path).name.startswith('call-') else None
        if match and boundary == 'before_receipt':
            os._exit(91)
        if phase == 'response' and boundary == 'before_response':
            os._exit(91)
        original_json(path, value)
        if match and boundary == 'after_receipt':
            os._exit(91)
        if (phase, boundary) in (('started', 'after_started'), ('response', 'after_response'), ('rejected', 'after_rejected')):
            os._exit(91)
    research.json_file = receipt
    if boundary == 'after_rejected':
        global raw_analysis
        original_raw = raw_analysis
        def rejected_raw():
            value = original_raw()
            value['experiments'] = [{'statement': 'An unbounded experiment.', 'locator': 'H0001'}]
            return value
        raw_analysis = rejected_raw
    f = Fixture(root, vault=boundary.endswith('_export'))
    if boundary.endswith('_export'):
        (f.root / 'vault').mkdir()
        original_export = research.export_report
        def export(config, state, text):
            if boundary == 'before_export':
                os._exit(91)
            original_export(config, state, text)
            os._exit(91)
        research.export_report = export
    await f.run()


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'crash-child':
        asyncio.run(crash_child(sys.argv[2], sys.argv[3]))
    else:
        unittest.main()
