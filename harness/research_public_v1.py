"""Frozen schema-1 validation/rendering for byte-exact historical replay ONLY.

Derived from 1cd3ce37f8da098a46ffa00bc561cbff5d76a56c. Never used to
prepare new research. Keeping old bytes valid does not revise their provenance.
"""
from datetime import date
import asyncio
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from urllib.parse import urlencode

import httpx

from .research_analysis import KEYS
from .research_sources import Arxiv, paper_id, parse_atom

TARGET = 'https://github.com/14-TR/agentic-research.git'
OMITTED = 'Statement withheld by the public content policy.'
# Reviewed general research vocabulary, never automatically expanded from model
# output, private state or downloaded source text. Public title terms are separate.
# Unknown words suppress the whole field; never remove just a suspicious token.
WORDS = frozenset('''a an the and or but if then than that this these those with without
of for from to in on at by as is are was were be been being it its their they them
has have had do does did not no none only all any each both same different more less
most other some such may might can could should would will must also about across
between within under over through after before into per one two three four five ten
twenty first second next new small large larger smaller simple complex fixed bounded
local public toy task tasks run runs example examples condition conditions compare
compared comparing comparison comparator baseline baselines test tests testing trial
trials budget count stop minutes change changes proposed proposal experiment experiments
metric metrics measure measured measuring measurement evaluate evaluated evaluating
evaluation evaluations benchmark benchmarks accuracy success failure failures rate rates
latency cost costs time tokens token resource resources efficiency efficient overhead
memory retrieval context agent agents agentic system systems model models language
planning plan plans planner tool tools use uses using used call calls execution execute
executed action actions step steps sequence sequences sequential parallel multi single
multiagent orchestration workflow workflows architecture architectures design designs
approach approaches method methods mechanism mechanisms framework frameworks pipeline
pipelines process processes protocol protocols performance reliability reliable robust
robustness recovery retry retries recover permission permissions sandbox security safety
injection prompt prompts instruction instructions adversarial attack attacks defense
defenses policy policies constraint constraints validation validate validated validating
verification verified verify evidence reported reports report author authors claim claims
result results support supports supported improve improves improved improvement reduce
reduces reduced reduction increase increases increased effect effects impact tradeoff
tradeoffs trade off benefit benefits limit limits limitation limitations limited suggests
suggest suggestive demonstrates demonstrate distinguish attribution interpretation
interpreted analysis analyses analyze summary synthesis connection connections learning
training inference reasoning answer answers question questions query queries response
responses input inputs output outputs feedback loop loops search selection selected
select adaptive adapt dynamic static structured structure structures format schema
state states checkpoint checkpoints durable persistence persistent storage cache cached
caching history historical retrieval augmented generation generated generate document
documents information relevant relevance knowledge retrieve retrieved retrieving embedding
embeddings vector vectors semantic context window windows length long short sequence
attention compression compress compressed compact sampling sample sampled samples coverage
complete incomplete partial full incomplete missing available unavailable abstract
objective objectives reward rewards optimization optimize optimized optimize optimal
quality error errors correct incorrect correctness reproducible reproducibility replicate
replicated replication independent independently empirical estimate estimates estimated
statistical statistics significant significance random randomized deterministic controlled
control controls variables variable ablation ablations component components module modules
operation operations end endpoint observation observations observability tracing traces
debugging diagnosis diagnostic monitor monitoring tracking track trace distribution
distributions domain domains general generalization generalize generalizes generalizing
transfer scale scaling scalable scalability capacity complexity diverse diversity data
dataset datasets standard standards standardize standardized identical consistent
consistency inconsistent comparison fair fairness balance balanced balancing allocation
allocate allocations schedule scheduling scheduler routing route routes routed route
delegation delegate delegated collaborative collaboration cooperation cooperative team
teams role roles communication coordination coordinate centralized decentralized
hierarchical hierarchy hierarchy iterative iteration iterations reflection reflective
reflect self critic critique evaluate evaluator judge judges scoring score scores scored
precision recall ranking rank ranked correct completion completions completed throughput
duration seconds minute repeated repeat repetition variance confidence interval intervals
percent percentage proportion ratio ratios number numbers total average mean median
maximum minimum max min overall per separately respectively qualitative quantitative
rather instead while when where whether because so yet further additional additional
including include includes included exclude excludes excluded lack lacks lacking
establish established assumption assumptions uncertain uncertainty possible potentially
potential practical operational human automated automatic automation oversight intervention
interventions behavior behaviors behavioral observable identical standard instruction
following follow follows handle handles handling robustly recoverable failure latency
retention forgetting retained retain relevant irrelevant retrieve retrieval retrieval
finite infinite zero low high higher lower few many enough expected unexpected benign
untrusted trusted trust external internal boundary boundaries isolation isolated isolate
interrupt interrupted interruption cancel cancellation leakage privacy sensitive secure
isolate execute instruction source sources textual text excerpt excerpts section sections
figures equations tables appendices original novelty causal causal correlation causality
correlated correlates criterion criteria hypothesis hypotheses falsify falsifiable verify
prove proves proof limited empirical reported not independently verified qwen transformer
implement implemented implementing implementation implementations harness stateless
enable enabled enables enabling disable disabled disabling introduce introduces introduced
scenario scenarios corpus contexts orchestrator orchestrators utility utilities event events
highest lowest among fully locally live configuration configurations current strategy
strategies similarity existing showing show shows capability capabilities involving
setting settings effectiveness corresponding preloaded every propose proposing content
network networks significantly alongside estimation achieve achieves achieved computational
study studies directional return returns numerical gains operator operators integrated
usage backed check checks build builds building blocked blocking insecure release releases
direct directly guard guards allow allowed allowing decisions simplified version versions
parameter parameters rewrite rewriting transform transformation representation representations
access value values attribute attributes demonstrated denial favorable
best worst designed depends dependent dependency dependencies based replacing
replace replaced selective selectively selecting integrating integrate integrates
maintain maintains maintained maintaining handle handled tested
efficiently jointly threshold thresholds granular granularity partition partitioning partitioned
explicit explicitly implicit implicitly subjective stable stability unstable instability
preserve preserves preserving preservation heterogeneous homogeneous lightweight heavyweight
temporal longitudinal formal informal operationalize operationalized formalize formalized
constrained restrictive restriction restrict restricts restricting
interleave interleaved interleaving trajectory trajectories tolerant tolerance fallback
subtask subtasks specialized specialization specialist specialists generic specific specificity
addition additionally optimizer optimizers online offline
deployment deployed deploy deploying aligned align alignment externalize abstraction abstractions
separate separated separates separating actionable identifying identify identified identities
label labels labeled annotation annotations controller controllers reader readers replayable
declarative imperative whereas which whose itself themselves together similar similarly unlike
versus toward towards above below during until since either neither always never often
sometimes generally particularly up aware reminder cloud device footprint terms
conventional manual review scanners publicly fewer session sessions processing file
conditional cascade gate invoke invokes necessary combine combines cumulative weighted
technical generator indicators gain realized phase meta market trading
transformers llm llms ai rag react python json api cpu gpu software engineering computer
program programs code coding function functions function calling callable interface
interfaces contract contracts output tool action environmental environment environments
simulation simulated simulate simulator web browser browsing navigation navigation
multimodal modality modalities visual vision image images detection classification
classify classified reasoning chain thought thoughts branch branches branching graph
graphs node nodes edge edges directed cycle cycles cyclic acyclic record records
recorded replay replays replayed reproducible integrity audit auditing auditing
paper papers algorithms algorithm eight six eleven strong commercial comprises
interconnected phases broad competence development adapts evolve extent adaptation detailed
applications beyond discussed improving given consistently outperform outperforms
performs better non exposed vary varying interpreting real world
interacts player players game deliberate fast speech client machine tight
closely qualities valued actual play population rule out unmeasured
near oracle selects estimator estimators operating regime scales wall clock nearly
workers consider intra parallelism realization pre trained cognitive embed frequency
employs bifurcated captures symbolic microstructure substantial profitability coupling
cognition yields markets learn extends principles grounded top set assets represent
enhance drawdown adapted decision making leveraging applied enhancing perception
proposes oriented eviction driven page management retains peak tailored types
produces cryptographically signed attestation attestations permissioned smart secured
consortium operated certificate authority relative traditional scanning conducted
art infer inferred interactions evaluates deny requires induce valid combination satisfies
harder protecting completes failing leaving nothing mental health dialogue dialogues
professionalism clinical authenticity constructed open working alliance technique
encodes stage logic backbone fine tuned preference pairs goal bond derived anonymized
counseling verbatim transcripts approximate reproduce therapist patient interaction
sufficient modest modern curve likely corpora production harnesses judgment core host
adapters declarations ledger consumable grants supporting grounds exercised monitored
sanitized contain matching decomposition invariance arbitrary rewrites property
slm stt tts nmse db snr mimo cnn cbt dpo asr ua times f1
various deems evidenced conspiratorial discourse social media expressed lexical markers
simply appending tweet tweets part nor necessarily desirable providing excessive noise harm
we our post posts user inspect metadata circle utilize unique challenging covering
published year span late early achieving chinese financial'''.split())


