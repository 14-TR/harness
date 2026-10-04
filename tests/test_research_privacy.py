"""Privacy policy regressions: ordinary research is not a word allowlist."""
import unittest
from harness import research_public as public
from test_research_public import completed, ID, TITLE


class PrivacyTests(unittest.TestCase):
    def test_novel_scientific_vocabulary_and_numbers_are_retained(self):
        values = [
            'Counterfactual metacognition mitigates epistemic miscalibration in heterogeneous swarms.',
            'Llama-3.1-8B and GPT-4o improve F1 by 12.45 percent over 2048 trajectories.',
            'The 2026 evaluation measures 1234 tokens and 0.0001 error at 95 percent confidence.',
            'Secret sharing and private information retrieval address credential leakage.',
            'Zygomorphic preconditioning stabilizes the variational eigensolver.',
        ]
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(public.prose(value), value)
        state = completed()
        state['items'][0]['analysis']['claims'][0]['statement'] = values[0]
        doc = public.build_document(state, {ID: TITLE})
        self.assertIn(values[0], public.render_document(doc).decode())

    def test_public_source_phrases_and_research_ratios_are_not_private(self):
        statement = 'The proposed framework introduces a decentralized blockchain-backed agentic system.'
        self.assertEqual(public.prose(statement, [statement]), statement)
        for value in ('The framework validates CI/CD configuration with independent attestations.',
                      'The partitioning scales wall-clock time nearly as 1/K with K workers.',
                      'The API keys can be rotated to reduce credential leakage.'):
            self.assertEqual(public.prose(value), value)

    def test_public_titles_and_pinned_links_are_not_secret_keywords(self):
        title = 'Private Information Retrieval and Secret Sharing with Llama-3.1'
        state = completed()
        statement = 'The public result at https://arxiv.org/abs/2609.12345v1 improves calibrated recall.'
        state['items'][0]['analysis']['claims'][0]['statement'] = statement
        doc = public.build_document(state, {ID: title})
        self.assertEqual(doc['papers'][0]['title'], title)
        self.assertEqual(doc['papers'][0]['sections']['claims'], [statement])
        self.assertIn(title, public.render_document(doc).decode())
        punctuated = 'The calibrated ablation is reported at https://arxiv.org/abs/2609.12345v1.'
        self.assertEqual(public.prose(punctuated), punctuated)
        malformed = 'The calibrated result uses https://arxiv.org/abs/２６０９.１２３４５v1 as support.'
        self.assertNotEqual(public.prose(malformed), malformed)
        for suffix in ('?token=abc', '/private', '#private', '%3Ftoken%3Dabc'):
            self.assertNotIn('https://arxiv.org/abs/2609.12345v1' + suffix,
                             public.prose(statement.replace('2609.12345v1', '2609.12345v1' + suffix)))

    def test_omissions_are_summarized_once_and_schema_one_is_frozen(self):
        from harness import research_public_v1 as legacy
        state = completed()
        old = legacy.build_document(state, {ID: TITLE})
        self.assertEqual(public.render_document(old), legacy.render_document(old))
        for key in ('claims', 'experiments'):
            state['items'][0]['analysis'][key][0]['statement'] = 'password: do not export'
        state['synthesis']['connections'][0]['statement'] = 'username: do not export'
        doc = public.build_document(state, {ID: TITLE})
        self.assertEqual(doc['schema'], 2)
        text = public.render_document(doc).decode()
        self.assertNotIn(public.OMITTED, text)
        self.assertEqual(text.count('3 statements omitted'), 1)
        self.assertNotIn('### Claims', text)
        self.assertNotIn('## Connections', text)
        self.assertIn('The method uses memory retrieval', text)

    def test_sensitive_context_and_obfuscation_fail_closed(self):
        cases = [
            'username is alice', 'Account number - 123456', 'Contact John Smith for details.',
            'I live at 123 Main Street.', 'Bearer memory', 'filename: private-notebook',
            'file name is private-notebook', 'My email is alice at example dot org',
            'ＡＰＩ＿ＫＥＹ＝testing', 'file%253A%252F%252Fetc%252Fpasswd',
            'https&#58;&#47;&#47;host.invalid', r'\u0068ttps\u003a\u002f\u002fexample.org',
            'AI12-AI34-AI56', 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDfake',
        ]
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(public.prose(value), public.OMITTED)
                doc = public.build_document(completed(), {ID: TITLE})
                doc['papers'][0]['sections']['claims'] = [value]
                with self.assertRaises(public.PublicError):
                    public.validate_document(doc)
        for span in ('"/Users/John Smith/private report.md"',
                     '"C:\\Users\\John Smith\\private report.txt"', '/Users/John Smith/notes.md'):
            text = public.prose('The calibrated ablation improves task accuracy using ' + span + ' for storage.')
            self.assertNotIn('John', text)
            self.assertNotIn('Smith', text)
            self.assertNotIn('report', text)

    def test_prior_credential_boundary_matrix_remains_closed(self):
        import itertools
        separators = [' ', '-', '--', ' . ', '.', ':', ',', ';', '(', ")'", '\t', '\n',
                      '\u00a0', '\u202f', '\u200b', '\r\n', '’s ']
        cases = [a + s + b + ' - memory' for a, s, b in itertools.product(
            ['api', 'access', 'refresh', 'auth', 'session'], separators, ['key', 'keys', 'token', 'tokens'])]
        cases += ['session-token - AI12AI34AI56AI78', 'AI12-AI34-AI56', 'F1F1', 'F1AI12',
                  'memory12retrieval34', "token’s: memory", 'token . memory']
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(public.prose(value), public.OMITTED)
                doc = public.build_document(completed(), {ID: TITLE})
                doc['papers'][0]['sections']['claims'] = [value]
                with self.assertRaises(public.PublicError):
                    public.validate_document(doc)
        # Single metric/model suffixes are not a scientific-vocabulary ban.
        self.assertEqual(public.prose('F1 score improves for model7 and memory1.'),
                         'F1 score improves for model7 and memory1.')

    def test_identifiable_private_spans_are_masked_without_losing_findings(self):
        spans = [
            '/Users/example/notes.md', '/home/alice/results.csv', '/tmp/cache',
            r'C:\Users\Alice\report.txt', r'\\server\share\notes', r'..\private\log',
            '~/Desktop/report', '~alice/notes', '${HOME}/notes', '$HOME/notes', 'AWS/KEYS',
            './results.json', '../secret/config', 'notes/daily.md', '.env', 'local.sqlite3',
            'experiment-results.csv', 'file:///Users/example/daily.md',
            'https://private.example.org/?token=sensitive', 'www.example.org',
            'alice@example.org', 'alice@localhost', 'alice:synthetic@localhost',
            'id_rsa', 'Makefile', 'Dockerfile', '@private_handle', '+1 (212) 555-1234',
            '98.53.12.34', 'ghp_' + 'x'*35, 'AI12AI34AI56AI78',
        ]
        prefix = 'The ablation improves calibrated recall over the baseline; artifact '
        for span in spans:
            with self.subTest(span=span):
                result = public.prose(prefix + span + ' is excluded.')
                # Cycle 2 deliberately strengthens span masking to whole-field
                # withholding. Keep every historical case and final-validator
                # assertion; useful unaffected sections are tested at Git sink.
                self.assertEqual(result, public.OMITTED)
                self.assertNotIn(span, result)
                self.assertEqual(public.prose(result), result)
                state = completed()
                state['items'][0]['analysis']['claims'][0]['statement'] = prefix + span + ' is excluded.'
                doc = public.build_document(state, {ID: TITLE})
                self.assertNotIn(span, public.render_document(doc).decode())
                doc['papers'][0]['sections']['claims'] = [prefix + span + ' is excluded.']
                with self.assertRaises(public.PublicError):
                    public.validate_document(doc)


if __name__ == '__main__':
    unittest.main()
