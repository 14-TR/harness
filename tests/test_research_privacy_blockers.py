"""Independent-review privacy regressions through a fetched local Git blob."""
import copy
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from harness import research_public as public
from harness.research_public_git import GitPublisher
from test_research_public import completed, DAY, ID, TITLE, PROPOSAL

PREFIX = 'The calibrated ablation improves task accuracy; '


class BlockerPrivacyTests(unittest.TestCase):
    def check_sink(self, value, markers=(), *, omitted=False, retained=False):
        state = completed()
        state['items'][0]['analysis']['claims'][0]['statement'] = value
        doc = public.build_document(state, {ID: TITLE})
        data = public.render_document(doc)
        public.validate_artifact(data, doc)
        injected = copy.deepcopy(doc)
        injected['papers'][0]['sections']['claims'] = [value]
        try:
            public.validate_document(injected)
            raw_accepted = True
        except public.PublicError:
            raw_accepted = False
        # Perform the complete sink path before assertions, including on RED.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            bare = root / 'remote.git'
            subprocess.run(['/usr/bin/git', 'init', '--bare', str(bare)], check=True, capture_output=True)
            with patch.object(GitPublisher, '_remote', return_value=str(bare)):
                publisher = GitPublisher(root / 'stage', public.TARGET)
                commit = publisher.publish(DAY, {DAY: doc})
            tip = subprocess.check_output(['/usr/bin/git', 'rev-parse', 'main'], cwd=bare).decode().strip()
            blob = subprocess.check_output(['/usr/bin/git', 'show', 'FETCH_HEAD:reports/' + DAY + '.md'], cwd=publisher.repo)
        self.assertEqual(commit, tip)
        self.assertEqual(blob, data)
        self.assertIn(PROPOSAL.encode(), blob)
        self.assertIn(b'The method uses memory retrieval', blob)
        self.assertNotIn(public.OMITTED.encode(), blob)
        claim = re.search(r'### Claims[^\n]*\n(.*?)(?=\n###|\n##|\Z)', blob.decode(), re.S)
        claim_text = claim.group(1) if claim else ''
        for marker in markers:
            self.assertNotIn(marker, claim_text, 'private component in fetched claim')
        if retained:
            self.assertTrue(raw_accepted)
            self.assertIn(value, claim_text)
        else:
            self.assertFalse(raw_accepted, 'direct final validator accepted unsafe claim')
        if omitted:
            self.assertEqual(doc['papers'][0]['sections']['claims'], [public.OMITTED])
            self.assertEqual(claim_text, '')

    def test_b1_credential_declarations_at_local_git_sink(self):
        cases = [
            ('password - memory', ('memory',)),
            ('password; meadow', ('meadow',)),
            ('passphrase "cobalt valley apple river"', ('cobalt', 'valley', 'apple', 'river')),
            ('API key "meadow"', ('meadow',)),
            ('API key meadow', ('meadow',)),
            ('password\u00a0-\nmemory', ('memory',)),
            ('password "juniper"', ('juniper',)),
            ("secret 'juniper meadow'", ('juniper', 'meadow')),
            ('credentials; juniper', ('juniper',)),
            ('passphrase - juniper', ('juniper',)),
            ('password juniper', ('juniper',)),
            ('access key juniper meadow', ('juniper', 'meadow')),
            ('session token\t"juniper"', ('juniper',)),
            ('API key “juniper meadow”', ('juniper', 'meadow')),
            ('secret — juniper', ('juniper',)),
            ('credential\u202f;\r\njuniper', ('juniper',)),
        ]
        for clause, markers in cases:
            with self.subTest(clause=clause):
                self.check_sink(PREFIX + clause, markers, omitted=True)

    def test_b2_contact_declarations_at_local_git_sink(self):
        for clause, markers in [
            ('Contact: Jane Smith for details.', ('Jane', 'Smith')),
            ('contact jane smith for details.', ('jane', 'smith')),
            ('CONTACT - marie delacroix for questions.', ('marie', 'delacroix')),
            ('contact\u00a0:\tjuniper meadow.', ('juniper', 'meadow')),
            ('contact = alex.', ('alex',)),
            ('contact “juniper meadow” for details.', ('juniper', 'meadow')),
        ]:
            with self.subTest(clause=clause):
                self.check_sink(PREFIX + clause, markers, omitted=True)

    def test_b3_dotted_telephones_at_local_git_sink(self):
        for number in ('+1.212.555.0198', '212.555.0198', '+44.20.7946.0958', '+1.415.555.0199'):
            with self.subTest(number=number):
                self.check_sink(PREFIX + 'call ' + number + ' for details.',
                                tuple(number.strip('+').split('.')))

    def test_b4_complete_spaced_path_components_at_local_git_sink(self):
        for span, markers in [
            ('"Quarterly Payroll.xlsx"', ('Quarterly', 'Payroll')),
            ('/Users/Alice/Quarterly Payroll Notes/report.md', ('Alice', 'Quarterly', 'Payroll', 'Notes')),
            ("'Annual Salary Register.csv'", ('Annual', 'Salary', 'Register')),
            ('“Quarterly Payroll.xlsx”', ('Quarterly', 'Payroll')),
            ('/Users/Alice/Quarterly Payroll Notes', ('Alice', 'Quarterly', 'Payroll', 'Notes')),
            ('../Juniper Payroll Notes/report.md', ('Juniper', 'Payroll', 'Notes')),
            (r'C:\Users\Alice\Quarterly Payroll Notes\report.md', ('Alice', 'Quarterly', 'Payroll', 'Notes')),
            ('"Juniper Payroll archive.tar.gz"', ('Juniper', 'Payroll', 'archive')),
        ]:
            with self.subTest(span=span):
                self.check_sink(PREFIX + 'artifact ' + span + ' is excluded.', markers)

    def test_b1_generic_token_values_are_sensitive_not_token_budgets(self):
        for clause in ('token "juniper"', 'token “juniper meadow”', 'token juniper', 'token juniper meadow'):
            with self.subTest(clause=clause):
                self.check_sink(PREFIX + clause, ('juniper', 'meadow'), omitted=True)
        for value in ('Token budgets can improve inference efficiency.',
                      'Compare token-budget efficiency with a fixed baseline.'):
            self.check_sink(value, retained=True)

    def test_b1_sentence_boundaries_and_multiword_bare_values(self):
        for clause in ('API key meadow', 'password juniper', 'token juniper meadow cobalt', 'API key sharing'):
            with self.subTest(clause=clause):
                self.check_sink('The calibrated ablation improves task accuracy. ' + clause,
                                ('juniper', 'meadow', 'cobalt', 'sharing'), omitted=True)

    def test_b2_ambiguous_bare_contacts_are_withheld(self):
        for clause in ('contact jane smith.', 'contact juniper meadow', 'contact alex.'):
            with self.subTest(clause=clause):
                self.check_sink(PREFIX + clause, ('jane', 'smith', 'juniper', 'meadow', 'alex'), omitted=True)

    def test_b4_labeled_unquoted_spaced_filenames_are_withheld(self):
        for span in ('Quarterly Payroll.xlsx', 'Annual Salary Register.csv'):
            with self.subTest(span=span):
                self.check_sink(PREFIX + 'artifact ' + span + ' is excluded.',
                                tuple(span.split()), omitted=True)

    def test_ordinary_research_still_reaches_local_git_verbatim(self):
        for value in (
            'Counterfactual metacognition mitigates epistemic miscalibration in heterogeneous swarms.',
            'The evaluation measures 1234 tokens with a fixed token-budget baseline.',
            'The framework validates CI/CD and scales nearly as 1/K with K workers.',
            'Llama-3.1-8B and GPT-4o improve F1 by 12.45 percent over 2048 trajectories.',
            'The proposed framework introduces a decentralized blockchain-backed agentic system.',
            'Secret sharing and private information retrieval address credential leakage.',
            'API keys can be rotated to reduce credential leakage.',
            'The API keys can be rotated to reduce credential leakage.',
            'Noether symmetry and Feynman path integrals inform the calibrated estimator.',
            'Contact dynamics improves calibrated robotic grasping in simulation.',
            'The model measures 0.0001 error at 95 percent confidence with version 3.12.1.',
        ):
            with self.subTest(value=value):
                self.check_sink(value, retained=True)
                self.assertEqual(public.prose(value, [value]), value)


if __name__ == '__main__':
    unittest.main()
