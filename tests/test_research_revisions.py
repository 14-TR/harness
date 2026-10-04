"""Append-only corrections of frozen deliveries, against real local bare Git."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from harness import research, research_public as public, research_public_v1 as legacy
from harness.research_public_git import GitPublisher
from test_research_repair import Fixture
from test_research_public import ID, TITLE


def payload(document):
    return json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(root, *args):
    return subprocess.check_output(['/usr/bin/git', *args], cwd=root, stderr=subprocess.DEVNULL).decode().strip()


class RevisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_correction_preserves_original_and_appends_readback_verified_commit(self):
        self.assertTrue(hasattr(public, 'revise'), 'Explicit hash-bound correction command missing')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            f = Fixture(root, vault=True)  # unavailable vault must not block correction
            state = await f.run()
            day = state['day']
            bare = root / 'remote.git'
            git(root, 'init', '--bare', str(bare))
            f.config.public_repository = public.TARGET
            f.config.public_staging_dir = str(root / 'stage')
            old = legacy.build_document(state, {ID: TITLE})
            new = public.build_document(state, {ID: TITLE})
            old_payload = payload(old)
            prior_hash = sha(old_payload.encode())
            approved_hash = sha(public.render_document(new))
            store = research.Store(root / 'state')
            public._queue(store.db)
            with patch.object(GitPublisher, '_remote', return_value=str(bare)):
                first = GitPublisher(root / 'stage', public.TARGET).publish(day, {day: old})
            with store.db:
                store.db.execute("insert into public_delivery(day,target,payload,sha256,status,phase,commit_sha) values(?,?,?,?,'delivered','confirmed',?)",
                                 (day, public.TARGET, old_payload, prior_hash, first))
            original = store.db.execute('select * from public_delivery').fetchall()
            private_tables = {table: store.db.execute('select * from ' + table).fetchall()
                              for table in ('runs', 'catalog', 'call_events', 'delivery_queue')}
            store.db.close()
            calls = dict(f.calls)
            with patch.object(GitPublisher, '_remote', return_value=str(bare)), patch.object(
                    public, 'verify_metadata', return_value={ID: TITLE}):
                corrected = await public.revise(f.config, day, expected_payload=prior_hash,
                                               expected_report=approved_hash)
            second = git(bare, 'rev-parse', 'main')
            self.assertEqual(corrected['commit_sha'], second)
            self.assertNotEqual(first, second)
            self.assertEqual(git(bare, 'rev-parse', 'main^'), first)
            self.assertEqual(git(bare, 'log', '-1', '--format=%s'), 'Correct research ' + day)
            self.assertEqual(git(bare, 'show', 'main:reports/' + day + '.md'), public.render_document(new).decode().strip())
            self.assertEqual(git(bare, 'show', first + ':reports/' + day + '.md'), legacy.render_document(old).decode().strip())
            store = research.Store(root / 'state')
            self.assertEqual(store.db.execute('select * from public_delivery').fetchall(), original)
            self.assertEqual({table: store.db.execute('select * from ' + table).fetchall() for table in private_tables}, private_tables)
            self.assertEqual(public._documents(store.db)[day], new)
            revision = store.db.execute('select previous_sha256,payload,sha256,status from public_revisions').fetchone()
            self.assertEqual(revision, (prior_hash, payload(new), sha(payload(new).encode()), 'delivered'))
            store.db.close()
            with patch.object(GitPublisher, '_remote', return_value=str(bare)), patch.object(
                    public, 'verify_metadata', side_effect=AssertionError('retry must use frozen approved bytes')):
                again = await public.revise(f.config, day, expected_payload=prior_hash, expected_report=approved_hash)
            self.assertEqual(again['commit_sha'], second)
            self.assertEqual(git(bare, 'rev-list', '--count', 'main'), '2')
            self.assertEqual(dict(f.calls), calls)
    async def seed(self, root, *, frozen_only=False):
        import harness.research_public_git as transport
        f = Fixture(root, vault=True)
        state = await f.run()
        bare = root / 'remote.git'
        git(root, 'init', '--bare', str(bare))
        f.config.public_repository, f.config.public_staging_dir = public.TARGET, str(root / 'stage')
        old = legacy.build_document(state, {ID: TITLE})
        with patch.object(GitPublisher, '_remote', return_value=str(bare)), patch.object(transport, 'README', transport.LEGACY_README):
            first = '' if frozen_only else GitPublisher(root / 'stage', public.TARGET).publish(state['day'], {state['day']: old})
        store = research.Store(root / 'state')
        public._queue(store.db)
        old_payload = payload(old)
        with store.db:
            store.db.execute('insert into public_delivery(day,target,payload,sha256,status,phase,commit_sha) values(?,?,?,?,?,?,?)',
                             (state['day'], public.TARGET, old_payload, sha(old_payload.encode()),
                              'pending' if frozen_only else 'delivered', 'publish' if frozen_only else 'confirmed', first))
        store.db.close()
        return f, state, bare, old, first

    async def test_revision_approvals_and_tampered_chain_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            f, state, bare, old, first = await self.seed(root)
            day = state['day']
            previous = sha(payload(old).encode())
            approved = sha(public.render_document(public.build_document(state, {ID: TITLE})))
            with patch.object(public, 'verify_metadata', return_value={ID: TITLE}), patch.object(
                    GitPublisher, 'publish', side_effect=AssertionError('unapproved revision must not reach Git')):
                for a, b in [('f'*64, approved), (previous, 'f'*64), ('BAD', approved)]:
                    with self.assertRaises(public.PublicError):
                        await public.revise(f.config, day, expected_payload=a, expected_report=b)
            store = research.Store(root / 'state')
            self.assertEqual(store.db.execute('select count(*) from public_revisions').fetchone(), (0,))
            self.assertEqual(store.db.execute('select payload,sha256 from public_delivery').fetchone(), (payload(old), previous))
            store.db.close()
            with patch.object(public, 'verify_metadata', return_value={ID: TITLE}), patch.object(
                    GitPublisher, 'publish', side_effect=OSError('private fixture message')):
                with self.assertRaisesRegex(public.PublicError, '^public_revision_failed$'):
                    await public.revise(f.config, day, expected_payload=previous, expected_report=approved)
            store = research.Store(root / 'state')
            self.assertEqual(store.db.execute('select status,error from public_revisions').fetchone(), ('pending', 'public_revision_failed'))
            with store.db:
                store.db.execute("update public_revisions set previous_sha256=?", ('e'*64,))
            store.db.close()
            with patch.object(GitPublisher, 'publish', side_effect=AssertionError('tampered ledger')):
                with self.assertRaisesRegex(public.PublicError, '^public_revision_integrity_failed$'):
                    await public.revise(f.config, day, expected_payload=previous, expected_report=approved)
            self.assertEqual(git(bare, 'rev-parse', 'main'), first)

    async def test_correction_crashes_after_commit_and_push_reuse_exact_revision(self):
        import os
        import sys
        from dataclasses import asdict
        for boundary in ('commit', 'push'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                f, state, bare, old, first = await self.seed(root)
                day = state['day']
                previous = sha(payload(old).encode())
                approved = sha(public.render_document(public.build_document(state, {ID: TITLE})))
                config = root / 'config.json'
                config.write_text(json.dumps(asdict(f.config)))
                script = '''import asyncio,os,sys
from harness import research, research_public as p
from harness.research_public_git import GitPublisher
async def metadata(*a, **k): return {'2609.12345v1': 'Agent memory evaluation'}
p.verify_metadata = metadata
GitPublisher._remote = lambda self: sys.argv[2]
original = GitPublisher._git
def crash(self, *args, **kwargs):
    result = original(self, *args, **kwargs)
    if args[0] == sys.argv[3]: os._exit(91)
    return result
GitPublisher._git = crash
asyncio.run(p.revise(research.load_config(sys.argv[1]), sys.argv[4], expected_payload=sys.argv[5], expected_report=sys.argv[6]))
'''
                child = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', script, str(config), str(bare), boundary, day, previous, approved],
                                                capture_output=True, text=True, timeout=20)
                self.assertEqual(child.returncode, 91, child.stderr)
                staged_commit = git(root / 'stage/repo', 'rev-parse', 'HEAD')
                store = research.Store(root / 'state')
                frozen = store.db.execute('select payload,sha256,source_sha256,previous_sha256 from public_revisions').fetchone()
                self.assertEqual(store.db.execute('select status from public_revisions').fetchone(), ('pending',))
                with patch.object(GitPublisher, 'publish', side_effect=AssertionError('daily sync cannot drain explicit revisions')):
                    await public.sync(store, f.config)
                store.db.close()
                with patch.object(GitPublisher, '_remote', return_value=str(bare)), patch.object(
                        public, 'verify_metadata', side_effect=AssertionError('must retry frozen revision')):
                    result = await public.revise(f.config, day, expected_payload=previous, expected_report=approved)
                self.assertEqual(result['commit_sha'], staged_commit)
                self.assertEqual(git(bare, 'rev-parse', 'main^'), first)
                self.assertEqual(git(bare, 'rev-list', '--count', 'main'), '2')
                store = research.Store(root / 'state')
                self.assertEqual(store.db.execute('select payload,sha256,source_sha256,previous_sha256 from public_revisions').fetchone(), frozen)
                self.assertEqual(store.db.execute('select payload,sha256 from public_delivery').fetchone(), (payload(old), previous))
                store.db.close()

    async def test_never_published_frozen_original_can_be_explicitly_revised(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            f, state, bare, old, _ = await self.seed(root, frozen_only=True)
            previous = sha(payload(old).encode())
            approved = sha(public.render_document(public.build_document(state, {ID: TITLE})))
            with patch.object(GitPublisher, '_remote', return_value=str(bare)), patch.object(public, 'verify_metadata', return_value={ID: TITLE}):
                await public.revise(f.config, state['day'], expected_payload=previous, expected_report=approved)
                store = research.Store(root / 'state')
                with patch.object(GitPublisher, 'publish', side_effect=AssertionError('superseded original must stay frozen')):
                    await public.sync(store, f.config)
                self.assertEqual(store.db.execute('select status,sha256 from public_delivery').fetchone(), ('pending', previous))
                self.assertEqual(public._statuses(store.db)[0]['status'], 'superseded')
                store.db.close()
            self.assertEqual(git(bare, 'rev-list', '--count', 'main'), '1')

    async def test_explicit_legacy_sync_preserves_frozen_history_without_needing_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            f, state, bare, old, first = await self.seed(root)
            f.paper_id = '2609.12346v1'
            later = await f.run('2026-09-28')
            self.assertEqual(later['delivery']['status'], 'pending')
            store = research.Store(root / 'state')
            # Compatibility coverage only; daily run/retry no longer publishes.
            with patch.object(GitPublisher, '_remote', return_value=str(bare)), patch.object(
                    public, 'verify_metadata', return_value={f.paper_id: TITLE}):
                await public.sync(store, f.config)
            docs = public._documents(store.db)
            self.assertEqual(docs[state['day']], old)
            self.assertEqual(docs['2026-09-28']['schema'], 2)
            self.assertEqual(store.db.execute('select count(*) from public_revisions').fetchone(), (0,))
            store.db.close()
            self.assertEqual(git(bare, 'show', 'main:reports/' + state['day'] + '.md'), legacy.render_document(old).decode().strip())
            self.assertNotIn(public.OMITTED, git(bare, 'show', 'main:reports/2026-09-28.md'))


if __name__ == '__main__':
    unittest.main()
