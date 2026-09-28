"""Public boundary tests; all Git pushes are to isolated local bare fixtures."""
import copy
import importlib
import importlib.util
import hashlib
import asyncio
import httpx
import unittest
import os
from pathlib import Path
import subprocess
import tempfile
import time
import sys
from unittest.mock import patch
from test_research_sources import atom, entry


DAY = '2026-09-28'
ID = '2609.12345v1'
TITLE = 'Agent memory evaluation'
PROPOSAL = ('Proposed change: Enable memory retrieval. Comparator: Same agent without retrieval. '
            'Metric: Task success rate. Trial budget: 10 tasks per condition; stop after 20 minutes.')


def completed():
    return {'day': DAY, 'completed': 1, 'target': 1, 'shortfall': 0,
            'status': 'complete', 'synthesis': {'connections': [
                {'statement': 'Compare memory retrieval with a fixed context budget.', 'papers': [ID]}],
                'next_experiment': PROPOSAL},
            'items': [{'status': 'complete', 'paper': {'id': ID, 'title': 'PRIVATE TITLE', 'url': 'file:///private/report.md'},
                       'analysis': {'claims': [{'statement': 'Memory retrieval improves task accuracy.', 'quote': 'RAW SOURCE QUOTE'}],
                                    'methods': [{'statement': 'The method uses memory retrieval with a fixed baseline.'}],
                                    'reported_evidence': [{'statement': 'The authors report improved task accuracy on ten tasks.'}],
                                    'limitations': [{'statement': 'The evaluation does not establish reliability across all conditions.'}],
                                    'experiments': [{'statement': 'Compare memory retrieval with a fixed context budget.', 'quote': 'RAW SOURCE QUOTE'}]}}],
            'report': '/private/daily.md', 'error': '', 'metrics': {'provider_log': 'PRIVATE PROVIDER LOG'}}


def public_module(test):
    test.assertIsNotNone(importlib.util.find_spec('harness.research_public'), 'Public delivery module is missing')
    return importlib.import_module('harness.research_public')


