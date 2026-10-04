"""Git transport for the sanitized outbox; never operate in the research repo."""
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import tempfile
import time

# Imported by research_public only after its boundary definitions are available.
from .research_public import PublicError, TARGET, checked_day, render_document, validate_artifact

LEGACY_README = b'''# Agentic research

Automated research digests of public arXiv papers, organized by calendar date.
Reports contain model-generated interpretation and unexecuted experiment proposals.
They are not independently verified evidence. Consult the original papers.

Public titles and pinned identifiers are checked against arXiv metadata. The
conservative publication vocabulary can withhold entire model statements.
No source quotations, raw downloads, provider receipts or private reports belong here.
'''
README = LEGACY_README.replace(
    b'conservative publication vocabulary can withhold entire model statements.',
    b'privacy checks redact identifiable private spans, not scientific vocabulary.').replace(
    b'No source quotations, raw downloads, provider receipts or private reports belong here.',
    b'Quotation fields, raw downloads, provider receipts and private reports are not exported.\nModel summaries may repeat public source language; privacy heuristics are not a secrecy guarantee.')
GIT_CONFIG = b'[core]\nrepositoryformatversion = 0\nbare = false\nfilemode = true\nlogallrefupdates = false\n'
IDENTITY = 'TR Ingram <14-TR@users.noreply.github.com>'


def safe_path(path):
    path = Path(path).absolute()
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise PublicError('unsafe_public_staging')
    return path


def regular_bytes(path, limit=100000):
    safe_path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        raise PublicError('unsafe_public_staging')
    return path.read_bytes()


