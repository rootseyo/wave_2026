"""Read query_runner data.jsonl / data.html rows without loading the export into a DOM."""
import json
import mmap
from pathlib import Path
import re

# Only locate row boundaries here. Cell structure/entities are parsed by BeautifulSoup.
ROW_TAG = re.compile(br'<\s*(/?)\s*tr\b[^>]*>', re.I)
CELL_RE = re.compile(br'<t[dh]\b[^>]*>(.*?)</t[dh]>', re.I | re.S)
TAG_STRIP_RE = re.compile(br'<[^>]+>')

# Pattern to detect actual HTML elements or JEditor markup in content
HTML_ELEMENT_PATTERN = re.compile(
    r'(?:</[a-zA-Z][a-zA-Z0-9]*>|'
    r'<[a-zA-Z][a-zA-Z0-9]*(?:\s+[^>]*?)?(?:/?>|\s)|'
    r'jeditor)',
    re.IGNORECASE | re.DOTALL,
)


def has_html_elements(text) -> bool:
    """Checks whether the text contains actual HTML elements or JEditor markup."""
    if not text:
        return False
    if "jeditor" in str(text).lower():
        return True
    return bool(HTML_ELEMENT_PATTERN.search(str(text)))


def _advise_sequential(data):
    if hasattr(data, 'madvise') and hasattr(mmap, 'MADV_SEQUENTIAL'):
        try:
            data.madvise(mmap.MADV_SEQUENTIAL)
        except Exception:
            pass