class ExportTests(unittest.TestCase):
    def test_representative_research_prose_and_bounded_proposals_survive(self):
        public = public_module(self)
        state = completed()
        sections = {
            'claims': ['The paper benchmarks eight channel estimation algorithms across various scenarios.'],
            'methods': ['The orchestrator selects the best estimator based on validation performance for each operating condition.'],
            'reported_evidence': ['The orchestrator improves NMSE over the best fixed strategy by up to 3.6 dB in the high-SNR MIMO regime.'],
            'limitations': ['The study does not consider intra-agent parallelism to reduce single-realization latency.'],
            'experiments': ['Proposed change: Implement a conditional cascade that invokes the CNN only when a confidence gate deems it necessary. Comparator: Compare the performance of the proposed conditional cascade with the current orchestrator. Metric: NMSE and computational efficiency. Trial budget: 10 tasks per condition; stop after 20 minutes.']}
        state['items'][0]['analysis'] = {key: [{'statement': value, 'quote': 'Unrelated source.'} for value in values]
                                       for key, values in sections.items()}
        proposals = [
            'Propose a small-scale experiment to integrate the Qwen-Planner-Agent\'s AI for Training phase with the META framework\'s memory module. Implement a toy task involving a simple financial trading scenario, such as setting up a trading strategy based on historical market data. Comparator: Compare the performance of the integrated system with the current META framework. Metric: Directional accuracy and computational efficiency. Trial budget: 10 tasks per condition; stop after 20 minutes.',
            'Proposed change: Implement a small local Qwen agent harness using toy tasks or locally available public examples.. Comparator: Compare the performance of the Qwen agent with a text-only LLM and a context-aware LLM.. Metric: F1 score. Trial budget: 10 tasks per condition; stop after 20 minutes.']
        for proposal in proposals:
            state['synthesis']['next_experiment'] = proposal
            doc = public.build_document(state, {ID: 'Qwen Planner Agent META channel estimation'})
            self.assertEqual(doc['papers'][0]['sections'], sections)
            self.assertEqual(doc['proposal'], proposal)
            public.validate_artifact(public.render_document(doc), doc)
        for original, normalized in [
                ('The agent’s performance may vary across real–world tasks.', "The agent's performance may vary across real-world tasks."),
                ('The model retains 98.53% accuracy with 3.97× throughput.', 'The model retains 98.53 percent accuracy with 3.97 times throughput.')]:
            self.assertEqual(public.prose(original), normalized)
        # Newly supported typography/numbers must not authorize disguised sensitive data.
        for value in ('session–token – AI12AI34', 'API’s token: memory', 'memory.md',
                      '98.53.12.34', 'F1AI12', 'memory\u200bretrieval', '/tmp/memory',
                      'memory’s secret: public', 'provider log: memory'):
            self.assertEqual(public.prose(value), public.OMITTED)

    def test_useful_operational_proposals_survive_without_allowing_unknown_names(self):
        public = public_module(self)
        state = completed()
        proposal = ('Proposed change: Implement bounded memory retrieval in the local harness. '
                    'Comparator: A stateless agent. Metric: Task success rate. '
                    'Trial budget: 10 tasks per condition; stop after 20 minutes.')
        state['synthesis']['next_experiment'] = proposal
        state['items'][0]['analysis']['claims'][0]['statement'] = 'ActKV improves memory efficiency.'
        doc = public.build_document(state, {ID: 'ActKV: Efficient Agent Memory'})
        self.assertEqual(doc['proposal'], proposal)
        self.assertEqual(doc['papers'][0]['sections']['claims'], ['ActKV improves memory efficiency.'])
        public.validate_artifact(public.render_document(doc), doc)
        state['items'][0]['analysis']['claims'][0]['statement'] = 'PrivateProjectName improves memory efficiency.'
        self.assertEqual(public.build_document(state, {ID: TITLE})['papers'][0]['sections']['claims'], [public.OMITTED])

    def test_new_allowlisted_report_uses_verified_title_and_interpretation(self):
        public = public_module(self)
        document = public.build_document(completed(), {ID: TITLE})
        text = public.render_document(document).decode()
        self.assertIn(TITLE, text)
        self.assertIn('https://arxiv.org/abs/' + ID, text)
        self.assertIn('Memory retrieval improves task accuracy.', text)
        self.assertIn('Compare memory retrieval with a fixed context budget.', text)
        self.assertIn('not independently verified', text)
        for secret in ('PRIVATE TITLE', 'RAW SOURCE QUOTE', '/private', 'daily.md', 'provider_log'):
            self.assertNotIn(secret, text)
        self.assertEqual(set(document), {'schema', 'day', 'papers', 'connections', 'proposal'})
        self.assertEqual(public.validate_artifact(public.render_document(document), document), document)

    def test_untrusted_fields_fail_closed_including_encoded_and_personal_data(self):
        public = public_module(self)
        cases = ['secret = harmless', 'API_KEY=sk-test-not-a-real-key', 'ghp_' + 'x'*35,
                 'AKIA' + 'X'*16, '/Users/example/private/report.md', 'C:\\Users\\example\\report.txt',
                 'file:///etc/passwd', '%252FUsers%252Fexample', '&#47;etc&#47;passwd',
                 r'\u002fUsers\u002fexample', 'L1VzZXJzL2V4YW1wbGU=',
                 '[memory](https://example.org)', '<a href="https://example.org">memory</a>',
                 'www.example.org', 'person@example.org', '+1 212 555 1234',
                 'John Smith compared memory.', 'password\u00a0:\npublic', 'daily.md',
                 'memory\u200bretrieval', 'PRIVATE KEY', 'correct horse battery staple',
                 'API token is memory', 'token: memory', 'call 1 2 3 4 5 6 7 8 9']
        for value in cases:
            with self.subTest(value=value):
                state = completed()
                state['items'][0]['analysis']['claims'][0]['statement'] = value
                state['synthesis']['next_experiment'] = value
                doc = public.build_document(state, {ID: TITLE})
                self.assertEqual(doc['papers'][0]['sections']['claims'], [public.OMITTED])
                self.assertEqual(doc['proposal'], public.OMITTED)
                with self.assertRaisesRegex(public.PublicError, '^insufficient_public_content$'):
                    public.render_document(doc)
        state = completed()
        statement = state['items'][0]['analysis']['claims'][0]['statement']
        state['items'][0]['analysis']['claims'][0]['quote'] = statement
        self.assertEqual(public.build_document(state, {ID: TITLE})['papers'][0]['sections']['claims'], [public.OMITTED])

    def test_credential_boundaries_and_mixed_lexemes_are_withheld_at_git_sink(self):
        public = public_module(self)
        from harness.research_public_git import GitPublisher
        variants = ['session-token - AI12AI34AI56AI78', 'token - memory',
                    'session.token - memory', 'session(token): memory',
                    'access,token; memory', 'auth:key - memory',
                    'refresh_token=memory', 'API\tkey: memory',
                    'session\u00a0token\n- memory', 'sessiontoken - memory',
                    'token equals memory', 'token value memory', 'AI12AI34AI56AI78',
                    "token 'memory'", "token's value memory", 'access-tokens - memory',
                    'session - tokens - memory', 'token-memory', 'token . memory']
        for value in variants:
            with self.subTest(value=value):
                state = completed()
                state['items'][0]['analysis']['claims'][0]['statement'] = value
                doc = public.build_document(state, {ID: TITLE})
                self.assertEqual(doc['papers'][0]['sections']['claims'], [public.OMITTED])
                proposal_state = completed()
                proposal_state['synthesis']['next_experiment'] = value
                self.assertEqual(public.build_document(proposal_state, {ID: TITLE})['proposal'], public.OMITTED)
                injected = public.build_document(completed(), {ID: TITLE})
                injected['proposal'] = value
                with self.assertRaises(public.PublicError):
                    public.validate_artifact(b'', injected)
                # Keep other safe research so withholding is observed in real Git.
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp).resolve()
                    bare = root / 'remote.git'
                    subprocess.run(['/usr/bin/git', 'init', '--bare', str(bare)], check=True, capture_output=True)
                    with patch.object(GitPublisher, '_remote', return_value=str(bare)):
                        publisher = GitPublisher(root / 'stage', public.TARGET)
                        commit = publisher.publish(DAY, {DAY: doc})
                        with self.assertRaises(public.PublicError):
                            publisher.publish(DAY, {DAY: injected})
                    self.assertEqual(subprocess.check_output(['/usr/bin/git', 'rev-parse', 'main'], cwd=bare).decode().strip(), commit)
                    blob = subprocess.check_output(['/usr/bin/git', 'show', 'main:reports/' + DAY + '.md'], cwd=bare)
                    self.assertNotIn(value.encode(), blob)
                    self.assertIn(b'Compare memory retrieval', blob)
        self.assertEqual(public.prose('Compare token budget with a fixed baseline.'),
                         'Compare token budget with a fixed baseline.')
        self.assertEqual(public.prose('Compare token-budget efficiency with a fixed baseline.'),
                         'Compare token-budget efficiency with a fixed baseline.')

    def test_final_artifact_and_structure_reject_injected_fields(self):
        public = public_module(self)
        document = public.build_document(completed(), {ID: TITLE})
        with self.assertRaises(public.PublicError):
            public.validate_artifact(public.render_document(document) + b'/private/extra', document)
        for change in ({'provider_log': 'x'}, {'day': '../bad'}, {'proposal': '/etc/passwd'}):
            with self.subTest(change=change), self.assertRaises(public.PublicError):
                public.render_document(dict(document, **change))


class MetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_is_fetched_from_fixed_source_before_export(self):
        public = public_module(self)
        self.assertTrue(hasattr(public, 'verify_metadata'), 'Independent public metadata verification missing')
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, content=atom([entry(id=ID, title=TITLE)]))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await public.verify_metadata([ID], client=client, resolver=lambda _: ['93.184.216.34'])
            self.assertEqual(result, {ID: TITLE})
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0].url.host, 'export.arxiv.org')
            self.assertEqual(requests[0].url.params['id_list'], ID)
            for bad in ('2609.12345', '../2609.12345v1', '2609.12345v1?key=secret', '2609.12345v01', '２６０９.１２３４５v1'):
                with self.subTest(bad=bad), self.assertRaises(public.PublicError):
                    await public.verify_metadata([bad], client=client, resolver=lambda _: ['93.184.216.34'])
            self.assertEqual(len(requests), 1)

    async def test_wrong_or_unsafe_metadata_and_transport_errors_are_safe(self):
        public = public_module(self)
        self.assertTrue(hasattr(public, 'verify_metadata'))
        for content in (atom([entry(id='2609.11111v1')]), atom([entry(id=ID, title='/private/title.md')]), b'bad'):
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content))) as client:
                with self.assertRaises(public.PublicError) as failure:
                    await public.verify_metadata([ID], client=client, resolver=lambda _: ['93.184.216.34'])
                self.assertEqual(str(failure.exception), 'public_metadata_unavailable')


