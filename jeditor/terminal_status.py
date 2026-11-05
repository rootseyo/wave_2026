"""ANSI terminal footer with a separate scrolling log area; no extra dependencies."""
import functools
import os
import shutil
import sys
import threading
import unicodedata


class _Output:
    def __init__(self, owner, stream):
        self.owner, self.stream = owner, stream

    def write(self, text):
        with self.owner.lock:
            self.owner._resize()
            return self.stream.write(text)

    def flush(self):
        with self.owner.lock:
            self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


class TerminalStatus:
    def __init__(self, max_lines=4):
        self.lock = threading.RLock()
        self.stream = None
        self.size = None
        self.lines = ['이슈 처리 준비 중...']
        self.enabled = False
        self.stop = threading.Event()
        self.thread = None
        self.max_lines = max_lines
        self.last_reserved = 0

    def _resize(self):
        size = shutil.get_terminal_size((80, 24))
        num_lines = min(max(1, len(self.lines)), min(self.max_lines, max(1, size.lines - 2)))
        if size != self.size or num_lines != self.last_reserved:
            self.size = size
            self.last_reserved = num_lines
            # Reserve bottom num_lines rows for multi-line status footer.
            # Logs scroll in rows 1..(lines - num_lines).
            bottom_margin = max(1, size.lines - num_lines)
            self.stream.write(f'\033[1;{bottom_margin}r\033[{bottom_margin};1H')
            self._draw()

    def _draw(self):
        if not self.size:
            return
        num_lines = self.last_reserved
        cmds = ['\0337']  # Save cursor
        for idx in range(num_lines):
            line_txt = self.lines[idx] if idx < len(self.lines) else ''
            row = self.size.lines - num_lines + 1 + idx
            width = 0
            chars = []
            for char in line_txt:
                if unicodedata.category(char).startswith('C'):
                    continue
                cells = 0 if unicodedata.combining(char) else (2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1)
                if width + cells >= self.size.columns:
                    break
                chars.append(char)
                width += cells
            cmds.append(f'\033[{row};1H\033[2K' + ''.join(chars))
        cmds.append('\0338')  # Restore cursor
        self.stream.write(''.join(cmds))
        self.stream.flush()

    def update(self, text):
        if not self.enabled:
            if isinstance(text, (list, tuple)):
                for l in text:
                    print(l, flush=True)
            else:
                print(text, flush=True)
            return
        with self.lock:
            if isinstance(text, (list, tuple)):
                self.lines = [str(x).rstrip() for x in text]
            else:
                self.lines = [l.rstrip() for l in str(text).split('\n')]
            if not self.lines:
                self.lines = ['']
            self._resize()
            self._draw()

    def _refresh(self):
        while not self.stop.wait(0.25):
            with self.lock:
                if self.enabled:
                    self._resize()
                    self._draw()

    def __enter__(self):
        self.stream = sys.stdout
        self.enabled = self.stream.isatty() and os.environ.get('TERM', '') != 'dumb'
        if self.enabled:
            self.original_stdout, self.original_stderr = sys.stdout, sys.stderr
            with self.lock:
                self._resize()
            sys.stdout = _Output(self, sys.stdout)
            if sys.stderr.isatty():
                sys.stderr = _Output(self, sys.stderr)
            self.thread = threading.Thread(target=self._refresh, daemon=True)
            self.thread.start()
        return self

    def __exit__(self, *exc):
        if self.enabled:
            self.stop.set()
            self.thread.join()
            with self.lock:
                sys.stdout, sys.stderr = self.original_stdout, self.original_stderr
                # Restore the full scrolling region on success, error, or Ctrl+C.
                self.stream.write('\033[r')
                start_row = max(1, self.size.lines - self.last_reserved + 1)
                self.stream.write(f'\033[{start_row};1H\033[J\n')
                self.stream.flush()
                self.enabled = False


_active = None


def progress_status(text):
    if _active is None:
        print(text, flush=True)
    else:
        _active.update(text)


def with_terminal_status(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        global _active
        previous = _active
        try:
            with TerminalStatus() as status:
                _active = status
                return function(*args, **kwargs)
        finally:
            _active = previous
    return wrapped
