"""Private PDF text-layer worker; invoked with a fixed argv, never paper code."""
import json
from pathlib import Path
import resource
import sys


def extract(path):
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise RuntimeError('Install PDF fallback: pip install -e ".[research]"') from error
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ValueError('Encrypted PDF is unsupported')
    if len(reader.pages) > 200:
        raise ValueError('PDF page budget exceeded (200)')
    blocks, total = [], 0
    for n, page in enumerate(reader.pages, 1):
        text = ' '.join((page.extract_text() or '').split())
        total += len(text)
        if total > 2 * 1024 * 1024:
            raise ValueError('PDF extracted character budget exceeded')
        for start in range(0, len(text), 1400):
            blocks.append({'locator': 'P%04dB%03d' % (n, start // 1400 + 1), 'text': text[start:start + 1400]})
    if total < 1000:
        raise ValueError('PDF lacks a usable text layer; OCR is not enabled')
    return blocks


def main():
    # Bound CPU and address space independently of the parent's wall-clock cap.
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    if sys.platform != 'darwin':
        resource.setrlimit(resource.RLIMIT_AS, (1024 * 1024 * 1024, 1024 * 1024 * 1024))
    # macOS does not reliably enforce AS/RSS limits; page, input, output, CPU
    # and parent wall-clock budgets still apply. This is not an OS sandbox.
    try:
        print(json.dumps(extract(Path(sys.argv[1])), ensure_ascii=False))
    except Exception as exc:
        print(type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
