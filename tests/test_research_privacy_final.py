"""Boundary families, not guessed sensitive value endpoints."""
import copy
import itertools
import unittest

from harness import research_public as public
from test_research_public import completed, ID, TITLE
import test_research_privacy_blockers as blockers

# Keep lexical values synthetic and unrelated to the recognizer.
BOUNDARIES = ('', 'The calibrated ablation improves recall. ',
              'The calibrated ablation improves recall; ',
              'The calibrated ablation improves recall — ',
              'The calibrated ablation improves recall。\u2007')
SPACES = (' ', '\t', '\n', '\u00a0', '\u202f', '\u2007')


class WholeFieldPrivacyTests(unittest.TestCase):
    def assert_withheld(self, value):
        self.assertEqual(public.prose(value), public.OMITTED, value)
        state = completed()
        state['items'][0]['analysis']['claims'][0]['statement'] = value
        doc = public.build_document(state, {ID: TITLE})
        self.assertEqual(doc['papers'][0]['sections']['claims'], [public.OMITTED])
        raw = copy.deepcopy(doc)
        raw['papers'][0]['sections']['claims'] = [value]
        with self.assertRaises(public.PublicError):
            public.validate_document(raw)

    def test_credential_field_cross_product(self):
        for boundary, article, space, label, join in itertools.product(
                BOUNDARIES, ('', 'the ', 'a ', 'our '), SPACES,
                ('API key', 'password', 'passphrase', 'secret', 'credential', 'token'),
                (' ', '=', ': ', ' — ', '；', ' “')):
            value = boundary + article + label.replace(' ', space) + join + 'junipermeadow'
            with self.subTest(value=value):
                self.assert_withheld(value)
        self.assert_withheld('The calibrated ablation improves recall with the token=junipermeadow.')

    def test_contact_field_cross_product(self):
        for boundary, space, name, tail in itertools.product(
                BOUNDARIES, SPACES, ('jane smith', 'jane q. smith', "jean o'brien", 'josé núñez'),
                ('.', ', please.', ' for details.', '')):
            self.assert_withheld(boundary + 'contact' + space + name + tail)

    def test_telephone_field_cross_product(self):
        for boundary, label, space, number in itertools.product(
                BOUNDARIES, ('call', 'phone', 'telephone', 'mobile'), SPACES,
                ('020.7946.0958', '+44.7911.123456', '020 7946 0958', '(020) 7946-0958')):
            self.assert_withheld(boundary + label + space + number + ' for details.')

    def test_filename_field_cross_product(self):
        for boundary, label, space, name in itertools.product(
                BOUNDARIES, ('file', 'filename', 'file name', 'artifact', 'path'), SPACES,
                ('Quarterly Payroll.xlsx', '"Quarterly O\'Brien Payroll.xlsx"',
                 '“Quarterly O’Brien Payroll.xlsx”', '/Users/Juniper/Payroll Q4 Notes/report.csv')):
            self.assert_withheld(boundary + label + space + name + ' is excluded.')
        for value in ('The comparison uses "Quarterly O\'Brien Payroll.xlsx" for storage.',
                      'The comparison uses /Users/Juniper/Payroll Q4 Notes/report.csv for storage.'):
            self.assert_withheld(value)

    def test_scientific_controls_are_not_vocabulary_filtered(self):
        for value in (
            'Contact dynamics improves calibrated robotic grasping in simulation.',
            'The contact dynamics model improves calibrated robotic grasping.',
            'The API keys can be rotated to reduce credential leakage.',
            'Secret sharing and private information retrieval address credential leakage.',
            'Token budgets can improve inference efficiency.',
            'The evaluation measures 1234 tokens with a fixed token-budget baseline.',
            'Feynman path integrals and Noether symmetry constrain the estimator.',
            'File systems improve reproducibility in computational science.',
            'The model measures 0.0001 error with version 3.12.1 and Llama-3.1-8B.',
            'Counterfactual metacognition mitigates epistemic miscalibration in heterogeneous swarms.',
        ):
            with self.subTest(value=value):
                self.assertEqual(public.prose(value), value)

    def test_unicode_letters_are_not_label_separators(self):
        value = 'Apiλkey improves calibration of stochastic estimators.'
        self.assertEqual(public.prose(value), value)

    def test_additional_scientific_subjects_are_not_censored(self):
        # These independently chosen ordinary subjects expose the residual
        # ambiguity in bare-label detection; do not weaken these expectations.
        for value in (
            'Contact mechanics explains friction in deformable solids.',
            'The API key rotation policy reduces credential leakage.',
            'Password hashing improves resistance to offline guessing.',
        ):
            with self.subTest(value=value):
                self.assertEqual(public.prose(value), value)

    def test_inline_ambiguous_contact_is_withheld(self):
        self.assert_withheld('The calibrated ablation improves recall; please contact jane q. smith.')

    def test_fresh_regressions_at_local_git_sink(self):
        sink = blockers.BlockerPrivacyTests()
        values = (
            'The calibrated ablation improves task accuracy; the token=junipermeadow was used.',
            'The calibrated ablation improves task accuracy; the API key junipermeadow',
            'The calibrated ablation improves task accuracy. contact jane smith.',
            'The calibrated ablation improves task accuracy.\tcontact jane smith.',
            'The calibrated ablation improves task accuracy; contact jane smith, please.',
            'The calibrated ablation improves task accuracy; contact jane q. smith for details.',
            'The calibrated ablation improves task accuracy; call 020.7946.0958 for details.',
            'The calibrated ablation improves task accuracy; call +44.7911.123456 for details.',
            'The calibrated ablation improves task accuracy; file Quarterly Payroll.xlsx is excluded.',
            'The calibrated ablation improves task accuracy; artifact "Quarterly O\'Brien Payroll.xlsx" is excluded.',
        )
        for value in values:
            with self.subTest(value=value):
                sink.check_sink(value, ('junipermeadow', 'jane', 'smith', 'Quarterly', 'Payroll'), omitted=True)


if __name__ == '__main__':
    unittest.main()
