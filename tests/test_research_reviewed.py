"""Exact-byte reviewed publication; network writes use local bare fixtures only."""
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_research_public import completed, ID, TITLE


def module(test):
    test.assertIsNotNone(importlib.util.find_spec('harness.research_reviewed'),
                         'Independent-review publication boundary is missing')
    return importlib.import_module('harness.research_reviewed')


class ReviewedTests(unittest.TestCase):
    def test_render_preserves_science_selects_analysis_not_private_artifacts(self):
        reviewed = module(self)
        state = completed()
        statement = 'Contact mechanics explains friction in deformable solids.'
        state['items'][0]['analysis']['claims'][0]['statement'] = statement
        text = reviewed.render(state, {ID: TITLE}).decode()
        self.assertIn(statement, text)
        self.assertIn('https://arxiv.org/abs/' + ID, text)
        self.assertIn('not executed', text)
        for private in ('PRIVATE TITLE', 'RAW SOURCE QUOTE', '/private', 'provider_log'):
            self.assertNotIn(private, text)
        self.assertIn('Author-reported evidence', text)
        self.assertIn('not a full-paper review', text)

    def test_unverified_cross_paper_mapping_is_not_published_as_provenance(self):
        reviewed = module(self)
        state = completed()
        state['synthesis']['connections'][0]['statement'] = 'Qwen-Planner-Agent supports these results.'
        rendered = reviewed.render(state, {ID: TITLE}).decode()
        self.assertNotIn('Qwen-Planner-Agent supports these results.', rendered)
        self.assertIn('Cross-paper synthesis links are omitted', rendered)

    def test_approval_binds_exact_bundle_bytes_before_git(self):
        reviewed = module(self)
        self.assertTrue(hasattr(reviewed, 'load_approved'), 'SHA-bound review gate missing')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            bundle, approval = make_bundle(root, reviewed)
            digest = hashlib.sha256(approval.read_bytes()).hexdigest()
            manifest, files, approved = reviewed.load_approved(bundle, approval, digest)
            self.assertEqual(files['README.md'], reviewed.README)
            self.assertEqual(approved['decision'], 'approve')
            (bundle / 'reports/2026-09-28.md').write_bytes(b'Changed after review')
            with self.assertRaisesRegex(ValueError, 'review_bundle_content_changed'):
                reviewed.load_approved(bundle, approval, digest)
            with self.assertRaisesRegex(ValueError, 'review_approval_changed'):
                reviewed.load_approved(bundle, approval, '0' * 64)

    def test_review_cli_checks_approval_without_network(self):
        reviewed = module(self)
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            bundle, approval = make_bundle(root, reviewed)
            digest = hashlib.sha256(approval.read_bytes()).hexdigest()
            result = subprocess.run([sys.executable, '-m', 'harness.research_reviewed', 'check',
                '--bundle', str(bundle), '--approval', str(approval), '--approval-sha256', digest],
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"approved": true', result.stdout)

    def test_legacy_publish_commands_cannot_bypass_review(self):
        from harness import research
        import sys
        for command in (['public-retry'], ['public-revise', '--date', '2026-09-28',
                '--expected-payload-sha256', 'a' * 64, '--expected-report-sha256', 'b' * 64]):
            with self.subTest(command=command), patch.object(sys, 'argv', ['research'] + command), patch.object(
                    research.research_public, 'retry', side_effect=AssertionError('Legacy retry reached')), patch.object(
                    research.research_public, 'revise', side_effect=AssertionError('Legacy revise reached')):
                with self.assertRaises(SystemExit) as caught:
                    research.main()
                self.assertEqual(caught.exception.code, 1)

    def test_receipt_alias_cannot_overwrite_approved_report(self):
        reviewed = module(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            bundle, approval = make_bundle(root, reviewed)
            (root / 'alias').mkdir()
            receipt = root / 'alias/../bundle/reports/2026-09-28.md'
            with patch.object(reviewed.ReviewedGit, 'deliver', side_effect=AssertionError('Receipt alias must fail before Git')):
                with self.assertRaisesRegex(ValueError, 'review_receipt_must_be_separate'):
                    reviewed.publish(bundle, approval, hashlib.sha256(approval.read_bytes()).hexdigest(), receipt)

    def test_rejected_review_never_reaches_git(self):
        reviewed = module(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            bundle, approval = make_bundle(root, reviewed)
            for changes in ({'decision': 'reject'}, {'target': 'https://example.org/private.git'},
                            {'checks': {'privacy': True}}, {'reviewer': ''}):
                original = approval.read_text()
                value = json.loads(original)
                value.update(changes)
                approval.write_text(json.dumps(value))
                with patch.object(reviewed.ReviewedGit, 'deliver', side_effect=AssertionError('No Git before approval')):
                    with self.assertRaisesRegex(ValueError, 'independent_review_required'):
                        reviewed.publish(bundle, approval, hashlib.sha256(approval.read_bytes()).hexdigest(), root / 'receipt.json')
                approval.write_text(original)

    def test_bundle_extra_files_symlinks_and_manifest_tampering_fail_closed(self):
        reviewed = module(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            bundle, approval = make_bundle(root, reviewed)
            digest = hashlib.sha256(approval.read_bytes()).hexdigest()
            extra = bundle / 'private-notes.txt'
            extra.write_text('Must not leave the private bundle')
            with self.assertRaisesRegex(ValueError, 'unexpected_review_bundle_file'):
                reviewed.load_approved(bundle, approval, digest)
            extra.unlink()
            report = bundle / 'reports/2026-09-28.md'
            external = root / 'external.md'
            report.rename(external)
            report.symlink_to(external)
            with self.assertRaises(ValueError):
                reviewed.load_approved(bundle, approval, digest)
            report.unlink()
            external.rename(report)
            manifest = bundle / 'manifest.json'
            manifest.write_bytes(manifest.read_bytes() + b' ')
            with self.assertRaisesRegex(ValueError, 'review_bundle_manifest_changed'):
                reviewed.load_approved(bundle, approval, digest)

    def test_append_correction_preserves_history_and_old_approval_cannot_rollback(self):
        reviewed = module(self)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            bare = root / 'remote.git'
            subprocess.run(['/usr/bin/git', 'init', '--bare', str(bare)], check=True, capture_output=True)
            bundle, approval = make_bundle(root / 'one', reviewed)
            digest = hashlib.sha256(approval.read_bytes()).hexdigest()
            with patch.object(reviewed.ReviewedGit, '_remote', return_value=str(bare)):
                first = reviewed.publish(bundle, approval, digest, root / 'receipt1.json')
                corrected, review2 = make_bundle(root / 'two', reviewed, first['commit'], first['files'])
                report = corrected / 'reports/2026-09-28.md'
                report.write_bytes(report.read_bytes() + b'\nReviewed correction; no experiment was executed.\n')
                manifest = json.loads((corrected / 'manifest.json').read_text())
                manifest['files']['reports/2026-09-28.md'] = hashlib.sha256(report.read_bytes()).hexdigest()
                (corrected / 'manifest.json').write_text(json.dumps(manifest))
                value = json.loads(review2.read_text())
                value['bundle_sha256'] = hashlib.sha256((corrected / 'manifest.json').read_bytes()).hexdigest()
                review2.write_text(json.dumps(value))
                second = reviewed.publish(corrected, review2, hashlib.sha256(review2.read_bytes()).hexdigest(), root / 'receipt2.json')
                with self.assertRaisesRegex(ValueError, 'review_remote_changed'):
                    reviewed.publish(bundle, approval, digest, root / 'receipt1.json')
            parent = subprocess.run(['/usr/bin/git', '--git-dir=' + str(bare), 'rev-parse', 'main^'], check=True, capture_output=True).stdout.decode().strip()
            self.assertEqual(parent, first['commit'])
            self.assertNotEqual(first['commit'], second['commit'])
            old = subprocess.run(['/usr/bin/git', '--git-dir=' + str(bare), 'show', 'main^:reports/2026-09-28.md'], check=True, capture_output=True).stdout
            self.assertEqual(old, (bundle / 'reports/2026-09-28.md').read_bytes())

    def test_crash_after_push_before_receipt_recovers_without_duplicate_commit(self):
        reviewed = module(self)
        from harness import research
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            bare = root / 'remote.git'
            subprocess.run(['/usr/bin/git', 'init', '--bare', str(bare)], check=True, capture_output=True)
            bundle, approval = make_bundle(root, reviewed)
            digest = hashlib.sha256(approval.read_bytes()).hexdigest()
            receipt = root / 'receipt.json'
            original = research.atomic
            def crash(path, data):
                if Path(path) == receipt:
                    raise OSError('simulated crash after remote confirmation')
                return original(path, data)
            with patch.object(reviewed.ReviewedGit, '_remote', return_value=str(bare)):
                with patch.object(research, 'atomic', side_effect=crash):
                    with self.assertRaises(OSError):
                        reviewed.publish(bundle, approval, digest, receipt)
                self.assertFalse(receipt.exists())
                second = reviewed.publish(bundle, approval, digest, receipt)
            self.assertEqual(json.loads(receipt.read_text())['commit'], second['commit'])
            count = subprocess.run(['/usr/bin/git', '--git-dir=' + str(bare), 'rev-list', '--count', 'main'], check=True, capture_output=True).stdout.strip()
            self.assertEqual(count, b'1')

    def test_real_local_git_push_readback_receipt_and_idempotent_retry(self):
        reviewed = module(self)
        self.assertTrue(hasattr(reviewed, 'publish'), 'Reviewed Git publisher missing')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            bare = root / 'remote.git'
            subprocess.run(['/usr/bin/git', 'init', '--bare', str(bare)], check=True, capture_output=True)
            bundle, approval = make_bundle(root, reviewed)
            digest = hashlib.sha256(approval.read_bytes()).hexdigest()
            receipt = root / 'receipt.json'
            with patch.object(reviewed.ReviewedGit, '_remote', return_value=str(bare)):
                first = reviewed.publish(bundle, approval, digest, receipt)
                second = reviewed.publish(bundle, approval, digest, receipt)
            self.assertEqual(first['commit'], second['commit'])
            self.assertEqual(first['status'], 'confirmed')
            self.assertEqual(json.loads(receipt.read_text()), second)
            actual = subprocess.run(['/usr/bin/git', '--git-dir=' + str(bare), 'show',
                'main:reports/2026-09-28.md'], check=True, capture_output=True).stdout
            self.assertEqual(actual, (bundle / 'reports/2026-09-28.md').read_bytes())
            count = subprocess.run(['/usr/bin/git', '--git-dir=' + str(bare), 'rev-list', '--count', 'main'],
                                   check=True, capture_output=True).stdout.strip()
            self.assertEqual(count, b'1')
            names = subprocess.run(['/usr/bin/git', '--git-dir=' + str(bare), 'ls-tree', '-r', '--name-only', 'main'],
                                   check=True, capture_output=True).stdout.decode().splitlines()
            self.assertEqual(names, ['README.md', 'reports/2026-09-28.md'])


class PrepareTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_run_does_not_automatically_publish_even_with_legacy_config(self):
        reviewed = module(self)
        from test_research_repair import Fixture
        from harness import research_public
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = Fixture(root)
            fixture.config.public_repository = reviewed.TARGET
            fixture.config.public_staging_dir = str(root / 'legacy-stage')
            with patch.object(research_public, 'sync', side_effect=AssertionError('Legacy automatic path must be disabled')):
                first = await fixture.run()
                second = await fixture.run()
            self.assertEqual(first['status'], 'complete')
            self.assertTrue(second['idempotent'])
            self.assertEqual(research_public.delivery_status(fixture.config), [])

    async def test_prepare_checks_sources_preserves_private_state_and_never_pushes(self):
        reviewed = module(self)
        self.assertTrue(hasattr(reviewed, 'prepare'), 'Reviewed preparation missing')
        from test_research_repair import Fixture
        import httpx
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = Fixture(root)
            await fixture.run()
            before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in (root / 'state').rglob('*') if p.is_file()}
            bare = root / 'remote.git'
            subprocess.run(['/usr/bin/git', 'init', '--bare', str(bare)], check=True, capture_output=True)
            async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handler)) as client:
                with patch.object(reviewed.ReviewedGit, '_remote', return_value=str(bare)), patch.object(
                        reviewed.ReviewedGit, 'deliver', side_effect=AssertionError('Preparation cannot push')):
                    result = await reviewed.prepare(fixture.config, root / 'preview', client=client,
                        resolver=lambda _: ['93.184.216.34'])
            self.assertEqual(result['prepared'], ['2026-09-27'])
            self.assertFalse(result['uploaded'])
            after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in (root / 'state').rglob('*') if p.is_file()}
            self.assertEqual(before, after)
            manifest = json.loads((root / 'preview/manifest.json').read_text())
            self.assertEqual(set(manifest['sources']), {'2026-09-27'})
            self.assertEqual(set(manifest['files']), {'README.md', 'reports/2026-09-27.md'})
            self.assertFalse((root / 'preview/.git').exists())