from .research_public import PublicError


def checked_day(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except (ValueError, TypeError):
        raise PublicError('invalid_public_date') from None
    return value


def checked_id(value):
    try:
        if not isinstance(value, str) or not value.isascii() or len(value) > 40:
            raise ValueError
        paper_id(value)
    except (ValueError, TypeError):
        raise PublicError('invalid_public_id') from None
    return value


def checked_title(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 400
            or value != ' '.join(value.split())
            or any(not (c.isalnum() or c in " -:,.?!()'–—") for c in value)
            or re.search(r'\w\.\w|(?:gh[pousr]_|sk-|AKIA|password|credential|secret)', value, re.I)):
        raise PublicError('unsafe_public_title')
    return value


def prose(value, quotations=(), public_words=frozenset()):
    if value == OMITTED:
        return OMITTED
    if not isinstance(value, str) or not 1 <= len(value) <= 1000:
        return OMITTED
    # Only explicit research typography; never Unicode compatibility folding,
    # URL decoding, arbitrary character deletion or unknown-word substitution.
    value = value.translate(str.maketrans({'’': "'", '–': '-', '—': '-',
                                          '%': ' percent', '×': ' times'}))
    if (not re.fullmatch(r"[A-Za-z0-9 .,;:()'\-]+", value)
            or re.search(r'[A-Za-z0-9]\.[A-Za-z]|[A-Za-z]\.[0-9]|\d+(?:\.\d+){2}'
                         r'|\d{4}|(?:\d[ -]*){7}|\b(?:password|secret|credential|authorization|bearer|private|email|phone|address)\b'
                         r"|\b(?:api|access|refresh|auth|session)(?:'s)?[^A-Za-z0-9]*(?:keys?|tokens?)\b"
                         r"|\btokens?(?:'s)?\s*(?:-(?!budget\b)|[.:;,()']|is\b|equals\b|value\b)", value, re.I)):
        return OMITTED
    # Keep mixed lexemes intact. Only the literal research metric F1 is allowed;
    # title words cannot bless mixed-alphanumeric credential-shaped strings.
    lexemes = re.findall(r"[A-Za-z0-9]+(?:\.[0-9]+)?(?:'s)?", value.lower())
    for word in lexemes:
        word = word.removesuffix("'s")
        if word == 'f1':
            continue
        if re.fullmatch(r'[0-9]{1,3}(?:\.[0-9]{1,3})?', word):
            if float(word) <= 100:
                continue
        elif word.isalpha() and word in WORDS | public_words:
            continue
        return OMITTED
    if not lexemes:
        return OMITTED
    words = re.findall(r'[a-z0-9]+', value.lower())
    normalized = ' '.join(value.split())
    spans = {tuple(words[i:i+8]) for i in range(len(words)-7)}
    for quote in quotations:
        if not isinstance(quote, str):
            continue
        q = re.findall(r'[a-z0-9]+', quote.lower())
        # Do not emit a direct quote disguised as a model statement.
        if words == q or any(tuple(q[j:j+8]) in spans for j in range(len(q)-7)):
            return OMITTED
    return normalized


def eligible(state):
    return (isinstance(state, dict) and type(state.get('target')) is int
            and 1 <= state['target'] <= 10 and state.get('completed') == state['target']
            and state.get('shortfall') == 0 and isinstance(state.get('synthesis'), dict)
            and isinstance(state.get('items'), list) and len(state['items']) == state['target']
            and all(i.get('status') == 'complete' for i in state['items'])
            and (not state.get('error') or state.get('error', '').startswith('Export failed:')))


def build_document(state, verified_titles):
    if not eligible(state):
        raise PublicError('research_not_complete')
    day = checked_day(state['day'])
    papers = []
    public_words = frozenset(w.lower() for i in state['items']
        for w in re.findall('[A-Za-z]+', checked_title(verified_titles.get(i['paper']['id'], ''))))
    quotes = [v.get('quote', '') for item in state['items'] for key in KEYS
              for v in item.get('analysis', {}).get(key, []) if isinstance(v, dict)]
    for item in state['items']:
        identity = checked_id(item['paper']['id'])
        if identity not in verified_titles:
            raise PublicError('public_metadata_unverified')
        papers.append({'id': identity, 'title': checked_title(verified_titles[identity]),
                       'sections': {key: [prose(v.get('statement'), quotes, public_words)
                            for v in item.get('analysis', {}).get(key, [])[:3]] for key in KEYS}})
    allowed = {p['id'] for p in papers}
    if len(allowed) != len(papers):
        raise PublicError('duplicate_public_paper')
    connections = []
    for connection in state['synthesis'].get('connections', [])[:5]:
        refs = connection.get('papers', [])
        if isinstance(refs, list) and 1 <= len(refs) <= 5 and all(p in allowed for p in refs):
            connections.append({'statement': prose(connection.get('statement'), quotes, public_words), 'papers': refs})
    return {'schema': 1, 'day': day, 'papers': papers, 'connections': connections,
            'proposal': prose(state['synthesis'].get('next_experiment'), quotes, public_words)}


def validate_document(doc):
    if (not isinstance(doc, dict) or set(doc) != {'schema', 'day', 'papers', 'connections', 'proposal'}
            or type(doc['schema']) is not int or doc['schema'] != 1):
        raise PublicError('invalid_public_document')
    checked_day(doc['day'])
    if not isinstance(doc['papers'], list) or not 1 <= len(doc['papers']) <= 10:
        raise PublicError('invalid_public_document')
    ids = set()
    for paper in doc['papers']:
        if not isinstance(paper, dict) or set(paper) != {'id', 'title', 'sections'}:
            raise PublicError('invalid_public_document')
        checked_id(paper['id'])
        checked_title(paper['title'])
        if paper['id'] in ids or not isinstance(paper['sections'], dict) or set(paper['sections']) != set(KEYS):
            raise PublicError('invalid_public_document')
        ids.add(paper['id'])
    public_words = frozenset(w.lower() for p in doc['papers'] for w in re.findall('[A-Za-z]+', p['title']))
    for paper in doc['papers']:
        for values in paper['sections'].values():
            if not isinstance(values, list) or len(values) > 3 or any(prose(v, public_words=public_words) != v for v in values):
                raise PublicError('unsafe_public_prose')
    if not isinstance(doc['connections'], list) or len(doc['connections']) > 5 or prose(doc['proposal'], public_words=public_words) != doc['proposal']:
        raise PublicError('invalid_public_document')
    for item in doc['connections']:
        if (not isinstance(item, dict) or set(item) != {'statement', 'papers'} or prose(item['statement'], public_words=public_words) != item['statement']
                or not isinstance(item['papers'], list) or not 1 <= len(item['papers']) <= 5
                or any(not isinstance(p, str) or p not in ids for p in item['papers'])):
            raise PublicError('invalid_public_document')
    # Structural validity is not enough: never deliver a bibliography or an
    # all-withheld digest. This is a minimum coverage floor, not a quality proof.
    analytical = set(KEYS) - {'experiments'}
    coverage = []
    for paper in doc['papers']:
        useful = {key for key, values in paper['sections'].items()
                  if any(v != OMITTED and len(re.findall(r'[A-Za-z]+', v)) >= 5 for v in values)}
        if not useful:
            raise PublicError('insufficient_public_content')
        coverage.append(useful & analytical)
    proposal = doc['proposal']
    if (len(set().union(*coverage)) < 3
            or sum(len(keys) >= 2 for keys in coverage) * 2 < len(coverage)
            or proposal == OMITTED or len(proposal.split()) < 20
            or not all(label in proposal.lower() for label in ('comparator:', 'metric:', 'trial budget:'))
            or not re.search(r'\b(?:[1-9]|1[0-9]|20) (?:tasks|trials|runs|examples) per condition; stop after 20 minutes\.', proposal)):
        raise PublicError('insufficient_public_content')
    return doc


def render_document(doc):
    validate_document(doc)
    lines = ['# Agentic research — ' + doc['day'], '',
             'Model-generated interpretation and proposals; not independently verified.',
             'No experiments were executed. Read the original papers before acting.',
             'Titles and pinned identifiers were checked against arXiv metadata.',
             'Excerpts may omit figures, equations, tables and appendices. This is not a full-paper review.',
             'Whole statements may be withheld. Quotation fields and private artifacts are not exported; short source phrases may recur.', '']
    names = {'claims': 'Claims — model summary, not independent evidence', 'methods': 'Methods — model summary',
             'reported_evidence': 'Author-reported evidence — not replicated', 'limitations': 'Limitations — model interpretation',
             'experiments': 'Experiments — model proposals, not executed'}
    for p in doc['papers']:
        lines += ['## ' + p['title'], '', 'https://arxiv.org/abs/' + p['id'], '']
        for key in KEYS:
            lines += ['### ' + names[key], ''] + ['- ' + s for s in p['sections'][key]] + ['']
    lines += ['## Connections — model interpretation', '']
    for c in doc['connections']:
        lines += ['- ' + c['statement'], '  ' + ' '.join('https://arxiv.org/abs/' + p for p in c['papers'])]
    lines += ['', '## Next experiment — model proposal, not executed', '', doc['proposal'], '']
    return '\n'.join(lines).encode('utf-8')