class GitPublisher:
    def __init__(self, root, target):
        if target != TARGET:
            raise PublicError('invalid_public_target')
        self.root = safe_path(root)
        self.repo = self.root / 'repo'
        self.deadline = 0

    def _remote(self):
        # Tests replace this method with a local bare fixture. There is deliberately
        # no config, CLI or environment switch permitting another destination.
        return TARGET

    def _git(self, *args, allow_failure=False):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise PublicError('public_git_timeout')
        env = {k: v for k, v in os.environ.items() if k in ('HOME', 'TMPDIR', 'SYSTEMROOT')}
        env.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin', LC_ALL='C', TZ='UTC',
                   GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_TERMINAL_PROMPT='0', GIT_ASKPASS='/usr/bin/false',
                   GIT_ALLOW_PROTOCOL='https:file', GIT_NO_REPLACE_OBJECTS='1',
                   GIT_AUTHOR_NAME='TR Ingram', GIT_COMMITTER_NAME='TR Ingram',
                   GIT_AUTHOR_EMAIL='14-TR@users.noreply.github.com',
                   GIT_COMMITTER_EMAIL='14-TR@users.noreply.github.com')
        argv = ['/usr/bin/git', '-c', 'core.hooksPath=' + os.devnull,
                '-c', 'core.fsmonitor=false', '-c', 'core.attributesFile=' + os.devnull,
                '-c', 'commit.gpgSign=false', '-c', 'credential.helper=',
                '-c', 'credential.helper=osxkeychain', '-c', 'credential.interactive=false',
                '-c', 'credential.useHttpPath=true',
                '-c', 'http.followRedirects=false', '-c', 'http.sslVerify=true',
                '-c', 'http.lowSpeedLimit=1000', '-c', 'http.lowSpeedTime=15', *args]
        try:
            with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
                result = subprocess.Popen(argv, cwd=self.repo, env=env, stdin=subprocess.DEVNULL,
                                          stdout=out, stderr=err, start_new_session=True)
                try:
                    result.wait(timeout=min(30, remaining))
                finally:
                    # A transport or credential helper must not outlive timeout,
                    # cancellation, or even a successfully exited Git parent.
                    try:
                        os.killpg(result.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    result.wait()
                if out.tell() > 2000000 or err.tell() > 100000:
                    raise PublicError('public_git_output_limit')
                out.seek(0)
                data = out.read()
        except subprocess.TimeoutExpired:
            raise PublicError('public_git_timeout') from None
        except OSError:
            raise PublicError('public_git_unavailable') from None
        if result.returncode and not allow_failure:
            # Never forward stderr: it may contain credentials, URLs or local paths.
            raise PublicError('public_git_failed')
        return result.returncode, data

    def _text(self, *args):
        try:
            return self._git(*args)[1].decode('ascii').strip()
        except UnicodeError:
            raise PublicError('invalid_public_git_output') from None

    def _initialize(self):
        from .research import atomic
        if {p.name for p in self.root.iterdir()} - {'run.lock', 'repo'}:
            raise PublicError('unexpected_public_staging_file')
        safe_path(self.repo)
        if not self.repo.exists():
            self.repo.mkdir(mode=0o700)
        if not (self.repo / '.git').exists():
            if list(self.repo.iterdir()):
                raise PublicError('unexpected_public_staging_file')
            self._git('init', '--template=', '--initial-branch=main')
            atomic(self.repo / '.git/config', GIT_CONFIG)
        self._check_git_metadata()

    def _check_git_metadata(self):
        if regular_bytes(self.repo / '.git/config') != GIT_CONFIG:
            raise PublicError('unsafe_public_git_config')
        if regular_bytes(self.repo / '.git/HEAD') != b'ref: refs/heads/main\n':
            raise PublicError('unsafe_public_git_head')
        # Reject redirection, injected hooks, object alternates and replacement refs.
        count = 0
        for directory, dirs, files in os.walk(self.repo / '.git', followlinks=False):
            for name in dirs + files:
                path = Path(directory) / name
                count += 1
                info = path.lstat()
                if count > 50000 or stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    raise PublicError('unsafe_public_git_metadata')
                if stat.S_ISREG(info.st_mode) and (info.st_nlink != 1 or info.st_size > 20000000):
                    raise PublicError('unsafe_public_git_metadata')
        for name in ('objects/info/alternates', 'objects/info/http-alternates', 'info/grafts', 'shallow'):
            if (self.repo / '.git' / name).exists():
                raise PublicError('unsafe_public_git_metadata')
        for name in ('hooks', 'refs/replace'):
            if (self.repo / '.git' / name).exists() and list((self.repo / '.git' / name).iterdir()):
                raise PublicError('unsafe_public_git_metadata')
        refs = self._text('for-each-ref', '--format=%(refname)').splitlines()
        if any(r != 'refs/heads/main' for r in refs):
            raise PublicError('unexpected_public_git_ref')

    def _remote_head(self):
        text = self._text('ls-remote', '--refs', self._remote(), 'refs/heads/main')
        if not text:
            return ''
        if not re.fullmatch(r'[a-f0-9]{40}\trefs/heads/main', text):
            raise PublicError('invalid_public_remote_head')
        return text.split('\t')[0]

    def _head(self):
        code, data = self._git('rev-parse', '--verify', 'HEAD', allow_failure=True)
        if code:
            return ''
        value = data.decode('ascii').strip()
        if not re.fullmatch('[a-f0-9]{40}', value):
            raise PublicError('invalid_public_git_head')
        return value

    def _tree(self, ref, allowed, *, index=False):
        raw = self._git(*(['ls-files', '--stage', '-z'] if index else ['ls-tree', '-r', '-z', ref]))[1]
        found = set()
        for item in raw.split(b'\0'):
            if not item:
                continue
            try:
                header, name = item.split(b'\t')
                mode, middle, last = header.decode('ascii').split(' ')
                name = name.decode('ascii')
            except (ValueError, UnicodeError):
                raise PublicError('unsafe_public_git_tree') from None
            sha = middle if index else last
            if (mode != '100644' or (index and last != '0') or (not index and middle != 'blob')
                    or name not in allowed or name in found or not re.fullmatch('[a-f0-9]{40}', sha)):
                raise PublicError('unsafe_public_git_tree')
            blob = self._git('cat-file', 'blob', sha)[1]
            if blob not in ((allowed[name],) if isinstance(allowed[name], bytes) else allowed[name]):
                raise PublicError('public_git_content_mismatch')
            found.add(name)
        return found

    def _worktree(self, allowed, permitted):
        found = set()
        for path in self.repo.iterdir():
            if path.name not in {'.git', 'reports', 'README.md'}:
                raise PublicError('unexpected_public_staging_file')
            safe_path(path)
        reports = self.repo / 'reports'
        if reports.exists() and not reports.is_dir():
            raise PublicError('unsafe_public_staging')
        paths = ([self.repo / 'README.md'] if (self.repo / 'README.md').exists() else [])
        paths += list(reports.iterdir()) if reports.exists() else []
        for path in paths:
            name = str(path.relative_to(self.repo))
            if name not in permitted or name not in allowed:
                raise PublicError('unexpected_public_staging_file')
            if regular_bytes(path) not in ((allowed[name],) if isinstance(allowed[name], bytes) else allowed[name]):
                raise PublicError('public_staged_content_mismatch')
            found.add(name)
        return found

    def _history(self, head, remote, allowed):
        # Only new objects can disclose local data. Verify every unpushed ancestor;
        # the remote tip is separately content-checked. Never merge or force-push.
        args = ['rev-list', '--max-count=33', head] + (['^' + remote] if remote else [])
        commits = self._text(*args).splitlines()
        if len(commits) > 32:
            raise PublicError('public_history_limit')
        for commit in commits:
            body = self._git('cat-file', 'commit', commit)[1].decode('ascii')
            headers, message = body.split('\n\n', 1)
            lines = headers.splitlines()
            if (not re.fullmatch(r'(?:Publish|Correct) research \d{4}-\d{2}-\d{2}\n', message)
                    or not re.fullmatch(r'tree [a-f0-9]{40}', lines[0])
                    or sum(line.startswith('parent ') for line in lines) > 1):
                raise PublicError('unsafe_public_commit')
            checked_day(message.split()[-1])
            for line in lines[1:]:
                if not (re.fullmatch(r'parent [a-f0-9]{40}', line)
                        or re.fullmatch(r'(author|committer) ' + re.escape(IDENTITY) + r' [0-9]+ \+0000', line)):
                    raise PublicError('unsafe_public_commit')
            self._tree(commit, allowed)

    def publish(self, day, documents, *, previous=None):
        from .research import atomic, locked
        checked_day(day)
        if day not in documents or len(documents) > 3660:
            raise PublicError('invalid_public_outbox')
        allowed = {'README.md': README}
        for key, document in documents.items():
            if checked_day(key) != document['day']:
                raise PublicError('invalid_public_outbox')
            data = render_document(document)
            validate_artifact(data, document)
            allowed['reports/' + key + '.md'] = data
        # Only a hash-verified prior document supplied by the private ledger may
        # differ at the target path. Arbitrary remote bytes are never adopted.
        accepted = dict(allowed, **{'README.md': (LEGACY_README, README)})
        if previous is not None:
            if previous['day'] != day:
                raise PublicError('invalid_public_revision')
            accepted['reports/' + day + '.md'] = (render_document(previous), allowed['reports/' + day + '.md'])
        self.deadline = time.monotonic() + 120
        with locked(self.root):
            self._initialize()
            remote = self._remote_head()
            head = self._head()
            if remote:
                self._git('fetch', '--quiet', '--no-tags', self._remote(), 'refs/heads/main')
                if self._text('rev-parse', 'FETCH_HEAD') != remote:
                    raise PublicError('public_remote_changed')
                self._tree(remote, accepted)
                if head and self._git('merge-base', '--is-ancestor', remote, head, allow_failure=True)[0]:
                    raise PublicError('public_remote_diverged')
            if not head and remote:
                # A new staging repo may recover only from already known safe bytes.
                if self._worktree(accepted, set()):
                    raise PublicError('unexpected_public_staging_file')
                self._git('read-tree', remote)
                self._tree('', accepted, index=True)
                self._git('checkout-index', '-a')
                self._git('update-ref', 'refs/heads/main', remote)
                head = remote
            prior = self._tree(head, accepted) if head else set()
            desired = 'reports/' + day + '.md'
            permitted = prior | {'README.md', desired}
            self._worktree(accepted, permitted)
            staged = self._tree('', accepted, index=True)
            if staged - permitted:
                raise PublicError('unexpected_public_index')
            atomic(self.repo / 'README.md', README)
            atomic(self.repo / desired, allowed[desired])
            self._git('add', '--', 'README.md', desired)
            work = self._worktree(allowed, permitted)
            if self._tree('', allowed, index=True) != work or work != permitted:
                raise PublicError('public_index_mismatch')
            if not head or self._git('diff', '--cached', '--quiet', allow_failure=True)[0]:
                self._git('commit', '--quiet', '--no-gpg-sign', '-m', ('Correct research ' if previous is not None else 'Publish research ') + day)
            head = self._head()
            self._history(head, remote, accepted)
            self._check_git_metadata()
            if self._tree(head, allowed) != work or self._tree('', allowed, index=True) != work:
                raise PublicError('public_index_mismatch')
            self._worktree(allowed, permitted)
            # A failed response may mean the server accepted the write. Read back
            # even then, and retry the same commit if acknowledgement is uncertain.
            try:
                if head != remote:
                    self._git('push', '--porcelain', '--no-verify', self._remote(), 'HEAD:refs/heads/main')
            except PublicError:
                pass
            if self._remote_head() != head:
                raise PublicError('public_push_unconfirmed')
            self._git('fetch', '--quiet', '--no-tags', self._remote(), 'refs/heads/main')
            if self._text('rev-parse', 'FETCH_HEAD') != head or self._tree('FETCH_HEAD', allowed) != work:
                raise PublicError('public_readback_mismatch')
            return head
