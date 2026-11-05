"""Markdown token mapping and Jira pipe-table recovery for Jira Cloud ADF."""
from copy import deepcopy
import re


def _slice(nodes, start, end):
    result, offset = [], 0
    for node in nodes:
        text = node['text']
        left, right = max(start - offset, 0), min(end - offset, len(text))
        if left < right:
            item = deepcopy(node)
            item['text'] = text[left:right]
            result.append(item)
        offset += len(text)
    return result


def _row(nodes, allow_single_column=False):
    text = ''.join(n['text'] for n in nodes)
    stripped = text.strip()
    if not (stripped.startswith('|') and stripped.endswith('|')):
        return None
    # Code-marked pipes and escaped pipes are literal text, not separators.
    code_positions, offset = set(), 0
    for node in nodes:
        if any(m.get('type') == 'code' for m in node.get('marks', [])):
            code_positions.update(range(offset, offset + len(node['text'])))
        offset += len(node['text'])
    separators, escaped, ticks = [], False, False
    for i, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == '\\':
            escaped = True
        elif char == '`':
            ticks = not ticks
        elif char == '|' and not ticks and i not in code_positions:
            separators.append(i)
    # Jira wiki header syntax: ||a||b||
    wiki = stripped.startswith('||') and stripped.endswith('||')
    if wiki:
        pairs = []
        i = 0
        while i < len(separators):
            if i + 1 >= len(separators) or separators[i + 1] != separators[i] + 1:
                return None
            pairs.append((separators[i], separators[i + 1] + 1))
            i += 2
    else:
        pairs = [(i, i + 1) for i in separators]
    if len(pairs) < (2 if allow_single_column else 3):
        return None
    cells = []
    for (_, left), (right, _) in zip(pairs, pairs[1:]):
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        cells.append(_slice(nodes, left, right))
    delimiter = all(re.fullmatch(r':?-{3,}:?', ''.join(n['text'] for n in cell)) for cell in cells)
    return cells, wiki, delimiter


def _strong_header(cells):
    texts = [''.join(node['text'] for node in cell) for cell in cells]
    return all(len(text) > 2 and text.startswith('*') and text.endswith('*') for text in texts)


def _unfold_line(nodes):
    """Recover space-separated wiki rows only when a bold header gives the width.

    Whitespace alone is ambiguous with empty cells. Once the header establishes
    N columns, consume exactly N cells for each row, preserving empty cells.
    """
    text = ''.join(node['text'] for node in nodes)
    for match in re.finditer(r'(?<!\S)\|(?:\*[^|]+\*\|){2,}', text):
        header_nodes = _slice(nodes, match.start(), match.end())
        header = _row(header_nodes)
        if not header or not _strong_header(header[0]):
            continue
        width = len(header[0])
        parts = [header_nodes]
        end = match.end()
        while end < len(text):
            start = end
            while start < len(text) and text[start].isspace():
                start += 1
            if start == end or start >= len(text) or text[start] != '|':
                break
            cursor, escaped, ticks, separators = start + 1, False, False, 0
            while cursor < len(text):
                char = text[cursor]
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '`':
                    ticks = not ticks
                elif char == '|' and not ticks:
                    separators += 1
                    if separators == width:
                        break
                cursor += 1
            if separators != width:
                break
            candidate = _slice(nodes, start, cursor + 1)
            row = _row(candidate)
            if row is None or len(row[0]) != width:
                break
            # A row must end here, rather than in the middle of a wider row.
            if cursor + 1 < len(text) and not text[cursor + 1].isspace():
                break
            parts.append(candidate)
            end = cursor + 1
        if len(parts) < 2 and not (match.start() > 0 and not text[end:].strip()):
            continue
        if match.start():
            parts.insert(0, _slice(nodes, 0, match.start()))
        if end < len(text) and text[end:].strip():
            parts.append(_slice(nodes, end, len(text)))
        return parts
    return None