def _release_pages(data, upto_offset):
    if hasattr(data, 'madvise') and hasattr(mmap, 'MADV_DONTNEED'):
        page_size = getattr(mmap, 'PAGESIZE', 4096)
        aligned = (upto_offset // page_size) * page_size
        if aligned > 0:
            try:
                data.madvise(mmap.MADV_DONTNEED, 0, aligned)
            except Exception:
                pass


def _is_jsonl_export(path: Path) -> bool:
    """Detect whether the export file is JSON Lines (.jsonl / .ndjson) or starts with a JSON object."""
    suffix = path.suffix.lower()
    if suffix in ('.jsonl', '.ndjson'):
        return True
    if suffix in ('.html', '.htm'):
        return False
    try:
        with path.open('rb') as f:
            head = f.read(512).lstrip()
            return head.startswith(b'{')
    except Exception:
        return False


def count_export_rows(path, projects=None, fields=None):
    """Count export rows in data.jsonl or data.html without loading DOM into memory.

    Optionally filter by projects (iterable of str) or fields (iterable of str).
    Returns 0 if the file does not exist, is empty, or header cannot be parsed.
    """
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        return 0
    if _is_jsonl_export(path):
        projects_u = {str(p).upper() for p in projects} if projects else None
        fields_s = {str(f) for f in fields} if fields else None
        try:
            with path.open('rb', buffering=4 * 1024 * 1024) as stream:
                if not projects_u and not fields_s:
                    count = 0
                    last_byte = b'\n'
                    while True:
                        chunk = stream.read(4 * 1024 * 1024)
                        if not chunk:
                            break
                        count += chunk.count(b'\n')
                        last_byte = chunk[-1:]
                    if last_byte != b'\n':
                        count += 1
                    return count
                count = 0
                for raw_line in stream:
                    stripped = raw_line.strip()
                    if not stripped:
                        continue
                    obj = json.loads(stripped.decode('utf-8'))
                    if projects_u:
                        pk = str(obj.get('project_key') or '').upper()
                        if pk not in projects_u:
                            continue
                    if fields_s:
                        fid = str(obj.get('field_id') or '')
                        if fid not in fields_s:
                            continue
                    count += 1
                return count
        except Exception:
            return 0
    projects_b = {p.encode('utf-8').upper() for p in projects} if projects else None
    fields_b = {f.encode('utf-8') for f in fields} if fields else None
    try:
        with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            _advise_sequential(data)
            position = 0
            after_header = None
            headers = []
            while True:
                match = ROW_TAG.search(data, position)
                if match is None:
                    break
                if match.group(1):
                    position = match.end()
                    continue
                end = _row_end(data, match.start())
                chunk = data[match.start():end]
                if b'issue_key' in chunk.lower() and b'field_id' in chunk.lower():
                    headers = [TAG_STRIP_RE.sub(b'', m.group(1)).strip().lower()
                               for m in CELL_RE.finditer(chunk)]
                    after_header = end
                    break
                position = end
            if after_header is None:
                return 0
            proj_idx = headers.index(b'project_key') if b'project_key' in headers else None
            field_idx = headers.index(b'field_id') if b'field_id' in headers else None
            need_filter = bool((projects_b and proj_idx is not None) or (fields_b and field_idx is not None))

            count = 0
            position = after_header
            last_released = 0
            release_step = 16 * 1024 * 1024
            while True:
                match = ROW_TAG.search(data, position)
                if match is None:
                    break
                if match.group(1):
                    position = match.end()
                    continue
                end = _row_end(data, match.start())
                if need_filter:
                    chunk = data[match.start():end]
                    cells = [TAG_STRIP_RE.sub(b'', m.group(1)).strip() for m in CELL_RE.finditer(chunk)]
                    if projects_b and proj_idx < len(cells) and cells[proj_idx].upper() not in projects_b:
                        position = end
                        continue
                    if fields_b and field_idx < len(cells) and cells[field_idx] not in fields_b:
                        position = end
                        continue
                count += 1
                position = end
                if position - last_released >= release_step:
                    _release_pages(data, position)
                    last_released = position
            _release_pages(data, position)
            return count
    except Exception:
        return 0


def _row_end(data, start):
    depth = 0
    for match in ROW_TAG.finditer(data, start):
        depth += -1 if match.group(1) else 1
        if depth == 0:
            return match.end()
    raise ValueError('Unclosed <tr> in input export')


def _headers(data):
    from bs4 import BeautifulSoup
    position = 0
    while True:
        match = ROW_TAG.search(data, position)
        if match is None:
            raise ValueError('data.html header row with issue_key/content was not found')
        if match.group(1):
            position = match.end()
            continue
        end = _row_end(data, match.start())
        soup = BeautifulSoup(data[match.start():end].decode('utf-8'), 'html.parser')
        row = soup.find('tr')
        headers = [cell.get_text(strip=True).lower() for cell in row.find_all('th', recursive=False)]
        if {'issue_key', 'field_id', 'content'} <= set(headers):
            return headers, end
        position = end


def _content(cell):
    """Undo ONLY the export presentation layer (escape + newline -> br/newline)."""
    from bs4 import NavigableString
    breaks = list(cell.find_all('br'))
    # Some exports put presentation newlines on both sides of <br>. Remove those
    # BEFORE inserting semantic newlines, so adjacent <br><br> remain two breaks.
    for br in breaks:
        preceding, following = br.previous_sibling, br.next_sibling
        if isinstance(preceding, NavigableString) and str(preceding).endswith(('\n', '\r')):
            preceding.replace_with(str(preceding).rstrip('\r\n'))
        if isinstance(following, NavigableString) and str(following).startswith(('\n', '\r')):
            following.replace_with(str(following).lstrip('\r\n'))
    for br in breaks:
        br.replace_with('\n')
    # Encoded source tags become text here. A second parser handles that source HTML.
    # Actual nested tags are preserved for exports that contain unescaped HTML.
    return ''.join(str(child) if isinstance(child, NavigableString) else str(child)
                   for child in cell.contents)


def _parse(data, start, end, headers, path):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(data[start:end].decode('utf-8'), 'html.parser')
    try:
        row = soup.find('tr')
        cells = row.find_all(['td', 'th'], recursive=False)
        if len(cells) != len(headers):
            raise ValueError(f'Export row at byte {start} has {len(cells)} cells; expected {len(headers)}')
        result = {name: cell.get_text(strip=True) for name, cell in zip(headers, cells)}
        result['content'] = _content(cells[headers.index('content')])
        result['offset'] = start
        result['source_file'] = str(Path(path).resolve())
        return result
    finally:
        soup.decompose()


def read_export(path, issue_key=None):
    """Yield all matching field rows, including non-contiguous occurrences.

    Supports both JSONL (data.jsonl) and HTML table (data.html) exports.
    Single-issue mode uses a byte search and parses matching rows only; it does
    not count rows or pre-parse the entire export.
    """
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f'Input export does not exist or is empty: {path}')
    if _is_jsonl_export(path):
        resolved_path = str(path.resolve())
        target_upper = issue_key.upper() if issue_key else None
        target_bytes = target_upper.encode('ascii') if target_upper else None
        with path.open('rb', buffering=4 * 1024 * 1024) as stream:
            offset = 0
            for raw_line in stream:
                line_offset = offset
                offset += len(raw_line)
                stripped = raw_line.strip()
                if not stripped:
                    continue
                if target_bytes is not None and target_bytes not in stripped.upper():
                    continue
                obj = json.loads(stripped.decode('utf-8'))
                if not isinstance(obj, dict):
                    raise ValueError(f'Invalid JSONL row at byte {line_offset}')
                row = {
                    str(k).lower(): ("" if v is None else (v if isinstance(v, str) else str(v)))
                    for k, v in obj.items()
                }
                if not ({'issue_key', 'field_id', 'content'} <= set(row.keys())):
                    raise ValueError(f'Export row at byte {line_offset} missing issue_key/field_id/content')
                row['offset'] = line_offset
                row['source_file'] = resolved_path
                if target_upper is not None and row['issue_key'].upper() != target_upper:
                    continue
                yield row
        return
    with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
        _advise_sequential(data)
        headers, after_header = _headers(data)
        last_released = 0
        release_step = 16 * 1024 * 1024
        if issue_key:
            key = re.escape(issue_key.encode('ascii'))
            needle = re.compile(br'<td\b[^>]*>\s*' + key + br'\s*</td\s*>', re.I)
            # Export metadata columns precede content, so the preceding tr is the outer row.
            if headers.index('issue_key') > headers.index('content'):
                raise ValueError('Single-issue lookup requires issue_key before content in export headers')
            previous_end = after_header
            for match in needle.finditer(data, after_header):
                if match.start() < previous_end:
                    continue
                # Find the preceding opening row token in a bounded metadata prefix.
                prefix = max(after_header, match.start() - 1024 * 1024)
                candidates = [tag for tag in ROW_TAG.finditer(data, prefix, match.start()) if not tag.group(1)]
                if not candidates:
                    continue
                start = candidates[-1].start()
                end = _row_end(data, start)
                previous_end = end
                row = _parse(data, start, end, headers, path)
                if end - last_released >= release_step:
                    _release_pages(data, start)
                    last_released = end
                if row['issue_key'].upper() == issue_key.upper():
                    yield row
        else:
            position = after_header
            while True:
                match = ROW_TAG.search(data, position)
                if match is None:
                    break
                if match.group(1):
                    position = match.end()
                    continue
                end = _row_end(data, match.start())
                row = _parse(data, match.start(), end, headers, path)
                position = end
                if position - last_released >= release_step:
                    _release_pages(data, match.start())
                    last_released = position
                yield row
