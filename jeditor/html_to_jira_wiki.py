#!/usr/bin/env python3
"""Convert an HTML file or standard input to Jira wiki text for copying."""
import argparse
from pathlib import Path
import re
import sys

from bs4 import BeautifulSoup, NavigableString


def html_to_jira_wiki(source):
    soup = BeautifulSoup(source, 'html.parser')
    warnings = []
    for element in soup.select('script, style, iframe, object'):
        element.decompose()

    def inline(node, in_cell=False):
        if isinstance(node, NavigableString):
            return re.sub(r'\s+', ' ', str(node).replace('\xa0', ' ')).replace('|', '&#124;') if in_cell else str(node).replace('\xa0', ' ')
        tag = node.name.lower()
        if tag == 'br':
            return '\\\\\n'
        if tag == 'img':
            src = node.get('src', '')
            if not src:
                warnings.append('이미지에 src가 없습니다.')
                return ''
            options = ','.join(f'{key}={node[key]}' for key in ('width', 'height') if node.get(key))
            return f'!{src}{"|" + options if options else ""}!'
        content = ''.join(inline(child, in_cell) for child in node.children)
        delimiters = {'b': '*', 'strong': '*', 'i': '_', 'em': '_', 'u': '+',
                      's': '-', 'strike': '-', 'del': '-', 'code': '{{'}
        if tag == 'a' and node.get('href'):
            return f'[{content or node["href"]}|{node["href"]}]'
        if tag in delimiters and content.strip():
            marker = delimiters[tag]
            content = marker + content + ('}}' if tag == 'code' else marker)
        style = node.get('style', '')
        match = re.search(r'(?:^|;)\s*color\s*:\s*([^;]+)', style, re.I)
        if match and content.strip():
            content = '{color:' + match.group(1).strip() + '}' + content + '{color}'
        if in_cell and tag in ('p', 'div', 'li', 'blockquote', 'tr'):
            content += '\\\\'
        return content

    def table_rows(table):
        if table.find('table'):
            warnings.append('중첩 표는 셀 안의 텍스트로 펼쳤습니다.')
        rows = [row for row in table.find_all('tr') if row.find_parent('table') is table]
        grid = [{} for _ in rows]
        def span(cell, key):
            value = cell.get(key, '1')
            return min(int(value), 100) if value.isdigit() and int(value) > 0 else 1
        for r, row in enumerate(rows):
            col = 0
            for cell in row.find_all(['td', 'th'], recursive=False):
                width, height = span(cell, 'colspan'), span(cell, 'rowspan')
                while any(col + i in grid[r] for i in range(width)):
                    col += 1
                if width > 1 or height > 1:
                    warnings.append('병합 셀은 내용을 왼쪽 위에 두고 나머지를 빈 셀로 펼쳤습니다. 병합 모양은 보존되지 않습니다.')
                text = inline(cell, True).strip()
                text = re.sub(r'(?:\\\\\s*)+$', '', text)
                text = text.replace('\\\\\\\\', '\\\\').replace('\n', ' ').replace('\r', ' ')
                for dr in range(min(height, len(rows) - r)):
                    for dc in range(width):
                        grid[r + dr][col + dc] = (text if dr == dc == 0 else '', cell.name == 'th')
                col += width
        width = max((max(row, default=-1) + 1 for row in grid), default=0)
        result = []
        for row in grid:
            separator = '||' if row and all(value[1] for value in row.values()) else '|'
            result.append(separator + separator.join(row.get(i, ('', False))[0] or ' ' for i in range(width)) + separator)
        return result

    def blocks(nodes, prefix=''):
        result = []
        for node in nodes:
            if isinstance(node, NavigableString):
                if str(node).strip():
                    result.append(str(node).strip())
                continue
            tag = node.name.lower()
            if tag in ('ul', 'ol'):
                marker = '*' if tag == 'ul' else '#'
                for item in node.find_all('li', recursive=False):
                    own = [c for c in item.children if getattr(c, 'name', None) not in ('ul', 'ol')]
                    result.append(prefix + marker + ' ' + ''.join(inline(c) for c in own).strip())
                    for nested in item.find_all(['ul', 'ol'], recursive=False):
                        result.extend(blocks([nested], prefix + marker))
            elif tag == 'table':
                result.extend(table_rows(node))
            elif re.fullmatch(r'h[1-6]', tag):
                result.append(f'{tag}. ' + ''.join(inline(c) for c in node.children).strip())
            elif tag == 'pre':
                result.append('{code}\n' + node.get_text().strip('\n') + '\n{code}')
            elif tag in ('p', 'blockquote'):
                content = ''.join(inline(c) for c in node.children).strip()
                if content:
                    result.append(('bq. ' if tag == 'blockquote' else '') + content)
            elif tag == 'hr':
                result.append('----')
            elif tag in ('html', 'body', 'div', 'section', 'article'):
                result.extend(blocks(node.children, prefix))
            else:
                content = inline(node).strip()
                if content:
                    result.append(content)
        return result

    parts = blocks(soup.body.children if soup.body else soup.children)
    def group(value):
        if value.startswith('|'):
            return 'table'
        if re.match(r'^[*#]+\s', value):
            return 'list'
        return None
    output = ''
    for index, part in enumerate(parts):
        if index:
            output += '\n' if group(part) and group(part) == group(parts[index - 1]) else '\n\n'
        output += part
    return output.strip(), list(dict.fromkeys(warnings))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', nargs='?', help='HTML file; omit to read standard input')
    parser.add_argument('-o', '--output', help='Write Jira wiki text to a UTF-8 file')
    args = parser.parse_args()
    source = Path(args.input).read_text(encoding='utf-8') if args.input else sys.stdin.read()
    result, warnings = html_to_jira_wiki(source)
    if args.output:
        Path(args.output).write_text(result + '\n', encoding='utf-8')
    else:
        print(result)
    for warning in warnings:
        print('경고: ' + warning, file=sys.stderr)


if __name__ == '__main__':
    main()