def convert_pipe_tables(document, allow_single_column=False):
    """Return (copied ADF document, number of tables converted).

    Recognizes bordered pipe rows with >=2 cells, optional Markdown separators,
    and Jira wiki ||headers||. Existing ADF tables and code blocks are untouched.
    """
    if not isinstance(document, dict) or document.get('type') != 'doc' or document.get('version') != 1 or not isinstance(document.get('content'), list):
        raise ValueError('Expected a Jira Cloud ADF document (version 1)')
    result = deepcopy(document)
    count = 0

    def paragraphs(group):
        nonlocal count
        # Preserve original paragraphs exactly unless a table is found in this run.
        lines = []
        for paragraph in group:
            current = []
            for node in paragraph.get('content', []):
                if node['type'] == 'hardBreak':
                    lines.append((current, paragraph))
                    current = []
                else:
                    chunks = node['text'].split('\n')
                    for index, chunk in enumerate(chunks):
                        if index:
                            lines.append((current, paragraph))
                            current = []
                        if chunk:
                            item = deepcopy(node)
                            item['text'] = chunk
                            current.append(item)
            lines.append((current, paragraph))
        output, changed, i = [], False, 0
        untouched, converted_sources = {}, set()
        fence = None
        while i < len(lines):
            line_text = ''.join(n['text'] for n in lines[i][0]).strip()
            marker = re.match(r'^(`{3,}|~{3,})', line_text)
            was_fenced = fence is not None
            if marker:
                token = marker.group(1)
                if fence is None:
                    fence = token
                elif token[0] == fence[0] and len(token) >= len(fence):
                    fence = None
            if not was_fenced and fence is None and not marker:
                expanded = _unfold_line(lines[i][0])
                if expanded:
                    original = lines[i][1]
                    lines[i:i + 1] = [(part, original) for part in expanded]
            row = None if was_fenced or fence is not None or marker else _row(lines[i][0], allow_single_column)
            if row is None or row[2]:
                nodes, original = lines[i]
                block = deepcopy(original)
                block['content'] = nodes
                output.append(block)
                untouched[id(block)] = original
                i += 1
                continue
            width = len(row[0])
            rows = [row]
            j = i + 1
            while j < len(lines):
                candidate = _row(lines[j][0], allow_single_column)
                if candidate is None or len(candidate[0]) != width:
                    break
                if candidate[2] and len(rows) != 1:
                    break
                rows.append(candidate)
                j += 1
            bold_header = _strong_header(rows[0][0])
            header = (len(rows) > 1 and rows[1][2]) or bold_header
            table_rows = []
            for index, (cells, wiki, delimiter) in enumerate(rows):
                if delimiter:
                    continue
                if index == 0 and bold_header:
                    formatted = []
                    for cell in cells:
                        length = sum(len(node['text']) for node in cell)
                        text = ''.join(node['text'] for node in cell)
                        edge = 2 if text.startswith('**') and text.endswith('**') and len(text) > 4 else 1
                        clean = _slice(cell, edge, length - edge)
                        for node in clean:
                            if not any(mark.get('type') == 'strong' for mark in node.get('marks', [])):
                                node.setdefault('marks', []).append({'type': 'strong'})
                        formatted.append(clean)
                    cells = formatted
                kind = 'tableHeader' if wiki or (header and index == 0) else 'tableCell'
                table_rows.append({'type': 'tableRow', 'content': [
                    {'type': kind, 'content': [{'type': 'paragraph', 'content': cell}]} for cell in cells]})
            converted_sources.update(id(original) for _, original in lines[i:j])
            output.append({'type': 'table', 'content': table_rows})
            count += 1
            changed = True
            i = j
        if not changed:
            return group
        restored, seen = [], set()
        for node in output:
            original = untouched.get(id(node))
            if original is not None and id(original) not in converted_sources:
                if id(original) not in seen:
                    restored.append(original)
                    seen.add(id(original))
            else:
                restored.append(node)
        return restored

    def blocks(content):
        output, pending = [], []
        def flush():
            if pending:
                output.extend(paragraphs(pending))
                pending.clear()
        for node in content:
            eligible = node.get('type') == 'paragraph' and all(
                n.get('type') in ('text', 'hardBreak') for n in node.get('content', []))
            if eligible:
                pending.append(node)
            else:
                flush()
                # Tables cannot be inserted in arbitrary ADF containers safely.
                output.append(node)
        flush()
        return output
    result['content'] = blocks(result['content'])
    return result, count


def _inline_tokens(tokens):
    """Map Markdown inline tokens directly to ADF; never render intermediate HTML."""
    nodes, marks = [], []
    mapping = {'strong': 'strong', 'em': 'em', 's': 'strike'}
    for token in tokens or []:
        kind = token.type
        if kind.endswith('_open'):
            base = kind[:-5]
            if base == 'link':
                from urllib.parse import urlsplit
                href = token.attrGet('href') or ''
                if urlsplit(href).scheme.lower() in ('', 'http', 'https', 'mailto', 'tel'):
                    marks.append({'type': 'link', 'attrs': {'href': href}})
                else:
                    marks.append(None)
            elif base in mapping:
                marks.append({'type': mapping[base]})
        elif kind.endswith('_close'):
            if marks:
                marks.pop()
        elif kind in ('softbreak', 'hardbreak'):
            nodes.append({'type': 'hardBreak'})
        elif kind in ('text', 'code_inline', 'image'):
            text = token.content
            if kind == 'image':
                text = token.content or '[image]'
            if not text:
                continue
            node = {'type': 'text', 'text': text}
            current = [deepcopy(m) for m in marks if m]
            if kind == 'code_inline':
                current = [{'type': 'code'}]
            if current:
                # ADF permits one mark of each type.
                node['marks'] = list({m['type']: m for m in current}.values())
            nodes.append(node)
    return nodes


