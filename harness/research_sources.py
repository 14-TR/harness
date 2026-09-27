"""Pinned arXiv discovery and bounded, read-only source acquisition.

Atom parsing adapted from Hermes Agent's MIT arxiv/search_arxiv.py helper.
Unlike that helper, citations retain versions and URLs are constructed locally.
"""
import asyncio
import ipaddress
import re
import socket
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin, urlencode

import httpx
from datetime import date

TOPICS = {
    'architecture_tools': ['architectur', 'orchestrat', 'tool use', 'tool-use', 'tool call', 'harness', 'workflow'],
    'memory_context': ['memory', 'retriev', 'context'],
    'planning_multiagent': ['planning', 'delegat', 'multi-agent', 'multiagent', 'multi agent'],
    'evaluation_observability': ['evaluat', 'benchmark', 'observab', 'debug', 'trac'],
    'reliability_security': ['reliab', 'recover', 'permission', 'sandbox', 'secur', 'robust', 'injection'],
    'efficiency_resources': ['latency', 'cost', 'resource', 'efficien', 'token budget'],
}
DEFAULT_QUERY = '(ti:agent OR ti:agentic) AND (cat:cs.AI OR cat:cs.CL OR cat:cs.SE OR cat:cs.CR)'
ID = re.compile(r'(?P<base>(?:\d{4}\.\d{4,5}|[a-z][a-z.-]+/\d{7}))v(?P<version>[1-9]\d*)\Z')
NS = {'a': 'http://www.w3.org/2005/Atom', 'x': 'http://arxiv.org/schemas/atom'}


def paper_id(value):
    match = ID.fullmatch(value)
    if not match:
        raise ValueError('A literal, version-pinned arXiv ID is required')
    return match['base'], int(match['version'])


def parse_atom(data):
    if b'<!DOCTYPE' in data or b'<!ENTITY' in data:
        raise ValueError('XML declarations are not allowed')
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError('Malformed arXiv Atom XML') from exc
    if root.tag != '{http://www.w3.org/2005/Atom}feed':
        raise ValueError('Response is not an arXiv Atom feed')
    papers = []
    for e in root.findall('a:entry', NS):
        def text(name):
            return ' '.join((e.findtext(name, '', NS) or '').split())
        raw = text('a:id')
        prefix = next((p for p in ('http://arxiv.org/abs/', 'https://arxiv.org/abs/') if raw.startswith(p)), None)
        if prefix is None:
            raise ValueError('arXiv returned an invalid entry or API error')
        value = raw[len(prefix):]
        base, version = paper_id(value)
        published, updated = text('a:published'), text('a:updated')
        date.fromisoformat(published[:10])
        date.fromisoformat(updated[:10])
        summary, comment = text('a:summary'), text('x:comment')
        title = text('a:title')
        if not title or not summary:
            raise ValueError('Missing arXiv title or abstract')
        papers.append(dict(id=value, base_id=base, version=version, title=title,
                           abstract=summary, comment=comment, published=published,
                           updated=updated, authors=[a.findtext('a:name', '', NS) for a in e.findall('a:author', NS)],
                           categories=[c.get('term') for c in e.findall('a:category', NS)],
                           withdrawn=bool(re.search(r'\b(withdrawn|retracted)\b', summary + ' ' + comment, re.I)),
                           url='https://arxiv.org/abs/' + value))
    return papers


def select_papers(papers, read_ids, today, count, fresh_days, topics=None):
    latest = {}
    for paper in papers:
        if paper['version'] > latest.get(paper['base_id'], {}).get('version', 0):
            latest[paper['base_id']] = paper
    selected = []
    for p in latest.values():
        if p['withdrawn'] or p['base_id'] in read_ids:
            continue
        text = (p['title'] + ' ' + p['abstract']).lower()
        if not re.search(r'\bagent(?:s|ic)?\b', text):
            continue
        matches = [k for k, terms in (topics or TOPICS).items() if any(t.lower() in text for t in terms)]
        if not matches:
            continue
        age = (today - date.fromisoformat(p['published'][:10])).days
        if age < 0:
            continue
        selected.append(dict(p, topics=matches, relevance_score=len(matches),
                             freshness='fresh' if age <= fresh_days else 'backlog'))
    # Prefer fresh; within each tier favor explicit engineering overlap then date.
    selected.sort(key=lambda p: (p['freshness'] == 'fresh', p['relevance_score'], p['published'], p['id']), reverse=True)
    return selected[:count]


def validate_url(url):
    u = urlsplit(url)
    if (u.scheme != 'https' or u.hostname not in {'arxiv.org', 'export.arxiv.org'}
            or u.username is not None or u.password is not None or u.port not in (None, 443)
            or u.fragment or any(ord(c) < 33 for c in url)):
        raise ValueError('Only credential-free HTTPS arXiv URLs are allowed')
    if u.path == '/api/query':
        return u
    if u.hostname != 'arxiv.org' or u.query:
        raise ValueError('Invalid paper URL')
    match = re.fullmatch(r'/(html|pdf)/(.+)', u.path)
    if not match:
        raise ValueError('Only pinned HTML/PDF endpoints are allowed')
    paper_id(match[2])
    return u


