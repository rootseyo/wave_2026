"""Convert exported Jira wiki/Markdown and real HTML content to ADF."""
from copy import deepcopy
import html
import re
from urllib.parse import urlsplit

from markdown_adf import convert_markdown_document, convert_pipe_tables, markdown_to_adf, _slice


def safe_url(value):
    return bool(value) and not re.search(r'[\x00-\x20\x7f]', value) and urlsplit(value).scheme.lower() in ('', 'http', 'https', 'mailto', 'tel')


def color(value):
    value = value.strip().lower().replace('!important', '').strip()
    names = {'white': '#ffffff', 'black': '#000000', 'red': '#ff0000', 'yellow': '#ffff00',
             'blue': '#0000ff', 'green': '#008000', 'gray': '#808080', 'grey': '#808080'}
    if value in names:
        return names[value]
    if re.fullmatch(r'#[0-9a-f]{3}', value):
        return '#' + ''.join(c * 2 for c in value[1:])
    if re.fullmatch(r'#[0-9a-f]{6}', value):
        return value
    match = re.fullmatch(r'rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', value)
    if match and all(0 <= int(x) <= 255 for x in match.groups()):
        return '#' + ''.join(f'{int(x):02x}' for x in match.groups())
    return None


def wiki_markdown(text):
    text = text.replace('{*}', '*')
    # Jira wiki bold differs from CommonMark emphasis. Leave list bullets alone.
    text = re.sub(r'(?<!\*)\*(?![\s*])([^*\n]+?)(?<!\s)\*(?!\*)', r'**\1**', text)
    text = re.sub(r'\[([^\]\n|]+)\|(https?://[^\]\n]+)\]', r'[\1](\2)', text)
    text = re.sub(r'\[(https?://[^\]\n]+)\]', r'[\1](\1)', text)
    text = re.sub(r'(?m)^\s*#\s+', '1. ', text)
    return text