def make_bundle(root, reviewed, base_commit='', base_files=None):
    bundle = root / 'bundle'
    (bundle / 'reports').mkdir(parents=True)
    files = {'README.md': reviewed.README,
             'reports/2026-09-28.md': reviewed.render(completed(), {ID: TITLE})}
    for name, data in files.items():
        (bundle / name).write_bytes(data)
    manifest = {'schema': 1, 'target': reviewed.TARGET, 'branch': 'main',
                'base_commit': base_commit, 'base_files': base_files or {},
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
                'sources': {'2026-09-28': {'state_sha256': 'a' * 64, 'papers': [ID]}}}
    raw = json.dumps(manifest, sort_keys=True).encode()
    (bundle / 'manifest.json').write_bytes(raw)
    approval = root / 'approval.json'
    approval.write_text(json.dumps({'schema': 1, 'target': reviewed.TARGET, 'branch': 'main',
        'bundle_sha256': hashlib.sha256(raw).hexdigest(), 'decision': 'approve',
        'reviewer': 'independent-test-reviewer', 'reviewed_at': '2026-10-04T12:00:00+00:00',
        'checks': {'privacy': True, 'usefulness': True, 'provenance': True,
                   'unexecuted_proposals': True}}))
    return bundle, approval


if __name__ == '__main__':
    unittest.main()