class Arxiv:
    """Caller supplies a proxy-free client. Never follow links inside papers."""
    def __init__(self, client, *, interval=3.1, max_bytes=20 * 1024 * 1024,
                 total_bytes=100 * 1024 * 1024, retries=2, resolver=None):
        self.client, self.interval, self.max_bytes = client, interval, max_bytes
        self.total_bytes, self.retries, self.resolver = total_bytes, retries, resolver
        self.last = 0.0
        self.metrics = {'requests': 0, 'download_bytes': 0, 'network_retries': 0}

    async def get(self, url):
        original = validate_url(url)
        for redirect in range(4):
            u = validate_url(url)
            # A redirect must not change a paper's version or source endpoint.
            if u.path != original.path or u.query != original.query:
                raise ValueError('Redirect changed the pinned source')
            if self.resolver:
                addresses = self.resolver(u.hostname)
            else:
                infos = await asyncio.get_running_loop().getaddrinfo(u.hostname, 443, type=socket.SOCK_STREAM)
                addresses = [i[4][0] for i in infos]
            if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
                raise ValueError('arXiv DNS must resolve to public addresses')
            for attempt in range(self.retries + 1):
                await asyncio.sleep(max(0, self.last + self.interval - time.monotonic()))
                self.last = time.monotonic()
                self.metrics['requests'] += 1
                try:
                    async with self.client.stream('GET', url, follow_redirects=False, timeout=45,
                                                  headers={'User-Agent': 'qwen-harness-research/0.1', 'Accept-Encoding': 'identity'}) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            if not response.headers.get('location'):
                                raise ValueError('Missing redirect location')
                            url = urljoin(url, response.headers['location'])
                            validate_url(url)
                            break
                        response.raise_for_status()
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            self.metrics['download_bytes'] += len(chunk)
                            if len(data) + len(chunk) > self.max_bytes or self.metrics['download_bytes'] > self.total_bytes:
                                raise ValueError('Download byte budget exceeded')
                            data.extend(chunk)
                        return bytes(data)
                except (httpx.TransportError, httpx.HTTPStatusError) as error:
                    retryable = not isinstance(error, httpx.HTTPStatusError) or error.response.status_code in (429, 500, 502, 503, 504)
                    if not retryable or attempt == self.retries:
                        raise
                    self.metrics['network_retries'] += 1
                    await asyncio.sleep(min(10, 2 ** attempt))
            else:
                raise RuntimeError('arXiv retry budget exhausted')
        raise ValueError('Too many redirects')


class ArticleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active = False
        self.hidden = 0
        self.parts, self.blocks = [], []

    def flush(self):
        text = ' '.join(''.join(self.parts).split())
        self.parts = []
        # Stable extraction-order locators; not invented native HTML anchors.
        for start in range(0, len(text), 1400):
            self.blocks.append({'locator': 'H%04d' % (len(self.blocks) + 1), 'text': text[start:start + 1400]})

    def handle_starttag(self, tag, attrs):
        if tag == 'article':
            self.active = True
        if tag in ('script', 'style', 'nav', 'svg'):
            self.hidden += 1
        if self.active and not self.hidden and tag in ('p', 'h1', 'h2', 'h3', 'h4', 'li', 'table', 'figcaption', 'section'):
            self.flush()

    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'nav', 'svg'):
            self.hidden = max(0, self.hidden - 1)
        if self.active and not self.hidden and tag in ('p', 'h1', 'h2', 'h3', 'h4', 'li', 'table', 'figcaption', 'section', 'article'):
            self.flush()
        if tag == 'article':
            self.active = False

    def handle_data(self, data):
        if self.active and not self.hidden:
            self.parts.append(data)


def extract_html(text):
    parser = ArticleText()
    parser.feed(text)
    parser.flush()
    if sum(len(b['text']) for b in parser.blocks) < 1000:
        raise ValueError('HTML is not usable article fulltext')
    return parser.blocks


def excerpts(blocks, max_chars):
    if not blocks:
        raise ValueError('No extracted text')
    total = sum(len(b['text']) for b in blocks)
    # Head plus systematic coverage throughout, including tail/appendices.
    order = list(range(min(4, len(blocks))))
    slots = max(1, max_chars // 1400)
    order += [round(i * (len(blocks) - 1) / slots) for i in range(slots + 1)]
    order += list(range(len(blocks)))
    chosen, seen, used = [], set(), 0
    for i in order:
        if i in seen:
            continue
        seen.add(i)
        remaining = max_chars - used
        if remaining <= 0:
            break
        b = dict(blocks[i], text=blocks[i]['text'][:remaining])
        chosen.append((i, b))
        used += len(b['text'])
    chosen.sort(key=lambda pair: pair[0])
    return [b for _, b in chosen], {'extracted_chars': total, 'included_chars': used,
                                   'extracted_blocks': len(blocks), 'included_blocks': len(chosen),
                                   'truncated': used < total,
                                   'caveat': 'Text-layer extraction; figures, equations and tables may be incomplete. Sampled context is not a full-paper review.'}