def _parse_markdown(text):
    """Parse Markdown/CommonMark, pipe tables and line-anchored Jira hN headings."""
    if not isinstance(text, str):
        raise ValueError('Markdown input must be a string')
    try:
        from markdown_it import MarkdownIt
    except ImportError as error:
        raise RuntimeError('Install converter dependencies: pip install -r requirements-adf.txt') from error
    # Jira heading adaptation is disabled inside literal fenced code blocks.
    lines, fence = [], None
    for line in text.split('\n'):
        marker = re.match(r'^\s*(`{3,}|~{3,})', line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        elif fence is None:
            line = re.sub(r'^h([1-6])\.\s+', lambda m: '#' * int(m[1]) + ' ', line)
        lines.append(line)
    tokens = MarkdownIt('js-default', {'html': False}).parse('\n'.join(lines))
    root = {'type': 'doc', 'version': 1, 'content': []}
    stack = [root]
    mapping = {'paragraph': 'paragraph', 'heading': 'heading', 'bullet_list': 'bulletList',
               'ordered_list': 'orderedList', 'list_item': 'listItem', 'blockquote': 'blockquote',
               'table': 'table', 'tr': 'tableRow', 'th': 'tableHeader', 'td': 'tableCell'}
    for token in tokens:
        if token.type in ('thead_open', 'thead_close', 'tbody_open', 'tbody_close'):
            continue
        if token.type.endswith('_open'):
            base = token.type[:-5]
            if base not in mapping:
                raise ValueError(f'Unsupported Markdown block: {token.type}')
            node = {'type': mapping[base], 'content': []}
            if base == 'heading':
                node['attrs'] = {'level': int(token.tag[1:])}
            elif base == 'ordered_list' and token.attrGet('start'):
                node['attrs'] = {'order': int(token.attrGet('start'))}
            if base == 'table' and stack[-1]['type'] != 'doc':
                raise ValueError('Markdown tables inside lists/quotes are unsupported; field was not changed')
            stack[-1]['content'].append(node)
            stack.append(node)
        elif token.type.endswith('_close'):
            node = stack.pop()
            if node['type'] in ('tableCell', 'tableHeader') and not node['content']:
                node['content'] = [{'type': 'paragraph', 'content': []}]
        elif token.type == 'inline':
            inline = _inline_tokens(token.children)
            if stack[-1]['type'] in ('tableCell', 'tableHeader'):
                stack[-1]['content'].append({'type': 'paragraph', 'content': inline})
            else:
                stack[-1]['content'].extend(inline)
        elif token.type in ('fence', 'code_block'):
            node = {'type': 'codeBlock', 'content': []}
            if token.content:
                node['content'] = [{'type': 'text', 'text': token.content}]
            if token.info.strip():
                node['attrs'] = {'language': token.info.split()[0]}
            stack[-1]['content'].append(node)
        elif token.type == 'hr':
            stack[-1]['content'].append({'type': 'rule'})
        else:
            raise ValueError(f'Unsupported Markdown token: {token.type}')
    if not root['content']:
        root['content'] = [{'type': 'paragraph', 'content': []}]
    return root


def convert_markdown_document(value):
    """Return (ADF, changed, table_count), preserving existing rich ADF blocks.

    Literal Markdown in unmarked top-level paragraphs is parsed. Existing code,
    tables, media, mentions and marked paragraphs are preserved. Pipe-table text
    is handled first so the Jira dialect and existing cell marks are retained.
    """
    if isinstance(value, str):
        source = {'type': 'doc', 'version': 1, 'content': [
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': value}] if value else []}]}
    else:
        source = value
    result, table_count = convert_pipe_tables(source)
    output = []
    original_tables = [block for block in source['content'] if block.get('type') == 'table']
    for node in result['content']:
        if node.get('type') == 'table' and node not in original_tables:
            from markdown_it import MarkdownIt
            parser = MarkdownIt('js-default', {'html': False})
            for row in node['content']:
                for cell in row['content']:
                    for paragraph in cell['content']:
                        inline = paragraph.get('content', [])
                        if inline and all(item.get('type') == 'text' and not item.get('marks') for item in inline):
                            text = ''.join(item['text'] for item in inline)
                            paragraph['content'] = _inline_tokens(parser.parseInline(text)[0].children)
        if node.get('type') != 'paragraph' or not node.get('content') or any(
            item.get('type') not in ('text', 'hardBreak') or item.get('marks')
            for item in node.get('content', [])
        ):
            output.append(node)
            continue
        text = ''.join('\n' if item['type'] == 'hardBreak' else item['text'] for item in node['content'])
        parsed = _parse_markdown(text)['content']
        # Plain text paragraphs are already valid ADF; preserve original layout/attrs.
        if not isinstance(value, str) and all(block['type'] == 'paragraph' and all(
            item.get('type') in ('text', 'hardBreak') and not item.get('marks')
            for item in block.get('content', [])) for block in parsed):
            output.append(node)
        else:
            output.extend(parsed)
            table_count += sum(block['type'] == 'table' for block in parsed)
    result['content'] = output
    return result, result != source or isinstance(value, str), table_count


def markdown_to_adf(text):
    """Convert Markdown text, including bare/wiki pipe tables, into an ADF doc."""
    if not isinstance(text, str):
        raise ValueError('Markdown input must be a string')
    return convert_markdown_document(text)[0]
