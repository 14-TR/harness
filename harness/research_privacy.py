"""Privacy heuristics for public-paper-only prose, not a secrecy proof.

There is no general scientific vocabulary allowlist. We reject ambiguous declarations
and encoded/control payloads; recognized private context withholds the field. Inputs
must remain public papers and their no-tool analyses, never private context.
"""
import re
import unicodedata

OMITTED = 'Statement withheld by the public content policy.'
REDACTED = '[redacted]'

# Keep labels indivisible across whitespace (including Unicode) and punctuation.
PAIR = r"\b(?:api|access|refresh|auth|session)(?:'s)?[\W_]*(?:keys?|tokens?)\b"
# Explicit labels share the same separators; values are never guessed/masked.
SENSITIVE_LABEL = r'(?:' + PAIR + r'|\b(?:passwords?|passphrases?|secrets?|credentials?|authorization|bearer)\b)'
DECLARATION = re.compile(
    SENSITIVE_LABEL + r'''\s*(?:[:=;,.()'"“”‘’-]|is\b|equals\b|value\b)'''
    # A label-led clause with a bare value is ambiguous. Grammatical modal
    # predicates and established noun compounds are not value declarations.
    r'|(?:^|[;:.!?]\s*)(?!secret sharing\b)' + SENSITIVE_LABEL
    + r'\s+(?!(?:can|could|may|might|must|should|will|would|are|were|have|has)\s+\S)\S+'
    r"|\b(?:private|ssh|rsa|openpgp)\s+key\b"
    r'''|\btokens?(?:'s)?\s*(?:-(?!budgets?\b)|[.:;,()'"“”‘’]|is\b|equals\b|value\b)'''
    r"|(?:^|[;:.!?]\s*)tokens?(?:'s)?\s+(?!(?:budgets?\b|(?:can|may|should|are)\s+\S))\S+"
    r"|\b(?:provider[ -]+logs?|account(?:[ -]*(?:id|number|name))?|username|login|email|phone|address|file[ -]*name)\s*(?:[:=-]|is\b|equals\b)"
    r"|\b(?:my|our|your)\s+(?:private|personal|home|account|colleague|password|secret|email|phone|address|login)\b"
    r'''|\bcontact\s*[:=;\-"“‘']'''
    r"|\bcontact\s+(?:[^\W\d_]+[ -]){1,3}[^\W\d_]+\s+(?:for|at|on)\b"
    r"|\b(?:I live at|bearer\s+(?!tokens?\b)\S+|contact\s+(?-i:[A-Z][a-z]+ [A-Z][a-z]+))"
    r'|(?:^|[;:]\s*)contact\s+[^\W\d_]+(?:[ -][^\W\d_]+){0,2}[.!?]?(?=\s*(?:$|;))'
    r'|\bartifact\s+(?:[\w-]+ +)+[\w-]+(?:\.[\w-]+)*\.[A-Za-z][A-Za-z0-9]{0,11}\b'
    r"|\bprivate[ -]+(?:project|repository|report|file|notes?)\b", re.I)
ENCODED = re.compile(r'%[0-9a-f]{2}|&#(?:x[0-9a-f]+|[0-9]+);?|&(?:sol|bsol|colon|commat);'
                     r'|\\(?:u[0-9a-f]{4}|x[0-9a-f]{2})|\b[A-Za-z0-9+/]{16,}={1,2}', re.I)
CREDENTIAL = re.compile(
    r'\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]+|'
    r'AKIA[A-Z0-9]{16}|ASIA[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]+|AIza[A-Za-z0-9_-]{20,})\b'
    r'|\bssh-(?:rsa|ed25519)\s+[A-Za-z0-9+/=]+'
    r'|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b'
    r'|\b[0-9a-f]{32,}\b', re.I)
# Multiple letter/digit transitions are credential-shaped, unlike F1, GPT-4o,
# Llama-3.1-8B or ordinary model sizes. Titles never authorize credential strings.
OPAQUE = re.compile(r'\b(?=[A-Za-z0-9_-]{4,}\b)(?:[A-Za-z]+[0-9]+[-_]?){2,}[A-Za-z0-9_-]*\b'
                    r'|\b(?=[A-Za-z0-9_-]{24,}\b)(?=[A-Za-z0-9_-]*[0-9])(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]+\b')
PRIVATE_SPAN = re.compile(
    r'''["'](?:[A-Za-z]:[\\/]|/|~/|\.\.?[/\\])[^"'\n]+["']'''
    r'|(?:[/\\][\w.-]+)+ +[\w.-]+(?:[/\\][\w.-]+)+'
    r'|(?:[A-Za-z][A-Za-z0-9+.-]*://|www\.)[^\s<>]+|\bmailto:[^\s<>]+'
    r'|(?:\b[\w.+-]+:)?[\w.+-]+@[\w.-]+(?::[^\s<>]+)?|(?<!\w)@[A-Za-z0-9_]{2,}'
    r'|\b(?:id_rsa|id_ed25519|authorized_keys|known_hosts|Makefile|Dockerfile)\b'
    r'|(?<!\w)(?:[A-Za-z]:[\\/]|\\\\|~/|~[\w.-]+/|\$\{?HOME\}?/|/)[^\s,;<>]+'
    r'|(?<!\w)(?:\.\.?[/\\]|[\w.-]+[/\\])[^\s,;<>]+'
    r'|(?<![\w.])(?:\.[A-Za-z][\w-]*|[\w-]+(?:\.[\w-]+)*\.[A-Za-z][A-Za-z0-9]{0,11})(?![\w.])'
    r'|(?<![\w.])(?:\+\d{1,3}\.\d{2,4}\.\d{3,4}\.\d{4}|\d{3}\.\d{3}\.\d{4})(?![\w.]\d)'
    r'|\b(?:\d{1,3}\.){3}\d{1,3}\b'
    r'|(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)', re.I)


