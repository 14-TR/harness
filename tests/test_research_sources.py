"""Offline contract tests; fixture data is never live-research evidence."""
import unittest
from datetime import date

from harness import research_sources as sources


def atom(entries):
    return ('<feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:arxiv="http://arxiv.org/schemas/atom">' + ''.join(
        '<entry><id>http://arxiv.org/abs/{id}</id><title>{title}</title>'
        '<summary>{summary}</summary><published>{published}T00:00:00Z</published>'
        '<updated>{published}T00:00:00Z</updated><author><name>A</name></author>'
        '<category term="cs.AI"/></entry>'.format(**e) for e in entries) + '</feed>').encode()


def entry(id='2609.12345v1', published='2026-09-20', summary='LLM agent memory retrieval evaluation', title='Agent architectures'):
    return dict(id=id, published=published, summary=summary, title=title)


class MetadataTests(unittest.TestCase):
    def test_rejects_malformed_ids_and_non_atom_success_pages(self):
        for value in ['2609.12345', '2609.12345v0', '2609.12345v1/../../etc', 'https://arxiv.org/abs/2609.12345v1']:
            with self.assertRaises(ValueError):
                sources.paper_id(value)
        for data in [b'<html>bot wall</html>', b'<feed', b'<!DOCTYPE foo><feed/>']:
            with self.assertRaises(ValueError):
                sources.parse_atom(data)

    def test_discovery_pins_latest_unread_nonwithdrawn_relevant_versions(self):
        papers = sources.parse_atom(atom([
            entry(), entry(id='2609.12345v2'),
            entry(id='2609.10000v1', summary='This paper has been withdrawn'),
            entry(id='2609.11000v1', title='Fish', summary='Aquatic biology'),
            entry(id='2501.10000v1', published='2025-01-01'),
            entry(id='2609.12000v1'),
        ]))
        chosen = sources.select_papers(papers, {'2609.12000'}, date(2026, 9, 27), 5, 30)
        self.assertEqual([p['id'] for p in chosen], ['2609.12345v2', '2501.10000v1'])
        self.assertEqual([p['freshness'] for p in chosen], ['fresh', 'backlog'])
        self.assertEqual(chosen[0]['url'], 'https://arxiv.org/abs/2609.12345v2')
        self.assertIn('memory_context', chosen[0]['topics'])
        self.assertTrue(papers[2]['withdrawn'])


class AcquisitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_fetch_rejects_private_redirects_before_request(self):
        import httpx
        seen = []
        async def handler(request):
            seen.append(str(request.url))
            return httpx.Response(302, headers={'location': 'http://127.0.0.1/secret'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fetch = sources.Arxiv(client, interval=0, resolver=lambda host: ['93.184.216.34'])
            with self.assertRaises(ValueError):
                await fetch.get('https://arxiv.org/html/2609.12345v1')
        self.assertEqual(len(seen), 1)
        for url in ['https://arxiv.org@localhost/pdf/2609.12345v1',
                    'https://user:pass@arxiv.org/pdf/2609.12345v1',
                    'https://arxiv.org/html/2609.12345', 'https://evil.test/pdf/2609.12345v1']:
            with self.assertRaises(ValueError):
                sources.validate_url(url)

    async def test_download_cap_and_dns_private_rejection(self):
        import httpx
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b'x' * 101))) as client:
            fetch = sources.Arxiv(client, interval=0, max_bytes=100, resolver=lambda host: ['93.184.216.34'])
            with self.assertRaisesRegex(ValueError, 'budget'):
                await fetch.get('https://arxiv.org/html/2609.12345v1')
            fetch = sources.Arxiv(client, interval=0, resolver=lambda host: ['127.0.0.1'])
            with self.assertRaisesRegex(ValueError, 'public'):
                await fetch.get('https://arxiv.org/html/2609.12345v1')

    async def test_html_extraction_keeps_locators_and_labels_sampling(self):
        body = '<html><nav>IGNORE</nav><article><h1>Methods</h1>' + ''.join(
            '<p>Paragraph %s agent memory evaluation %s.</p>' % (n, 'words ' * 50) for n in range(30)) + '<script>RUN EVIL</script></article></html>'
        blocks = sources.extract_html(body)
        excerpt, coverage = sources.excerpts(blocks, 2000)
        self.assertTrue(coverage['truncated'])
        self.assertLessEqual(coverage['included_chars'], 2000)
        self.assertGreater(coverage['extracted_chars'], 9000)
        self.assertNotIn('RUN EVIL', str(blocks))
        self.assertNotIn('IGNORE', str(blocks))
        self.assertTrue(all(b['locator'].startswith('H') for b in excerpt))
        with self.assertRaises(ValueError):
            sources.extract_html('<html>not fulltext</html>')


if __name__ == '__main__':
    unittest.main()