class GitTests(unittest.TestCase):
    def test_meaningless_documents_never_reach_local_git(self):
        public = public_module(self)
        from harness.research_public_git import GitPublisher
        for kind in ('all-withheld', 'bibliography', 'proposal-only', 'no-proposal', 'tiny'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                bare = root / 'remote.git'
                self.git(root, 'init', '--bare', str(bare))
                doc = public.build_document(completed(), {ID: TITLE})
                if kind == 'no-proposal':
                    doc['proposal'] = public.OMITTED
                else:
                    for paper in doc['papers']:
                        paper['sections'] = {k: ([] if kind == 'bibliography' else
                            ['Memory.' if kind == 'tiny' else public.OMITTED]) for k in public.KEYS}
                    if kind != 'proposal-only':
                        doc['proposal'] = public.OMITTED
                    doc['connections'] = []
                with self.assertRaisesRegex(public.PublicError, '^insufficient_public_content$'):
                    public.validate_document(doc)
                with patch.object(GitPublisher, '_remote', return_value=str(bare)):
                    with self.assertRaisesRegex(public.PublicError, '^insufficient_public_content$'):
                        GitPublisher(root / 'stage', public.TARGET).publish(DAY, {DAY: doc})
                self.assertEqual(self.git(bare, 'for-each-ref', '--format=%(refname)'), '')
                self.assertFalse((root / 'stage').exists())

    def test_git_auth_uses_repo_scoped_keychain_and_ignores_environment_config(self):
        public = public_module(self)
        from harness.research_public_git import GitPublisher
        with tempfile.TemporaryDirectory() as tmp:
            publisher = GitPublisher(Path(tmp).resolve() / 'stage', public.TARGET)
            publisher.repo.mkdir(parents=True)
            publisher.deadline = time.monotonic() + 10
            popen = subprocess.Popen
            with patch.dict(os.environ, {'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'http.proxy', 'GIT_CONFIG_VALUE_0': 'untrusted'}), patch(
                    'harness.research_public_git.subprocess.Popen', wraps=popen) as spawn:
                publisher._git('--version')
            argv = spawn.call_args.args[0]
            self.assertIn('credential.useHttpPath=true', argv)
            self.assertIn('credential.helper=osxkeychain', argv)
            self.assertNotIn('GIT_CONFIG_COUNT', spawn.call_args.kwargs['env'])
            self.assertEqual(spawn.call_args.kwargs['env']['GIT_CONFIG_GLOBAL'], os.devnull)

    def test_process_crash_after_commit_or_push_recovers_without_duplicate_commit(self):
        public = public_module(self)
        from harness.research_public_git import GitPublisher
        doc = public.build_document(completed(), {ID: TITLE})
        for boundary in ('commit', 'push'):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                bare = root / 'remote.git'
                self.git(root, 'init', '--bare', str(bare))
                script = '''import json,os,sys
from harness.research_public_git import GitPublisher
from harness.research_public import TARGET
p = GitPublisher(sys.argv[1], TARGET)
p._remote = lambda: sys.argv[2]
original = p._git
def crash(*args, **kwargs):
    result = original(*args, **kwargs)
    if args[0] == sys.argv[3]:
        os._exit(91)
    return result
p._git = crash
document = json.loads(sys.stdin.read())
p.publish(document['day'], {document['day']: document})
'''
                child = subprocess.run([sys.executable, '-c', script, str(root / 'stage'), str(bare), boundary],
                                       input=__import__('json').dumps(doc), capture_output=True, text=True, timeout=15)
                self.assertEqual(child.returncode, 91, child.stderr)
                with patch.object(GitPublisher, '_remote', return_value=str(bare)):
                    commit = GitPublisher(root / 'stage', public.TARGET).publish(DAY, {DAY: doc})
                self.assertEqual(commit, self.git(bare, 'rev-parse', 'refs/heads/main'))
                self.assertEqual(self.git(bare, 'rev-list', '--count', 'main'), '1')

    def test_timeout_kills_git_transport_descendants_and_suppresses_output(self):
        public = public_module(self)
        from harness.research_public_git import GitPublisher
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            marker = root / 'escaped'
            child = 'import time,pathlib; time.sleep(1); pathlib.Path(' + repr(str(marker)) + ').write_text("escaped")'
            script = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' + repr(child) + ']); time.sleep(10)'
            publisher = GitPublisher(root / 'staging', public.TARGET)
            publisher.repo.mkdir(parents=True)
            publisher.deadline = time.monotonic() + 0.5
            popen = subprocess.Popen
            calls = []
            def substitute(argv, **kwargs):
                calls.append((argv, kwargs))
                return popen([sys.executable, '-c', script], **kwargs)
            with patch('harness.research_public_git.subprocess.Popen', side_effect=substitute):
                with self.assertRaisesRegex(public.PublicError, '^public_git_timeout$'):
                    publisher._git('ls-remote', public.TARGET, 'refs/heads/main')
            time.sleep(1.1)
            self.assertFalse(marker.exists(), 'Timed-out Git transport escaped the process group')
            self.assertTrue(calls[0][1].get('start_new_session'))
            self.assertFalse(calls[0][1].get('shell', False))
            self.assertNotIn('GIT_CONFIG_COUNT', calls[0][1]['env'])

    def git(self, cwd, *args):
        result = subprocess.run(['/usr/bin/git', *args], cwd=cwd, capture_output=True, check=True, timeout=15)
        return result.stdout.decode().strip()

    def test_real_local_bare_publication_is_scoped_readback_verified_and_idempotent(self):
        public = public_module(self)
        self.assertIsNotNone(importlib.util.find_spec('harness.research_public_git'), 'Safe Git publisher missing')
        from harness.research_public_git import GitPublisher
        doc = public.build_document(completed(), {ID: TITLE})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            bare = root / 'remote.git'
            self.git(root, 'init', '--bare', str(bare))
            publisher = GitPublisher(root / 'staging', public.TARGET)
            with patch.object(publisher, '_remote', return_value=str(bare)):
                first = publisher.publish(DAY, {DAY: doc})
                again = publisher.publish(DAY, {DAY: doc})
            self.assertEqual(first, again)
            self.assertEqual(first, self.git(bare, 'rev-parse', 'refs/heads/main'))
            self.assertEqual(self.git(bare, 'rev-list', '--count', 'main'), '1')
            self.assertEqual(set(self.git(bare, 'ls-tree', '-r', '--name-only', 'main').splitlines()),
                             {'README.md', 'reports/' + DAY + '.md'})
            actual = self.git(bare, 'show', 'main:reports/' + DAY + '.md')
            self.assertEqual(actual, public.render_document(doc).decode().strip())
            self.assertEqual(self.git(bare, 'show', '-s', '--format=%an|%ae|%cn|%ce', 'main'),
                             'TR Ingram|14-TR@users.noreply.github.com|TR Ingram|14-TR@users.noreply.github.com')
            self.assertEqual(self.git(root / 'staging/repo', 'remote'), '')

    def test_staging_rejects_symlinks_unexpected_files_and_injected_index(self):
        public = public_module(self)
        from harness.research_public_git import GitPublisher
        for attack in ('file', 'directory', 'symlink', 'git-symlink', 'staged', 'config', 'modified'):
            with self.subTest(attack=attack), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp).resolve()
                bare = root / 'remote.git'
                self.git(root, 'init', '--bare', str(bare))
                doc = public.build_document(completed(), {ID: TITLE})
                publisher = GitPublisher(root / 'staging', public.TARGET)
                with patch.object(publisher, '_remote', return_value=str(bare)):
                    prior = publisher.publish(DAY, {DAY: doc})
                    repo = root / 'staging/repo'
                    if attack in ('file', 'staged'):
                        (repo / 'secret.txt').write_text('PRIVATE TEST FIXTURE')
                        if attack == 'staged':
                            self.git(repo, 'add', '--', 'secret.txt')
                            (repo / 'secret.txt').unlink()  # Only the index still has it.
                    elif attack == 'directory':
                        (repo / 'reports/unexpected').mkdir()
                    elif attack == 'symlink':
                        (repo / 'reports' / (DAY + '.md')).unlink()
                        (repo / 'reports' / (DAY + '.md')).symlink_to(root / 'remote.git/config')
                    elif attack == 'git-symlink':
                        (repo / '.git/objects/info/alternates').symlink_to(root / 'remote.git/objects')
                    elif attack == 'config':
                        with (repo / '.git/config').open('a') as stream:
                            stream.write('\n[url "https://private.invalid"]\n insteadOf = https://github.com\n')
                    elif attack == 'modified':
                        (repo / 'reports' / (DAY + '.md')).write_text('/Users/private/credentials')
                    with self.assertRaises(public.PublicError):
                        publisher.publish(DAY, {DAY: doc})
                self.assertEqual(self.git(bare, 'rev-parse', 'refs/heads/main'), prior)

    def test_retry_after_lost_push_readback_reuses_exact_commit(self):
        public = public_module(self)
        from harness.research_public_git import GitPublisher
        doc = public.build_document(completed(), {ID: TITLE})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            bare = root / 'remote.git'
            self.git(root, 'init', '--bare', str(bare))
            publisher = GitPublisher(root / 'staging', public.TARGET)
            original = publisher._remote_head
            calls = 0
            def lost_readback():
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise public.PublicError('public_git_timeout')
                return original()
            with patch.object(publisher, '_remote', return_value=str(bare)):
                with patch.object(publisher, '_remote_head', side_effect=lost_readback):
                    with self.assertRaises(public.PublicError):
                        publisher.publish(DAY, {DAY: doc})
                pushed = self.git(bare, 'rev-parse', 'refs/heads/main')
                self.assertEqual(publisher.publish(DAY, {DAY: doc}), pushed)
            self.assertEqual(self.git(bare, 'rev-list', '--count', 'main'), '1')

    def test_second_day_and_new_clean_staging_recover_known_public_history(self):
        public = public_module(self)
        from harness.research_public_git import GitPublisher
        first = public.build_document(completed(), {ID: TITLE})
        second = dict(first, day='2026-09-29')
        docs = {DAY: first, '2026-09-29': second}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            bare = root / 'remote.git'
            self.git(root, 'init', '--bare', str(bare))
            with patch.object(GitPublisher, '_remote', return_value=str(bare)):
                GitPublisher(root / 'stage-one', public.TARGET).publish(DAY, {DAY: first})
                GitPublisher(root / 'stage-two', public.TARGET).publish('2026-09-29', docs)
            self.assertEqual(self.git(bare, 'rev-list', '--count', 'main'), '2')


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_insufficient_content_stays_pending_unfrozen_without_git(self):
        public = public_module(self)
        from harness import research
        from test_research_repair import Fixture
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = Fixture(root)
            state = await fixture.run()
            for item in state['items']:
                for values in item['analysis'].values():
                    for value in values:
                        value['statement'] = 'UnknownPrivateTerm'
            state['synthesis']['next_experiment'] = 'UnknownPrivateTerm'
            fixture.config.public_repository = public.TARGET
            fixture.config.public_staging_dir = str(root / 'public')
            store = research.Store(root / 'state')
            try:
                store.save(state)
                with patch.object(public, 'verify_metadata', return_value={ID: TITLE}):
                    await public.sync(store, fixture.config)
            finally:
                store.db.close()
            store = research.Store(root / 'state')
            try:
                self.assertEqual(store.db.execute('select status,phase,error,payload from public_delivery').fetchone(),
                                 ('pending', 'prepare', 'insufficient_public_content', None))
                self.assertFalse((root / 'public').exists())
            finally:
                store.db.close()

    async def test_opt_in_validation_does_not_require_access_to_vault(self):
        public = public_module(self)
        from harness import research
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            vault = root / 'inaccessible-vault'
            config = research.Config(data_dir=str(root / 'state'), obsidian_dir=str(vault),
                                     public_repository=public.TARGET, public_staging_dir=str(root / 'public'))
            original = Path.resolve
            def inaccessible(path, *args, **kwargs):
                if path == vault:
                    raise PermissionError('vault unavailable')
                return original(path, *args, **kwargs)
            with patch.object(Path, 'resolve', inaccessible):
                try:
                    config.validate()
                except PermissionError:
                    self.fail('Public opt-in validation unexpectedly accesses the unavailable vault')

    async def test_exact_opt_in_target_and_separate_stage_are_required(self):
        public = public_module(self)
        from harness import research
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            config = research.Config(data_dir=str(root / 'state'))
            config.validate()
            self.assertFalse(await public.sync(None, config))
            for target in ('https://github.com/14-TR/harness.git', 'https://user:password@github.com/14-TR/agentic-research.git',
                           public.TARGET + '/', public.TARGET + '?token=sample', public.TARGET.upper(),
                           'git@github.com:14-TR/agentic-research.git', str(root / 'remote.git'), True):
                config.public_repository, config.public_staging_dir = target, str(root / 'public')
                with self.subTest(target=target), self.assertRaises(public.PublicError):
                    config.validate()
            config.public_repository = public.TARGET
            for stage in (root, root / 'state/inside', Path.cwd() / 'public', Path('relative')):
                config.public_staging_dir = str(stage)
                with self.subTest(stage=stage), self.assertRaises(public.PublicError):
                    config.validate()
            (root / 'link').symlink_to(root / 'other')
            config.public_staging_dir = str(root / 'link')
            with self.assertRaises(public.PublicError):
                config.validate()
            config.public_staging_dir = str(root / 'public')
            config.validate()

    async def test_public_outbox_drain_is_bounded_and_frozen_payload_tamper_blocks(self):
        public = public_module(self)
        from harness import research
        from harness.research_public_git import GitPublisher
        from test_research_repair import Fixture
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = Fixture(root)
            original = await fixture.run()
            fixture.config.public_repository = public.TARGET
            fixture.config.public_staging_dir = str(root / 'public')
            store = research.Store(root / 'state')
            try:
                for n in range(1, 9):
                    state = copy.deepcopy(original)
                    state['day'] = '2026-09-%02d' % n
                    store.save(state)
                with patch.object(public, 'verify_metadata', return_value={ID: TITLE}), patch.object(
                        GitPublisher, 'publish', side_effect=OSError('private error')) as push:
                    await public.sync(store, fixture.config)
                    self.assertEqual(push.call_count, 7)
                saved = public.delivery_status(fixture.config)
                self.assertEqual(sum(d['attempts'] for d in saved), 7)
                with store.db:
                    store.db.execute("update public_delivery set payload=payload || ' ' where payload is not null")
                with patch.object(GitPublisher, 'publish', side_effect=AssertionError('Tampered outbox must not publish')) as push, patch.object(
                        public, 'verify_metadata', return_value={ID: TITLE}):
                    await public.sync(store, fixture.config)
                    self.assertEqual(push.call_count, 0)
            finally:
                store.db.close()

    async def test_private_prepare_has_no_git_or_state_writes(self):
        public = public_module(self)
        from test_research_repair import Fixture
        self.assertTrue(hasattr(public, 'prepare'), 'Nonpublishing historical prepare command missing')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = Fixture(root)
            await fixture.run()
            before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / 'state').rglob('*') if p.is_file()}
            async with httpx.AsyncClient(transport=httpx.MockTransport(fixture.handler)) as client:
                with patch('harness.research_public_git.GitPublisher.publish', side_effect=AssertionError('Must not upload')):
                    result = await public.prepare(fixture.config, root / 'preview', client=client,
                                                  resolver=lambda _: ['93.184.216.34'])
            self.assertEqual(result['prepared'], ['2026-09-27'])
            self.assertFalse(result['uploaded'])
            after = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (root / 'state').rglob('*') if p.is_file()}
            self.assertEqual([p.name for p in before.keys() | after.keys() if before.get(p) != after.get(p)], [])
            self.assertFalse(list((root / 'preview').rglob('.git')))

    async def test_public_retry_is_durable_and_independent_of_failed_obsidian(self):
        public = public_module(self)
        from harness import research
        from harness.research_public_git import GitPublisher
        from test_research_repair import Fixture
        self.assertIn('public_repository', research.Config.__dataclass_fields__, 'Explicit opt-in missing')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = Fixture(root, vault=True)  # No vault: Obsidian fails.
            fixture.config.public_repository = public.TARGET
            fixture.config.public_staging_dir = str(root / 'public-stage')
            bare = root / 'remote.git'
            subprocess.run(['/usr/bin/git', 'init', '--bare', str(bare)], check=True, capture_output=True)
            with patch.object(GitPublisher, '_remote', return_value=str(bare)), patch.object(
                    GitPublisher, 'publish', side_effect=OSError('/private/secret credential detail')):
                first = await fixture.run()
            self.assertEqual(first['delivery']['status'], 'pending')
            queued = public.delivery_status(fixture.config)
            self.assertEqual(queued[0]['status'], 'pending')
            self.assertEqual(queued[0]['error'], 'public_delivery_failed')
            requests = dict(fixture.calls)
            with patch.object(GitPublisher, '_remote', return_value=str(bare)), patch.object(
                    public, 'verify_metadata', side_effect=AssertionError('Frozen retry must not refetch')):
                again = await fixture.run()
            self.assertEqual(dict(fixture.calls), requests)
            self.assertEqual(again['delivery']['status'], 'pending')
            self.assertEqual(public.delivery_status(fixture.config)[0]['status'], 'delivered')
            self.assertEqual(first['attempts'], again['attempts'])

    async def test_completed_run_publishes_before_early_obsidian_permission_error(self):
        public = public_module(self)
        from harness import research
        from harness.research_public_git import GitPublisher
        from test_research_repair import Fixture
        self.assertIn('public_repository', research.Config.__dataclass_fields__)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            fixture = Fixture(root, vault=True)
            (root / 'vault').mkdir()
            first = await fixture.run()
            fixture.config.public_repository = public.TARGET
            fixture.config.public_staging_dir = str(root / 'public-stage')
            bare = root / 'remote.git'
            subprocess.run(['/usr/bin/git', 'init', '--bare', str(bare)], check=True, capture_output=True)
            original = research.verify_artifacts
            def inaccessible_export(state):
                if state.get('export'):
                    raise PermissionError('private vault denied')
                return original(state)
            calls = dict(fixture.calls)
            with patch.object(GitPublisher, '_remote', return_value=str(bare)), patch.object(
                    research, 'verify_artifacts', side_effect=inaccessible_export):
                with self.assertRaises(PermissionError):
                    await fixture.run()
            self.assertEqual(public.delivery_status(fixture.config)[0]['status'], 'delivered')
            self.assertEqual(fixture.calls['analysis'], calls['analysis'])
            self.assertEqual(fixture.calls['synthesis'], calls['synthesis'])
