# -*- coding: utf-8 -*-
import re
import csv
import json
from collections import Counter

raw_path = '/Users/sean/Downloads/1872건 2. 엑셀 복사용 탭 구분 목록 (이슈번호, 파일명,.txt'
csv_output = 'jcma_unmigrated_attachments_1872.csv'
json_output = 'unmigrated_1872_data.json'

with open(raw_path, 'r', encoding='utf-8') as f:
    text = f.read()

prefix_match = re.search(r'작성자\s+', text)
data_text = text[prefix_match.end():] if prefix_match else text

pattern = re.compile(
    r'(?P<project>[A-Z0-9_]+)\s+'
    r'(?P<issue_key>[A-Z0-9_]+-\d+)\s+'
    r'(?P<attachment_id>\d+)\s+'
    r'(?P<filename_rest>.*?)'
    r'(?=(?:[A-Z0-9_]+\s+[A-Z0-9_]+-\d+\s+\d+)|\Z)',
    re.DOTALL
)

tail_pattern = re.compile(r'^(?P<filename>.*?)\s+(?P<filesize>\d+)\s+(?P<created>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?)\s+(?P<author>\S+)\s*$')

items = []
project_counts = Counter()
project_sizes = Counter()
ext_counts = Counter()

for idx, m in enumerate(pattern.finditer(data_text)):
    proj = m.group('project')
    ikey = m.group('issue_key')
    att_id = m.group('attachment_id')
    rest = m.group('filename_rest').strip()
    
    tm = tail_pattern.match(rest)
    if not tm:
        print(f"Error parsing record {idx}: {rest}")
        continue
        
    fname = tm.group('filename')
    fsize = int(tm.group('filesize'))
    created = tm.group('created')
    author = tm.group('author')
    ext = fname.split('.')[-1].lower() if '.' in fname else 'no_ext'
    
    project_counts[proj] += 1
    project_sizes[proj] += fsize
    ext_counts[ext] += 1
    
    items.append({
        'index': idx + 1,
        'project': proj,
        'issue_key': ikey,
        'attachment_id': att_id,
        'file_name': fname,
        'file_size_bytes': fsize,
        'file_size_mb': round(fsize / (1024 * 1024), 2),
        'created_date': created,
        'author': author,
        'extension': ext
    })

print(f"Total parsed records: {len(items)}")

# 1. Export CSV
with open(csv_output, 'w', encoding='utf-8-sig', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['번호', '프로젝트', '이슈번호', '첨부파일ID', '파일명', '확장자', '파일크기(Bytes)', '파일크기(MB)', '등록일시', '작성자'])
    for it in items:
        writer.writerow([
            it['index'],
            it['project'],
            it['issue_key'],
            it['attachment_id'],
            it['file_name'],
            it['extension'],
            it['file_size_bytes'],
            it['file_size_mb'],
            it['created_date'],
            it['author']
        ])

print(f"Saved CSV: {csv_output}")

# 2. Export JSON
summary = {
    'total_count': len(items),
    'total_bytes': sum(it['file_size_bytes'] for it in items),
    'total_mb': round(sum(it['file_size_bytes'] for it in items) / (1024 * 1024), 2),
    'total_gb': round(sum(it['file_size_bytes'] for it in items) / (1024 * 1024 * 1024), 2),
    'project_counts': dict(project_counts),
    'project_sizes_mb': {k: round(v / (1024 * 1024), 2) for k, v in project_sizes.items()},
    'top_extensions': dict(ext_counts.most_common(15)),
    'items': items
}

with open(json_output, 'w', encoding='utf-8') as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)

print(f"Saved JSON: {json_output}")