# Recognize file context, but never try to extract a possibly multiword name.
FILENAME = re.compile(
    r'(?<![\w.])(?:\.[A-Za-z][\w-]*|[\w-]+(?:\.[\w-]+)*\.[A-Za-z][A-Za-z0-9]{0,11})(?![\w.])'
    r'|\b(?:id_rsa|id_ed25519|authorized_keys|known_hosts|Makefile|Dockerfile)\b', re.I)
LOCAL_PATH = re.compile(
    r'(?<!\w)(?:[A-Za-z]:[\\/]|\\\\|~/|~[\w.-]+/|\$\{?HOME\}?/|/|\.\.?[/\\]|[\w.-]+[/\\])[^\s,;<>]+')


# Detect declaration context independently of a guessed value/name endpoint.
# Unicode punctuation is a lexical boundary; neither capitalization, articles,
# nor sentence placement authorize a sensitive value.
LABEL = re.compile(SENSITIVE_LABEL + r"|\btokens?(?:'s)?\b|\bcontact\b", re.I)
WORD = re.compile(r'[^\W_]+', re.UNICODE)
ARTICLES = {'the', 'a', 'an', 'my', 'our', 'your', 'this', 'that'}
MODALS = {'can', 'could', 'may', 'might', 'must', 'should', 'will', 'would',
          'are', 'were', 'have', 'has'}


def declaration_context(value):
    """Reject a declaration as a field, without extracting its private value."""
    for label in LABEL.finditer(value):
        rest = value[label.end():]
        next_word = WORD.search(rest)
        if not next_word:
            continue
        word = next_word.group().casefold()
        gap = rest[:next_word.start()]
        kind = label.group().casefold()
        # Established scientific compounds, not a vocabulary for whole prose.
        compound = ((kind in ('secret', 'secrets') and word == 'sharing') or
                    (kind in ('token', 'tokens') and word in ('budget', 'budgets')) or
                    (kind == 'contact' and word == 'dynamics'))
        if compound and all(c.isspace() or c == '-' for c in gap):
            continue
        if any(unicodedata.category(c)[0] in 'PS' for c in gap):
            return True
        if word in ('is', 'equals', 'value'):
            return True
        if word in MODALS:
            continue
        before = value[:label.start()].rstrip()
        previous = list(WORD.finditer(before))
        if previous and previous[-1].group().casefold() in ARTICLES:
            return True
        if not before or unicodedata.category(before[-1])[0] in 'PS':
            return True
    for label in re.finditer(r'\b(?:call|phone|telephone|mobile)\b', value, re.I):
        number = re.match(r'[\s:=+().\-\d]+', value[label.end():])
        if number and sum(c.isdecimal() for c in number.group()) >= 7:
            return True
    return bool(DECLARATION.search(value))


def sanitize(value, quotations=()):
    """Return safe text and a fixed reason; never include raw input in diagnostics."""
    if value == OMITTED:
        return OMITTED, 'omitted'
    if not isinstance(value, str) or not 1 <= len(value) <= 1000:
        return OMITTED, 'invalid_text'
    if (any(unicodedata.category(c).startswith('C') and c not in '\t\n\r' for c in value)
            or ENCODED.search(value)):
        return OMITTED, 'encoded_or_control'
    folded = unicodedata.normalize('NFKC', value)
    if folded != value:
        checked, reason = sanitize(folded)
        if checked == OMITTED or reason == 'redacted':
            return OMITTED, 'encoded_or_control'
    value = value.translate(str.maketrans({'’': "'", '–': '-', '—': '-',
                                          '%': ' percent', '×': ' times'}))
    value = ' '.join(value.split())
    # Protected public notation does not authorize any surrounding private text.
    from .research_public import checked_id
    public_links = []
    for link in re.finditer(r'https://arxiv\.org/abs/[^\s<>]+', value):
        url = link.group().rstrip('.,;)')
        try:
            checked_id(url.removeprefix('https://arxiv.org/abs/'))
        except ValueError:
            continue
        public_links.append((link.start(), link.end()))
    public_links += [(m.start(), m.end()) for m in re.finditer(
        r'(?<![\w/])(?:CI/CD|[0-9]+/[A-Za-z]|[0-9]+/[0-9]+)(?![\w/])', value)]
    if declaration_context(value):
        return OMITTED, 'sensitive_context'
    # A path or filename can contain spaces, punctuation, opposite quotes and
    # arbitrary name components. Do not guess its endpoint or publish remnants.
    if any(not any(a <= m.start() and m.end() <= b for a, b in public_links)
           for pattern in (LOCAL_PATH, FILENAME) for m in pattern.finditer(value)):
        return OMITTED, 'private_file_context'
    if any(not any(a <= m.start() and m.end() <= b for a, b in public_links)
           for pattern in (PRIVATE_SPAN, CREDENTIAL, OPAQUE) for m in pattern.finditer(value)):
        return OMITTED, 'private_span'
    plain = value.replace(REDACTED, '')
    if any(c in plain for c in '<>\\`{}[]'):
        return OMITTED, 'unsupported_markup'
    if REDACTED in value and len(re.findall(r'[^\W\d_]+', plain)) < 5:
        return OMITTED, 'private_span'
    words = re.findall(r'[a-z0-9]+', value.lower())
    if not words:
        return OMITTED, 'invalid_text'
    # Quotation fields are never copied. Public-source phrase overlap in a
    # model statement is not a privacy signal, and does not justify censorship.
    return value, 'redacted' if REDACTED in value else 'retained'
