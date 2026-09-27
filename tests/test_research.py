import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import httpx
from harness import research
from harness.research_analysis import KEYS
from test_research_sources import atom, entry
from test_research_analysis import raw_analysis, proposal


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_five_paper_durable_run_exports_and_same_day_is_noop(self):
        requests = []
        async def handler(request):
            requests.append(str(request.url))
            if request.url.path == '/api/query':
                return httpx.Response(200, content=atom([entry(id='2609.%05dv1' % i) for i in range(10000, 10005)]))
            if request.url.path.startswith('/html/'):
                return httpx.Response(200, text='<article><p>' + 'We evaluate agent memory with ten tasks. ' * 60 + '</p></article>')
            data = json.loads(request.content)
            payload = json.loads(data['messages'][1]['content'])
            if 'source_blocks' in payload:
                value = raw_analysis()
            else:
                value = {'connections': [{'statement': 'Compare memory evaluation protocols.', 'papers': [p['id'] for p in payload['current']]}],
                         'next_experiment': proposal()}
            return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(value)}, 'eval_count': 100, 'prompt_eval_count': 200})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            config = research.Config(data_dir=str(root / 'state'), obsidian_dir=str(root / 'vault'), search_limit=5)
            (root / 'vault').mkdir()
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as web, httpx.AsyncClient(
                base_url='http://127.0.0.1:11434', transport=httpx.MockTransport(handler)) as model:
                result = await research.run(config, day='2026-09-27', web_client=web, model_client=model,
                                            resolver=lambda host: ['93.184.216.34'], interval=0)
                self.assertEqual(result['status'], 'complete')
                self.assertEqual(result['completed'], 5)
                self.assertEqual(result['shortfall'], 0)
                self.assertEqual(result['metrics']['model_calls'], 6)
                before = {str(p): p.read_bytes() for p in root.rglob('*.md')}
                request_count = len(requests)
                again = await research.run(config, day='2026-09-27', web_client=web, model_client=model,
                                           resolver=lambda host: ['93.184.216.34'], interval=0)
                self.assertEqual(len(requests), request_count)
                self.assertTrue(again['idempotent'])
                self.assertEqual(before, {str(p): p.read_bytes() for p in root.rglob('*.md')})
                report_path = Path(result['report'])
                original = report_path.read_bytes()
                report_path.write_text('tampered')
                with self.assertRaisesRegex(ValueError, 'integrity'):
                    await research.run(config, day='2026-09-27', web_client=web, model_client=model,
                                       resolver=lambda host: ['93.184.216.34'], interval=0)
                report_path.write_bytes(original)
            with sqlite3.connect(root / 'state' / 'research.sqlite3') as db:
                self.assertEqual(db.execute('select count(*) from catalog where read_version is not null').fetchone()[0], 5)
            report = Path(result['report']).read_text()
            self.assertIn('Independently verified', report)
            self.assertIn('None', report)
            self.assertTrue(Path(result['export']).is_file())
            self.assertEqual(len(list((root / 'state' / 'papers').glob('*/analysis.json'))), 5)


    async def test_resume_retries_only_failed_papers_and_next_day_deduplicates(self):
        from collections import Counter
        calls = Counter()
        fail = True
        async def handler(request):
            if request.url.path == '/api/query':
                calls['search'] += 1
                return httpx.Response(200, content=atom([entry()]))
            if request.url.path.startswith('/html/'):
                calls['html'] += 1
                return httpx.Response(200, text='<article><p>' + 'We evaluate agent memory with ten tasks. ' * 40 + '</p></article>')
            calls['model'] += 1
            if fail:
                return httpx.Response(503)
            payload = json.loads(json.loads(request.content)['messages'][1]['content'])
            if 'source_blocks' in payload:
                value = raw_analysis()
            else:
                value = {'connections': [{'statement': 'Test memory.', 'papers': ['2609.12345v1']}], 'next_experiment': proposal()}
            return httpx.Response(200, json={'done': True, 'message': {'content': json.dumps(value)}})
        with tempfile.TemporaryDirectory() as temp:
            config = research.Config(data_dir=str(Path(temp).resolve()), count=1, search_limit=5)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as web, httpx.AsyncClient(base_url=config.host, transport=httpx.MockTransport(handler)) as model:
                opts = dict(web_client=web, model_client=model, interval=0, resolver=lambda host: ['93.184.216.34'])
                first = await research.run(config, day='2026-09-27', **opts)
                self.assertEqual(first['status'], 'partial')
                self.assertEqual(first['shortfall'], 1)
                fail = False
                second = await research.run(config, day='2026-09-27', retry=True, **opts)
                self.assertEqual(second['status'], 'complete')
                self.assertEqual(calls['html'], 1)
                self.assertEqual(calls['search'], 1)
                third = await research.run(config, day='2026-09-28', **opts)
                self.assertEqual(third['completed'], 0)
                self.assertEqual(third['shortfall'], 1)
                self.assertEqual(calls['model'], 4)  # two failed calls, analysis, synthesis

    async def test_lock_and_artifact_integrity_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with research.locked(root):
                with self.assertRaisesRegex(RuntimeError, 'lock'):
                    with research.locked(root):
                        self.fail('Second lock acquired')
            target = root / 'report.md'
            target.write_text('changed')
            with self.assertRaisesRegex(ValueError, 'integrity'):
                research.verify_artifacts({'artifact_hashes': {str(target): '0' * 64}})
            redirected = root / 'redirected'
            with tempfile.TemporaryDirectory() as other:
                redirected.symlink_to(Path(other).resolve(), target_is_directory=True)
                with self.assertRaisesRegex(ValueError, 'symlink'):
                    research.atomic(redirected / 'leak.txt', 'no')
                self.assertFalse((Path(other) / 'leak.txt').exists())

    async def test_failed_resynthesis_cannot_complete_with_stale_partial_synthesis(self):
        from unittest.mock import patch
        from harness.research_analysis import ResearchModel
        from test_research_analysis import valid_analysis
        stage = 0
        analyzed = 0
        async def handler(request):
            if request.url.path == '/api/query':
                return httpx.Response(200, content=atom([entry(), entry(id='2609.12346v1')]))
            return httpx.Response(200, text='<article><p>' + 'We evaluate memory retrieval on ten tasks. Accuracy improves by 5 percent. ' * 25 + '</p></article>')
        async def analyze(model, paper, blocks, coverage):
            nonlocal analyzed
            analyzed += 1
            if stage == 0 and analyzed == 2:
                raise ValueError('Paper failure')
            return valid_analysis()
        async def synthesize(model, current, history):
            if stage:
                raise ValueError('Synthesis failure')
            return {'connections': [{'statement': 'Partial synthesis', 'papers': [current[0]['id']]}], 'next_experiment': 'One test'}
        with tempfile.TemporaryDirectory() as temp:
            config = research.Config(data_dir=str(Path(temp).resolve()), count=2, search_limit=5)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as web, httpx.AsyncClient(base_url=config.host) as local:
                with patch.object(ResearchModel, 'analyze', analyze), patch.object(ResearchModel, 'synthesize', synthesize):
                    first = await research.run(config, day='2026-09-27', web_client=web, model_client=local, interval=0, resolver=lambda h: ['93.184.216.34'])
                    self.assertEqual(first['completed'], 1)
                    stage = 1
                    second = await research.run(config, day='2026-09-27', retry=True, web_client=web, model_client=local, interval=0, resolver=lambda h: ['93.184.216.34'])
            self.assertEqual(second['completed'], 2)
            self.assertEqual(second['status'], 'partial')
            self.assertNotIn('synthesis', second)
            self.assertEqual(second['synthesis_error'], 'Synthesis failure')

    async def test_runtime_timeout_is_checkpointed_and_releases_lock(self):
        import asyncio
        async def stalled(request):
            await asyncio.sleep(5)
            return httpx.Response(200, content=atom([]))
        with tempfile.TemporaryDirectory() as temp:
            config = research.Config(data_dir=str(Path(temp).resolve()), runtime_seconds=1)
            async with httpx.AsyncClient(transport=httpx.MockTransport(stalled)) as web, httpx.AsyncClient(base_url=config.host) as model:
                state = await research.run(config, day='2026-09-27', web_client=web, model_client=model,
                                           interval=0, resolver=lambda host: ['93.184.216.34'])
            self.assertEqual(state['status'], 'partial')
            self.assertIn('runtime budget', state['error'])
            self.assertEqual(research.status(config)[0]['shortfall'], 5)
            with research.locked(config.data_dir):
                pass

    async def test_export_never_overwrites_human_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            config = research.Config(obsidian_dir=str(Path(temp).resolve()))
            state = {'day': '2026-09-27'}
            research.export_report(config, state, 'generated one')
            target = Path(state['export'])
            target.write_text('human annotation')
            with self.assertRaisesRegex(ValueError, 'preserving'):
                research.export_report(config, state, 'generated two')
            self.assertEqual(target.read_text(), 'human annotation')

    async def test_pdf_fallback_retains_original_and_page_locators(self):
        try:
            from pypdf import PdfWriter
            from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        except ImportError:
            self.skipTest('Install .[research] for PDF coverage')
        import io
        from harness import research
        from harness.research_sources import Arxiv
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 20 700 Td (' + b'Agent memory evaluation on ten tasks. ' * 40 + b') Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        buffer = io.BytesIO()
        writer.write(buffer)
        async def handler(request):
            return httpx.Response(404) if '/html/' in request.url.path else httpx.Response(200, content=buffer.getvalue())
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp).resolve()
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as web:
                fetch = Arxiv(web, interval=0, resolver=lambda host: ['93.184.216.34'])
                source = await research.acquire(research.Config(), fetch, directory, {'id': '2609.12345v1'})
            self.assertEqual(source['format'], 'pdf')
            self.assertTrue((directory / 'original.pdf').read_bytes().startswith(b'%PDF-'))
            blocks = json.loads((directory / 'extracted.json').read_text())
            self.assertEqual(blocks[0]['locator'], 'P0001B001')
            self.assertIn('Agent memory evaluation on ten tasks.', blocks[0]['text'])
            self.assertIn('404', source['html_fallback_reason'])


if __name__ == '__main__':
    unittest.main()
