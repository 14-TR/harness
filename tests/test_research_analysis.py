import copy
import json
import unittest

import httpx
from harness import research_analysis as analysis

BLOCKS = [{'locator': 'H0001', 'text': 'We evaluate memory retrieval on ten tasks. Accuracy improves by 5 percent.'}]


def valid_analysis():
    item = {'statement': 'The authors evaluate ten tasks.', 'locator': 'H0001', 'quote': BLOCKS[0]['text']}
    value = {key: [dict(item)] for key in analysis.KEYS}
    value['experiments'][0]['statement'] = ('Proposed change: Enable memory retrieval. Comparator: Same agent without retrieval. '
        'Metric: Task success rate. Trial budget: 10 tasks per condition; stop after 20 minutes.')
    return value


def proposal():
    return {'change': 'Enable memory retrieval', 'comparison': 'Same agent without retrieval',
            'metric': 'Task success rate', 'trial_count': 10, 'unit': 'tasks'}


def raw_analysis():
    value = {k: [{'statement': 'The authors evaluate ten tasks.', 'locator': 'H0001'}] for k in analysis.KEYS}
    value['experiments'] = [dict(proposal(), locator='H0001')]
    return value


class AnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_model_paths_reject_nonoperational_proposals(self):
        invalid = [
            'Train on a billion examples with no stop condition.',
            {'statement': 'Train on a billion examples.', 'locator': 'H0001'},
            {'statement': 'Train on a billion examples.', 'locator': 'H0001', 'quote': BLOCKS[0]['text']},
            {k: v for k, v in proposal().items() if k != 'metric'},
            dict(proposal(), extra='unapproved'),
        ] + [dict(proposal(), trial_count=n) for n in (True, False, 1.5, '10', 0, 21, 1000000000)] + [
            dict(proposal(), unit='epochs'), dict(proposal(), unit=[]), dict(proposal(), change='x')]
        for path in ('analysis', 'synthesis'):
            for bad in invalid:
                with self.subTest(path=path, bad=bad):
                    value = raw_analysis() if path == 'analysis' else {
                        'connections': [{'statement': 'Compare methods.', 'papers': ['2609.12345v1']}],
                        'next_experiment': bad}
                    if path == 'analysis':
                        value['experiments'] = [dict(bad, locator='H0001') if isinstance(bad, dict) else bad]
                    async def handler(request):
                        return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(value)}})
                    async with httpx.AsyncClient(base_url='http://127.0.0.1:11434', transport=httpx.MockTransport(handler)) as client:
                        model = analysis.ResearchModel(client, 'qwen2.5:7b')
                        with self.assertRaises(ValueError):
                            if path == 'analysis':
                                await model.analyze({}, BLOCKS, {})
                            else:
                                await model.synthesize([{'id': '2609.12345v1', 'title': 'Test'}], [])

    async def test_local_model_has_no_tools_and_checks_quotes_before_accepting(self):
        bodies = []
        async def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json={'message': {'content': json.dumps(raw_analysis())}, 'done': True,
                                            'done_reason': 'stop', 'prompt_eval_count': 100, 'eval_count': 90})
        async with httpx.AsyncClient(base_url='http://127.0.0.1:11434', transport=httpx.MockTransport(handler)) as client:
            model = analysis.ResearchModel(client, 'qwen2.5:7b', max_calls=1)
            result = await model.analyze({'id': '2609.12345v1', 'title': 'Test'}, BLOCKS, {})
            self.assertEqual(result, valid_analysis())
            self.assertEqual(model.metrics['output_tokens'], 90)
            self.assertEqual(bodies[0]['tools'], [])
            with self.assertRaisesRegex(RuntimeError, 'budget'):
                await model.analyze({}, BLOCKS, {})
        bad = copy.deepcopy(result)
        bad['claims'][0]['quote'] = 'invented result'
        with self.assertRaisesRegex(ValueError, 'quote'):
            analysis.validate_analysis(bad, BLOCKS)
        bad = copy.deepcopy(result)
        bad['methods'][0]['locator'] = 'H9999'
        with self.assertRaisesRegex(ValueError, 'locator'):
            analysis.validate_analysis(bad, BLOCKS)
        with self.assertRaises(ValueError):
            analysis.validate_analysis(dict(result, verified=True), BLOCKS)

    async def test_model_selects_a_source_locator_and_controller_quotes_it(self):
        sent = []
        async def handler(request):
            sent.append(json.loads(request.content))
            value = {k: [{'statement': 'Authors report ten tasks.', 'locator': 'H0001'}] for k in analysis.KEYS}
            value['experiments'] = [dict(proposal(), locator='H0001')]
            return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(value)}})
        async with httpx.AsyncClient(base_url='http://127.0.0.1:11434', transport=httpx.MockTransport(handler)) as client:
            model = analysis.ResearchModel(client, 'qwen2.5:7b')
            result = await model.analyze({}, BLOCKS, {})
        self.assertEqual(result['claims'][0]['quote'], BLOCKS[0]['text'])
        item = sent[0]['format']['properties']['claims']['items']
        self.assertEqual(item['properties']['locator']['enum'], ['H0001'])
        self.assertNotIn('quote', item['properties'])

    async def test_metadata_abstract_is_not_uncitable_model_context(self):
        sent = []
        async def handler(request):
            sent.append(json.loads(json.loads(request.content)['messages'][1]['content']))
            return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(raw_analysis())}})
        async with httpx.AsyncClient(base_url='http://127.0.0.1:11434', transport=httpx.MockTransport(handler)) as client:
            await analysis.ResearchModel(client, 'qwen2.5:7b').analyze({'id': '2609.12345v1', 'title': 'Test', 'abstract': 'Uncitable claim'}, BLOCKS, {})
        self.assertNotIn('abstract', sent[0]['paper'])

    async def test_experiment_requires_operational_proposal_not_a_paper_summary(self):
        sent = []
        async def handler(request):
            sent.append(json.loads(request.content))
            value = {k: [{'statement': 'Authors report ten tasks.', 'locator': 'H0001'}] for k in analysis.KEYS}
            value['experiments'] = [{'change': 'Enable memory retrieval', 'comparison': 'Same agent without retrieval',
                                     'metric': 'Task success and token count', 'trial_count': 10, 'unit': 'tasks', 'locator': 'H0001'}]
            return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(value)}})
        blocks = [BLOCKS[0], {'locator': 'H0002', 'text': '2 Methods'}]
        async with httpx.AsyncClient(base_url='http://127.0.0.1:11434', transport=httpx.MockTransport(handler)) as client:
            model = analysis.ResearchModel(client, 'qwen2.5:7b')
            result = await model.analyze({}, blocks, {})
        self.assertIn('Comparator: Same agent without retrieval', result['experiments'][0]['statement'])
        self.assertIn('Trial budget: 10 tasks', result['experiments'][0]['statement'])
        schema = sent[0]['format']['properties']
        self.assertEqual(set(schema['experiments']['items']['required']), {'change', 'comparison', 'metric', 'trial_count', 'unit', 'locator'})
        self.assertNotIn('H0002', schema['claims']['items']['properties']['locator']['enum'])

    async def test_failed_output_receipts_survive_validation_errors(self):
        saved = []
        async def handler(request):
            wrong = raw_analysis()
            wrong['claims'][0]['quote'] = 'This is not in the source'
            return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(wrong)}, 'eval_count': 50})
        async with httpx.AsyncClient(base_url='http://127.0.0.1:11434', transport=httpx.MockTransport(handler)) as client:
            model = analysis.ResearchModel(client, 'qwen2.5:7b', receipt_sink=lambda n, value: saved.append(json.loads(json.dumps(value))))
            with self.assertRaisesRegex(ValueError, 'quote'):
                await model.analyze({}, BLOCKS, {})
        self.assertEqual(len(saved), 2)
        self.assertIn('quotes are controller-owned', saved[-1]['error'])
        self.assertEqual(saved[-1]['response']['eval_count'], 50)
        self.assertIn('request_sha256', saved[-1])

    async def test_synthesis_history_is_explicitly_bounded(self):
        seen = []
        async def handler(request):
            data = json.loads(request.content)
            seen.append(data['messages'][1]['content'])
            return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps({
                'connections': [{'statement': 'Compare methods.', 'papers': ['2609.12345v1']}], 'next_experiment': proposal()})}})
        record = {'id': '2609.12345v1', 'title': 'Test', 'analysis': {k: [{'statement': 'x' * 1000, 'quote': 'y' * 1000, 'locator': 'H0001'}] * 3 for k in analysis.KEYS}}
        async with httpx.AsyncClient(base_url='http://127.0.0.1:11434', transport=httpx.MockTransport(handler)) as client:
            model = analysis.ResearchModel(client, 'qwen2.5:7b')
            await model.synthesize([record] * 5, [record] * 20)
        self.assertLessEqual(len(seen[0]), 28000)

    async def test_synthesis_cannot_cite_unseen_history(self):
        value = {'connections': [{'statement': 'Compare recovery methods.', 'papers': ['2609.12345v1', '2401.99999v1']}],
                 'next_experiment': proposal()}
        with self.assertRaisesRegex(ValueError, 'citation'):
            analysis.validate_synthesis(value, {'2609.12345v1'})
        value['connections'][0]['papers'] = ['2609.12345v1']
        result = analysis.validate_synthesis(value, {'2609.12345v1'})
        self.assertEqual(result['connections'], value['connections'])
        self.assertEqual(result['next_experiment'], valid_analysis()['experiments'][0]['statement'])


if __name__ == '__main__':
    unittest.main()