def source_to_adf(content, attachment, warnings=None):
    """attachment(ref, image=bool, width=..., height=...) returns a block node."""
    from bs4 import BeautifulSoup, NavigableString, Comment, Doctype
    warnings = warnings if warnings is not None else []
    if not isinstance(content, str) or not content.strip():
        raise ValueError('Source content is empty')
    # Jira template exports sometimes contain closing </br> tags and omit </p>
    # before the next paragraph. Normalize these before BeautifulSoup builds a
    # nested tree that can change the resulting ADF block order.
    content = re.sub(r'</br\s*>', '<br/>', content, flags=re.I)
    paragraph_tags = re.compile(r'<\s*(/?)\s*p\b[^>]*>', re.I)
    normalized, cursor, paragraph_open = [], 0, False
    for match in paragraph_tags.finditer(content):
        normalized.append(content[cursor:match.start()])
        if match.group(1):
            if paragraph_open:
                normalized.append('</p>')
                paragraph_open = False
        else:
            if paragraph_open:
                normalized.append('</p>')
            normalized.append(match.group(0))
            paragraph_open = True
        cursor = match.end()
    normalized.append(content[cursor:])
    if paragraph_open:
        normalized.append('</p>')
    content = ''.join(normalized)
    content = re.sub(r'\{code(?::([^}\n]+))?\}(.*?)\{code\}',
        lambda m: '<pre data-language="' + html.escape(m[1] or '', quote=True) + '"><code>' + html.escape(m[2].strip('\n')) + '</code></pre>',
        content, flags=re.S)
    soup = BeautifulSoup(content, 'html.parser')
    for tag in list(soup.find_all(['script', 'style', 'head', 'iframe', 'object'])):
        if tag.parent is not None:
            tag.decompose()
    for tag in list(soup.find_all()):
        if tag.attrs is not None and (re.search(r'display\s*:\s*none', tag.get('style', ''), re.I) or 'jeditor-template-watermark' in tag.get('class', [])):
            tag.decompose()
    for node in list(soup.find_all(string=lambda value: isinstance(value, (Comment, Doctype)))):
        node.extract()
    # Split wiki images/files without interpreting code contents as attachments.
    pattern = re.compile(r'!\[[^\]\n]*\]\((?P<markdown_url>https?://[^)\s]+)\)|!(?P<wiki>[^!\n]+)!|\[\^(?P<file>[^\]\n]+)\]')
    for text_node in list(soup.find_all(string=True)):
        if text_node.find_parent(['pre', 'code']):
            continue
        value = str(text_node)
        matches = []
        for match in pattern.finditer(value):
            wiki = match.group('wiki')
            if wiki is not None:
                reference = wiki.partition('|')[0].strip()
                if not (reference.startswith(('https://', 'http://', '/')) or re.search(r'\.[A-Za-z0-9]{1,8}$', reference)):
                    continue  # Punctuation such as 'Hello! How are you!' is not an image.
            matches.append(match)
        if not matches:
            continue
        offset = 0
        for match in matches:
            if match.start() > offset:
                text_node.insert_before(NavigableString(value[offset:match.start()]))
            tag = soup.new_tag('migration-file' if match.group('file') is not None else 'migration-image')
            if match.group('wiki') is not None or match.group('markdown_url') is not None:
                reference, _, options = (match.group('wiki') or match.group('markdown_url')).partition('|')
                tag['src'] = reference.strip()
                for key in ('width', 'height'):
                    dimension = re.search(r'(?:^|[,\s])' + key + r'=(\d+)', options)
                    if dimension:
                        tag[key] = dimension[1]
            else:
                tag['src'] = match.group('file')
            text_node.insert_before(tag)
            offset = match.end()
        if offset < len(value):
            text_node.insert_before(NavigableString(value[offset:]))
        text_node.extract()

    def element_marks(element, marks=None):
        marks = list(marks or [])
        mark_map = {'b': 'strong', 'strong': 'strong', 'i': 'em', 'em': 'em', 'u': 'underline', 's': 'strike', 'del': 'strike', 'code': 'code'}
        if element.name in mark_map:
            marks.append({'type': mark_map[element.name]})
        if element.name == 'a':
            href = html.unescape(element.get('href', '')).strip()
            if safe_url(href):
                marks.append({'type': 'link', 'attrs': {'href': href}})
        for declaration in element.get('style', '').split(';'):
            key, _, value = declaration.partition(':')
            if key.strip().lower() == 'color' and color(value):
                marks.append({'type': 'textColor', 'attrs': {'color': color(value)}})
        return marks

    def inline(element, marks=None):
        marks = list(marks or [])
        if isinstance(element, NavigableString):
            value = str(element)
            if not value:
                return []
            node = {'type': 'text', 'text': value}
            if marks:
                node['marks'] = [{'type': 'code'}] if any(m['type'] == 'code' for m in marks) else list({m['type']: m for m in marks}.values())
            return [node]
        if element.name == 'br':
            return [{'type': 'hardBreak'}]
        marks = element_marks(element, marks)
        output = []
        for child in element.children:
            output.extend(inline(child, marks))
        return output

    def text_blocks(nodes):
        if not nodes:
            return []
        if any(n.get('marks') for n in nodes):
            # `flush()` clears its pending list after this return, so detach the
            # paragraph content before storing it in the output tree.
            return [{'type': 'paragraph', 'content': list(nodes)}]
        text = ''.join('\n' if n['type'] == 'hardBreak' else n['text'] for n in nodes)
        # nbsp is layout whitespace in export rows and empty wiki cells.
        text = text.replace('\xa0', ' ')
        text = re.sub(r'(?m)^[\u200b\u200c\ufeff]+', '', text)
        if not text.strip():
            return []
        # Jira templates commonly use a lone dash as an empty-field prompt;
        # CommonMark would otherwise turn it into an empty bullet list item.
        if re.fullmatch(r'\s*-\s*', text):
            return [{'type': 'paragraph', 'content': [{'type': 'text', 'text': '-'}]}]
        source = {'type': 'doc', 'version': 1, 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': wiki_markdown(text)}]}]}
        tables, _ = convert_pipe_tables(source, allow_single_column=True)
        converted, _, _ = convert_markdown_document(tables)
        output = []
        for block in converted['content']:
            if block['type'] == 'paragraph' and all(item.get('type') == 'text' and not item.get('marks') for item in block.get('content', [])):
                plain = ''.join(item['text'] for item in block['content'])
                output.extend(markdown_to_adf(plain)['content'])
            else:
                output.append(block)
        return output

    def blocks(elements, marks=None):
        output, pending = [], []
        def flush():
            if pending:
                output.extend(text_blocks(pending))
                pending.clear()
        for element in elements:
            if isinstance(element, NavigableString):
                pending.extend(inline(element, marks))
                continue
            tag = element.name
            contains_block = element.find(['img', 'migration-image', 'migration-file', 'table', 'p', 'div', 'ul', 'ol', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
            if tag == 'br':
                pending.append({'type': 'hardBreak'})
            elif tag in ('img', 'migration-image', 'migration-file'):
                flush()
                reference = element.get('src', '')
                node = attachment(reference, image=tag != 'migration-file',
                                  width=element.get('width'), height=element.get('height'))
                output.append(node)
            elif tag == 'a' and ('jeditor_file_provider' in element.get('href', '') or '/secure/attachment/' in element.get('href', '')):
                flush()
                output.append(attachment(element['href'], image=False))
            elif tag == 'table':
                flush()
                if element.find(['td', 'th']) is None:
                    # Exported templates may end with an empty colgroup-only table.
                    warnings.append('HTML table without cells omitted; any text preserved')
                    output.extend(blocks(list(element.children), marks))
                    continue
                if element.find('table') is not None:
                    # Unwrap layout tables so leaf tables remain editable ADF tables.
                    # Keep outer-cell labels and descriptions in reading order.
                    warnings.append('Outer HTML table unwrapped; innermost tables preserved (outer layout removed)')
                    caption = element.find('caption', recursive=False)
                    if caption is not None:
                        output.extend(blocks(list(caption.children), marks))
                    for row in element.find_all('tr'):
                        if row.find_parent('table') is not element:
                            continue
                        for cell in row.find_all(['td', 'th'], recursive=False):
                            cell_marks = element_marks(cell, marks)
                            # Layout labels such as "1. 사양 현황" are literal text,
                            # not Markdown ordered lists. Join their HTML paragraphs.
                            if (cell.find(['table', 'ul', 'ol', 'pre', 'img', 'migration-image', 'migration-file']) is None
                                    and re.match(r'^\s*\d+\.\s', cell.get_text(' ', strip=True))):
                                label_nodes = []
                                for child in cell.children:
                                    if isinstance(child, NavigableString) and not str(child).strip():
                                        continue
                                    if label_nodes:
                                        label_nodes.append({'type': 'text', 'text': ' '})
                                    label_nodes.extend(inline(child, cell_marks))
                                output.append({'type': 'paragraph', 'content': label_nodes})
                            else:
                                output.extend(blocks(list(cell.children), cell_marks))
                else:
                    output.append(table(element))
            elif tag in ('ul', 'ol'):
                flush()
                items = []
                def emit_items():
                    if items:
                        node = {'type': 'bulletList' if tag == 'ul' else 'orderedList', 'content': list(items)}
                        if tag == 'ol' and str(element.get('start', '')).isdigit():
                            node['attrs'] = {'order': max(1, int(element['start']))}
                        output.append(node)
                        items.clear()
                for li in element.find_all('li', recursive=False):
                    segment = []
                    for child in blocks(list(li.children)):
                        if child['type'] == 'table':
                            if segment:
                                if segment[0]['type'] != 'paragraph':
                                    segment.insert(0, {'type': 'paragraph', 'content': []})
                                items.append({'type': 'listItem', 'content': segment})
                                segment = []
                            emit_items()
                            output.append(child)
                            warnings.append('HTML list table lifted to document level')
                        else:
                            segment.append(child)
                    if segment:
                        if segment[0]['type'] != 'paragraph':
                            segment.insert(0, {'type': 'paragraph', 'content': []})
                        items.append({'type': 'listItem', 'content': segment})
                emit_items()
            elif tag == 'pre':
                flush()
                text = element.get_text()
                node = {'type': 'codeBlock', 'content': [{'type': 'text', 'text': text}] if text else []}
                language = element.get('data-language')
                if language:
                    node['attrs'] = {'language': language}
                output.append(node)
            elif tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
                flush()
                nodes = []
                for child in element.children:
                    nodes.extend(inline(child, marks))
                output.append({'type': 'heading', 'attrs': {'level': int(tag[1])}, 'content': nodes})
            elif tag in ('b', 'strong', 'i', 'em', 'u', 's', 'del', 'code', 'a', 'span'):
                if contains_block:
                    flush()
                    output.extend(blocks(list(element.children), element_marks(element, marks)))
                else:
                    pending.extend(inline(element, marks))
            elif tag in ('html', 'body', 'p', 'div', 'section', 'article', 'li', 'blockquote') or contains_block:
                flush()
                output.extend(blocks(list(element.children), marks))
            elif tag == 'hr':
                flush()
                output.append({'type': 'rule'})
            else:
                pending.extend(inline(element, marks))
        flush()
        return output

    def table(element):
        rows = []
        for row in element.find_all('tr'):
            if row.find_parent('table') is not element:
                continue
            cells = []
            for cell in row.find_all(['td', 'th'], recursive=False):
                content = blocks(list(cell.children))
                node = {'type': 'tableHeader' if cell.name == 'th' else 'tableCell',
                        'content': content or [{'type': 'paragraph', 'content': []}]}
                attrs = {}
                for key in ('colspan', 'rowspan'):
                    value = str(cell.get(key, ''))
                    if value.isdigit() and int(value) > 0:
                        attrs[key] = int(value)
                for declaration in cell.get('style', '').split(';'):
                    key, _, value = declaration.partition(':')
                    if key.strip().lower() == 'background-color' and color(value):
                        attrs['background'] = color(value)
                if attrs:
                    node['attrs'] = attrs
                cells.append(node)
            if cells:
                rows.append({'type': 'tableRow', 'content': cells})
        if not rows:
            raise ValueError('Source HTML table has no cells')
        return {'type': 'table', 'content': rows}

    try:
        document = {'type': 'doc', 'version': 1, 'content': blocks(list(soup.children))}
    finally:
        soup.decompose()
    def wiki_colors(nodes):
        output, active_color = [], None
        for node in nodes:
            if node.get('type') != 'text' or any(m['type'] == 'code' for m in node.get('marks', [])):
                output.append(node)
                continue
            def emit(text):
                if text:
                    part = deepcopy(node)
                    part['text'] = text
                    if active_color:
                        part['marks'] = [m for m in part.get('marks', []) if m['type'] != 'textColor'] + [
                            {'type': 'textColor', 'attrs': {'color': active_color}}]
                    output.append(part)
            cursor = 0
            for match in re.finditer(r'\{color(?::([^}\n]+))?\}', node['text']):
                emit(node['text'][cursor:match.start()])
                selected = color(match[1]) if match[1] else None
                if match[1] and selected:
                    active_color = selected
                elif not match[1] and active_color:
                    active_color = None
                else:
                    emit(match[0])
                cursor = match.end()
            emit(node['text'][cursor:])
        return output

    def normalize(node):
        if node.get('type') in ('paragraph', 'heading'):
            node['content'] = wiki_colors(node.get('content', []))
            # Jira wiki +underline+ may wrap text that already has a strong mark.
            content = node.get('content', [])
            if content and all(item.get('type') == 'text' for item in content):
                text = ''.join(item['text'] for item in content)
                matches = list(re.finditer(r'(?<![\w+])\+(?=\S)([^\n]+?)(?<=\S)\+(?![\w+])', text))
                if matches:
                    marked, offset = [], 0
                    for match in matches:
                        marked.extend(_slice(content, offset, match.start()))
                        inner = _slice(content, match.start() + 1, match.end() - 1)
                        for item in inner:
                            item['marks'] = [m for m in item.get('marks', []) if m['type'] != 'underline'] + [{'type': 'underline'}]
                        marked.extend(inner)
                        offset = match.end()
                    marked.extend(_slice(content, offset, len(text)))
                    node['content'] = marked
            fixed = []
            for item in node.get('content', []):
                if item.get('type') == 'text' and '\n' in item['text']:
                    for index, part in enumerate(item['text'].split('\n')):
                        if index:
                            fixed.append({'type': 'hardBreak'})
                        if part:
                            segment = deepcopy(item)
                            segment['text'] = part
                            fixed.append(segment)
                else:
                    fixed.append(item)
            node['content'] = fixed
        for child in node.get('content', []):
            normalize(child)
    normalize(document)
    if not document['content']:

        document['content'] = [{'type': 'paragraph', 'content': []}]
    return document
