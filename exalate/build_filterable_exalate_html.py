#!/usr/bin/env python3
"""Add an interactive project-key migration filter to a DBeaver HTML export."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


STYLE = r"""
<style id="exalate-migration-filter-style">
  :root {
    --ef-border: #cbd5e1;
    --ef-ink: #172033;
    --ef-muted: #64748b;
    --ef-blue: #2563eb;
    --ef-target: #dcfce7;
    --ef-target-border: #16a34a;
    --ef-nontarget: #fff7ed;
    --ef-noref: #f8fafc;
  }
  body { margin: 0; color: var(--ef-ink); background: #f8fafc; }
  #exalate-migration-filter {
    position: static;
    box-sizing: border-box;
    width: 100%;
    padding: 14px 18px;
    border-bottom: 1px solid var(--ef-border);
    background: #ffffff;
    box-shadow: 0 2px 8px rgba(15, 23, 42, .06);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }
  .ef-title { margin: 0 0 10px; font-size: 18px; }
  .ef-controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
  #ef-project-keys {
    box-sizing: border-box;
    min-width: 330px;
    flex: 1 1 420px;
    height: 38px;
    padding: 7px 10px;
    border: 1px solid #94a3b8;
    border-radius: 6px;
    font: 14px ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  #ef-mode, #ef-apply, #ef-clear {
    height: 38px;
    padding: 0 12px;
    border: 1px solid #94a3b8;
    border-radius: 6px;
    background: white;
    color: var(--ef-ink);
    font-weight: 600;
  }
  #ef-apply { border-color: var(--ef-blue); background: var(--ef-blue); color: white; cursor: pointer; }
  #ef-clear { cursor: pointer; }
  #ef-summary { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; font-size: 13px; }
  .ef-count { padding: 3px 8px; border-radius: 999px; background: #e2e8f0; }
  #ef-message { width: 100%; margin-top: 7px; color: var(--ef-muted); font-size: 12px; }
  #ef-message.ef-error { color: #b91c1c; font-weight: 600; }
  table { background: white; }
  tr.ef-target > td { background: var(--ef-target) !important; }
  tr.ef-unmatched > td { background: var(--ef-nontarget) !important; }
  tr.ef-no-reference > td { background: var(--ef-noref) !important; color: #475569; }
  td.ef-matched-cell { box-shadow: inset 4px 0 0 var(--ef-target-border); }
  .ef-row-result {
    display: block;
    margin: 0 0 5px;
    padding: 3px 6px;
    border-radius: 4px;
    background: rgba(255,255,255,.82);
    color: #166534;
    font: 700 11px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    text-align: left;
  }
  tr.ef-unmatched .ef-row-result { color: #9a3412; }
  tr.ef-no-reference .ef-row-result { color: #64748b; }
  .ef-hidden { display: none !important; }
  .ef-empty-row td {
    padding: 32px 16px;
    text-align: center;
    color: var(--ef-muted);
    font-size: 14px;
    font-weight: 600;
    background: #f8fafc !important;
  }
  .ef-check-label {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    user-select: none;
    padding: 0 10px;
    height: 38px;
    border: 1px solid #94a3b8;
    border-radius: 6px;
    background: white;
    box-sizing: border-box;
  }
  .ef-check-label input { margin: 0; cursor: pointer; }
  .ef-inc-badge {
    display: inline-block;
    margin: 0 0 6px;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 700;
    text-align: left;
  }
  .ef-inc-valid {
    background: #dcfce7;
    color: #166534;
    border: 1px solid #86efac;
  }
  .ef-inc-comment {
    background: #fff7ed;
    color: #c2410c;
    border: 1px solid #fdba74;
  }
  .ef-inc-empty {
    background: #f1f5f9;
    color: #64748b;
    border: 1px solid #cbd5e1;
  }
  .ef-kpi-active { background: #dbeafe !important; color: #1e40af !important; }
  .ef-kpi-incoming { background: #dcfce7 !important; color: #166534 !important; }
  .ef-kpi-zero { background: #fee2e2 !important; color: #991b1b !important; }
  @media (max-width: 720px) {
    #ef-project-keys { min-width: 100%; }
  }
</style>
"""


PANEL = r"""
<section id="exalate-migration-filter" aria-label="Exalate migration project filter">
  <h1 class="ef-title">Exalate 마이그레이션 프로젝트 필터 & Active 리포트</h1>
  <div class="ef-controls">
    <input id="ef-project-keys" type="text" autocomplete="off"
           placeholder="프로젝트 키를 쉼표로 입력: AA,BB,CC"
           aria-label="마이그레이션 대상 프로젝트 키">
    <label class="ef-check-label" title="ACTIVE 상태인 Connection만 표시합니다">
      <input type="checkbox" id="ef-active-only" checked> ACTIVE만 보기
    </label>
    <select id="ef-mode" aria-label="표시 조건">
      <option value="target" selected>마이그레이션 대상만</option>
      <option value="non-target">마이그레이션 비대상만</option>
      <option value="unmatched">project.key 참조 있으나 키 불일치</option>
      <option value="no-reference">project.key 참조 없음</option>
      <option value="all">전체 (색상 구분)</option>
    </select>
    <button id="ef-apply" type="button">필터 적용</button>
    <button id="ef-clear" type="button">초기화</button>
  </div>
  <div id="ef-summary" aria-live="polite"></div>
  <div id="ef-message">프로젝트 키를 입력하고 [필터 적용]을 누르면 해당 ACTIVE 마이그레이션 대상 및 INCOMING_PROCESSOR 상태가 분석됩니다.</div>
</section>
"""


SCRIPT = r"""
<script id="exalate-migration-filter-script">
(() => {
  'use strict';

  const table = document.querySelector('table');
  const input = document.getElementById('ef-project-keys');
  const activeOnly = document.getElementById('ef-active-only');
  const mode = document.getElementById('ef-mode');
  const applyButton = document.getElementById('ef-apply');
  const clearButton = document.getElementById('ef-clear');
  const summary = document.getElementById('ef-summary');
  const message = document.getElementById('ef-message');

  if (!table) {
    message.textContent = '조회 결과 테이블을 찾지 못했습니다.';
    message.classList.add('ef-error');
    return;
  }

  function analyzeIncoming(rawHtml) {
    if (!rawHtml) return { status: 'EMPTY', count: 0, lines: 0 };
    let text = rawHtml.replace(/<div[\s\S]*?<\/div>/gi, '').replace(/<br\s*\/?>/gi, '\n');
    const tempDiv = document.createElement('div');
    tempDiv.innerHTML = text;
    text = (tempDiv.textContent || tempDiv.innerText || '').trim();
    if (!text) return { status: 'EMPTY', count: 0, lines: 0 };

    const rawLines = text.split('\n');
    const codeLines = [];
    let inBlock = false;
    for (let rawLine of rawLines) {
      let line = rawLine.trim();
      if (inBlock) {
        const endIdx = line.indexOf('*/');
        if (endIdx !== -1) {
          inBlock = false;
          line = line.substring(endIdx + 2).trim();
        } else {
          continue;
        }
      }
      while (line.includes('/*')) {
        const startIdx = line.indexOf('/*');
        const endIdx = line.indexOf('*/', startIdx + 2);
        if (endIdx !== -1) {
          line = (line.substring(0, startIdx) + ' ' + line.substring(endIdx + 2)).trim();
        } else {
          line = line.substring(0, startIdx).trim();
          inBlock = true;
          break;
        }
      }
      if (line.startsWith('//')) continue;
      const commentIdx = line.indexOf('//');
      if (commentIdx !== -1) {
        const before = line.substring(0, commentIdx);
        const quoteCount = (before.match(/"/g) || []).length + (before.match(/'/g) || []).length;
        if (quoteCount % 2 === 0) {
          line = before.trim();
        }
      }
      if (line) codeLines.push(line);
    }

    if (codeLines.length > 0) {
      return { status: 'VALID', count: 1, lines: codeLines.length };
    }
    return { status: 'ALL_COMMENT', count: 0, lines: 0 };
  }

  const projectKeyReference = /\bproject(?:\s*\.\s*|[._]?)key\b/i;
  const rowModels = Array.from(table.querySelectorAll('tr'))
    .filter(row => row.querySelector('td'))
    .map(row => {
      const cells = Array.from(row.querySelectorAll(':scope > td'));
      const status = (cells[2]?.textContent || '').trim().toUpperCase();
      const instanceUrl = (cells[6]?.textContent || '').trim();
      const incomingRaw = cells[13]?.innerHTML || '';
      const incomingAnalysis = analyzeIncoming(incomingRaw);

      return {
        row,
        cells,
        status,
        instanceUrl,
        incomingAnalysis,
        texts: cells.map(cell => cell.textContent || '')
      };
    });

  // Attach incoming processor badges
  rowModels.forEach(model => {
    if (model.cells[13]) {
      const a = model.incomingAnalysis;
      const badge = document.createElement('div');
      if (a.status === 'VALID') {
        badge.className = 'ef-inc-badge ef-inc-valid';
        badge.textContent = `🟢 유효 코드 (${a.lines}행)`;
      } else if (a.status === 'ALL_COMMENT') {
        badge.className = 'ef-inc-badge ef-inc-comment';
        badge.textContent = '🟠 전체 주석 처리됨 (0건 카운트)';
      } else {
        badge.className = 'ef-inc-badge ef-inc-empty';
        badge.textContent = '⚪ 내용 없음 (0건)';
      }
      model.cells[13].prepend(badge);
    }
  });

  // Empty state row
  const tbody = table.querySelector('tbody') || table;
  const headerCols = table.querySelectorAll('th').length || 15;
  const emptyRow = document.createElement('tr');
  emptyRow.className = 'ef-empty-row ef-hidden';
  const emptyCell = document.createElement('td');
  emptyCell.colSpan = headerCols;
  emptyCell.textContent = '일치하는 마이그레이션 대상이 없습니다.';
  emptyRow.appendChild(emptyCell);
  tbody.appendChild(emptyRow);

  function parseKeys(value) {
    const raw = value.split(',').map(key => key.trim().toUpperCase()).filter(Boolean);
    const unique = [...new Set(raw)];
    const valid = unique.filter(key => /^[A-Z][A-Z0-9_]*$/.test(key));
    const invalid = unique.filter(key => !/^[A-Z][A-Z0-9_]*$/.test(key));
    return { valid, invalid };
  }

  function escapeRegExp(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  function containsExactKey(text, key) {
    const pattern = new RegExp(`(^|[^A-Z0-9_])${escapeRegExp(key)}(?=$|[^A-Z0-9_])`, 'i');
    return pattern.test(text);
  }

  function markerFor(model) {
    let marker = model.cells[0].querySelector('.ef-row-result');
    if (!marker) {
      marker = document.createElement('span');
      marker.className = 'ef-row-result';
      model.cells[0].prepend(marker);
    }
    return marker;
  }

  function clearMarkers() {
    rowModels.forEach(model => {
      model.row.classList.remove('ef-target', 'ef-unmatched', 'ef-no-reference', 'ef-hidden');
      model.cells.forEach(cell => cell.classList.remove('ef-matched-cell'));
      const marker = model.cells[0].querySelector('.ef-row-result');
      if (marker) marker.remove();
      model.state = null;
    });
    emptyRow.classList.add('ef-hidden');
  }

  function classify(model, keys) {
    model.row.classList.remove('ef-target', 'ef-unmatched', 'ef-no-reference', 'ef-hidden');
    model.cells.forEach(cell => cell.classList.remove('ef-matched-cell'));

    const referencedIndexes = [];
    model.texts.forEach((text, index) => {
      if (projectKeyReference.test(text)) referencedIndexes.push(index);
    });

    const matchedKeys = keys.filter(key => referencedIndexes.some(index => {
      const matched = containsExactKey(model.texts[index], key);
      if (matched) model.cells[index].classList.add('ef-matched-cell');
      return matched;
    }));

    let state;
    if (matchedKeys.length) {
      state = 'target';
      model.row.classList.add('ef-target');
      markerFor(model).textContent = `대상 · 일치: ${matchedKeys.join(', ')}`;
    } else if (referencedIndexes.length) {
      state = 'unmatched';
      model.row.classList.add('ef-unmatched');
      markerFor(model).textContent = '비대상 · project.key 참조는 있으나 입력 키 불일치';
    } else {
      state = 'no-reference';
      model.row.classList.add('ef-no-reference');
      markerFor(model).textContent = '비대상 · project.key 참조 없음';
    }
    model.state = state;
  }

  function visibleFor(state, selectedMode) {
    if (selectedMode === 'all') return true;
    if (selectedMode === 'non-target') return state !== 'target';
    return state === selectedMode;
  }

  function applyFilter(forceTargetMode = false) {
    const rawInput = input.value.trim();
    const activeFilter = activeOnly.checked;

    if (!rawInput) {
      clearMarkers();
      rowModels.forEach(model => {
        const isVisible = !activeFilter || model.status === 'ACTIVE';
        model.row.classList.toggle('ef-hidden', !isVisible);
      });
      const activeTotal = rowModels.filter(m => m.status === 'ACTIVE').length;
      const visibleCount = rowModels.filter(m => !m.row.classList.contains('ef-hidden')).length;

      summary.innerHTML = activeFilter
        ? `<span class="ef-count ef-kpi-active">전체 ${rowModels.length}개 중 ACTIVE ${visibleCount}개 표시 중</span>`
        : `<span class="ef-count">전체 ${rowModels.length}개 연결 표시 중</span>`;

      message.textContent = '프로젝트 키를 입력하고 [필터 적용]을 누르면 해당 ACTIVE 대상 및 INCOMING_PROCESSOR 분석 결과가 표시됩니다.';
      message.classList.remove('ef-error');
      emptyRow.classList.toggle('ef-hidden', visibleCount > 0);
      return;
    }

    const { valid: keys, invalid } = parseKeys(rawInput);
    if (!keys.length) {
      clearMarkers();
      rowModels.forEach(model => {
        const isVisible = !activeFilter || model.status === 'ACTIVE';
        model.row.classList.toggle('ef-hidden', !isVisible);
      });
      const visibleCount = rowModels.filter(m => !m.row.classList.contains('ef-hidden')).length;
      summary.innerHTML = `<span class="ef-count">현재 표시 ${visibleCount}/${rowModels.length}행</span>`;
      message.textContent = '유효한 프로젝트 키를 입력하세요. 예: AA,BB,CC';
      message.classList.add('ef-error');
      emptyRow.classList.toggle('ef-hidden', visibleCount > 0);
      return;
    }

    if (forceTargetMode && mode.value === 'all') {
      mode.value = 'target';
    }

    const selectedMode = mode.value;

    rowModels.forEach(model => classify(model, keys));
    rowModels.forEach(model => {
      const stateVisible = visibleFor(model.state, selectedMode);
      const activeVisible = !activeFilter || model.status === 'ACTIVE';
      model.row.classList.toggle('ef-hidden', !(stateVisible && activeVisible));
    });

    const targetRows = rowModels.filter(model => model.state === 'target');
    const activeTargetRows = targetRows.filter(model => model.status === 'ACTIVE');
    const activeTargetCount = activeTargetRows.length;

    // Incoming count: all-comment or empty counts as 0!
    const effectiveRows = activeFilter ? activeTargetRows : targetRows;
    const validIncomingCount = effectiveRows.filter(m => m.incomingAnalysis.count === 1).length;
    const zeroIncomingCount = effectiveRows.filter(m => m.incomingAnalysis.count === 0).length;
    const commentedIncomingCount = effectiveRows.filter(m => m.incomingAnalysis.status === 'ALL_COMMENT').length;
    const emptyIncomingCount = effectiveRows.filter(m => m.incomingAnalysis.status === 'EMPTY').length;

    const visibleCount = rowModels.filter(model => !model.row.classList.contains('ef-hidden')).length;
    emptyRow.classList.toggle('ef-hidden', visibleCount > 0);

    summary.innerHTML = [
      `<span class="ef-count">입력 키 ${keys.length}개</span>`,
      `<span class="ef-count ef-kpi-active">대상 ACTIVE ${activeTargetCount}건</span>`,
      `<span class="ef-count ef-kpi-incoming">유효 Incoming ${validIncomingCount}건</span>`,
      `<span class="ef-count ef-kpi-zero">주석/미작성 ${zeroIncomingCount}건 (0건 산정)</span>`,
      `<span class="ef-count">현재 표시 ${visibleCount}/${rowModels.length}행</span>`
    ].join('');

    if (invalid.length) {
      message.textContent = `무시된 형식: ${invalid.join(', ')} · 허용 형식 예: AA, BB2, CC_TEST`;
      message.classList.add('ef-error');
    } else if (visibleCount === 0) {
      message.innerHTML = `검색 키: <strong>${keys.join(', ')}</strong> · 표시 조건(ACTIVE 포함)에 일치하는 행이 없습니다.`;
      message.classList.add('ef-error');
    } else {
      message.innerHTML = `검색 키: <strong>${keys.join(', ')}</strong> · 대상 ACTIVE <strong>${activeTargetCount}건</strong> 중 유효 INCOMING_PROCESSOR <strong>${validIncomingCount}건</strong> (전체 주석 ${commentedIncomingCount}건/미작성 ${emptyIncomingCount}건은 <strong>0건</strong>으로 산정)`;
      message.classList.remove('ef-error');
    }
  }

  input.addEventListener('keydown', event => {
    if (event.key === 'Enter') applyFilter(true);
  });
  activeOnly.addEventListener('change', () => applyFilter(false));
  mode.addEventListener('change', () => applyFilter(false));
  applyButton.addEventListener('click', () => applyFilter(true));
  clearButton.addEventListener('click', () => {
    input.value = '';
    activeOnly.checked = true;
    mode.value = 'target';
    applyFilter(false);
    input.focus();
  });

  mode.value = 'target';
  applyFilter(false);
  input.focus();
})();
</script>
"""


def transform(source: Path, destination: Path) -> None:
    document = source.read_text(encoding="utf-8")
    if 'id="exalate-migration-filter"' in document:
        raise ValueError("The input HTML already contains the migration filter")
    if not re.search(r"</head\s*>", document, flags=re.IGNORECASE):
        raise ValueError("The input HTML has no closing </head> tag")
    if not re.search(r"<body\b[^>]*>", document, flags=re.IGNORECASE):
        raise ValueError("The input HTML has no <body> tag")
    if not re.search(r"</body\s*>", document, flags=re.IGNORECASE):
        raise ValueError("The input HTML has no closing </body> tag")
    if not re.search(r"<table\b", document, flags=re.IGNORECASE):
        raise ValueError("The input HTML has no table")

    if not re.search(r"<title\b", document, flags=re.IGNORECASE):
        document = re.sub(
            r"</head\s*>",
            lambda _: "<title>Exalate migration project filter</title>\n</head>",
            document,
            count=1,
            flags=re.IGNORECASE,
        )
    document = re.sub(
        r"</head\s*>",
        lambda _: STYLE + "\n</head>",
        document,
        count=1,
        flags=re.IGNORECASE,
    )
    document = re.sub(
        r"(<body\b[^>]*>)",
        lambda match: match.group(1) + "\n" + PANEL,
        document,
        count=1,
        flags=re.IGNORECASE,
    )
    document = re.sub(
        r"</body\s*>",
        lambda _: SCRIPT + "\n</body>",
        document,
        count=1,
        flags=re.IGNORECASE,
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add a comma-separated project.key filter to a DBeaver HTML export."
    )
    parser.add_argument("input", type=Path, help="Source DBeaver HTML export")
    parser.add_argument("output", type=Path, nargs="?", help="Generated interactive HTML")
    args = parser.parse_args()

    output = args.output or args.input.with_name(f"{args.input.stem}-filterable.html")
    transform(args.input.expanduser().resolve(), output.expanduser().resolve())
    print(output.expanduser().resolve())


if __name__ == "__main__":
    main()
