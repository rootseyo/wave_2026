#!/usr/bin/env python3
"""
Enterprise B2B Web-based .env Manager
- Python standard library only (Zero external dependencies)
- B2B Enterprise UI: Dense grid (1/2/3 columns), high information density
- Sticky / Floating Top Bar with instant Save, Search, View Controls, and Counters
- Multi-value candidate management (Radio toggle: 1 active, rest commented)
- Keyboard shortcut: Cmd+S / Ctrl+S to Save
- Section categorization & quick filtering
- Automatic backup before saving (.bak)
"""

import json
import os
import shutil
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler

ENV_PATH = os.environ.get("ENV_FILE_PATH", ".env")
if not os.path.exists(ENV_PATH) and os.path.exists("env"):
    ENV_PATH = "env"

HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", 8080))


def parse_env_file(file_path):
    if not os.path.exists(file_path):
        return []

    raw_items = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            raw_line = line.rstrip("\r\n")
            stripped = raw_line.strip()
            if not stripped:
                raw_items.append({"type": "empty"})
            elif stripped.startswith("#"):
                content = stripped[1:].strip()
                if "=" in content:
                    parts = content.split("=", 1)
                    k, v = parts[0].strip(), parts[1]
                    if k and " " not in k:
                        raw_items.append({"type": "candidate_var", "key": k, "val": v, "active": False})
                        continue
                raw_items.append({"type": "comment", "value": raw_line})
            elif "=" in raw_line:
                parts = raw_line.split("=", 1)
                k, v = parts[0].strip(), parts[1]
                raw_items.append({"type": "candidate_var", "key": k, "val": v, "active": True})
            else:
                raw_items.append({"type": "comment", "value": raw_line})

    grouped_items = []
    i = 0
    while i < len(raw_items):
        item = raw_items[i]
        if item["type"] == "candidate_var":
            curr_key = item["key"]
            candidates = [{"val": item["val"], "active": item["active"]}]
            j = i + 1
            while j < len(raw_items):
                next_item = raw_items[j]
                if next_item["type"] == "candidate_var" and next_item["key"] == curr_key:
                    candidates.append({"val": next_item["val"], "active": next_item["active"]})
                    j += 1
                else:
                    break

            active_idx = 0
            found_active = False
            for idx, c in enumerate(candidates):
                if c["active"]:
                    active_idx = idx
                    found_active = True
                    break
            if not found_active and candidates:
                candidates[0]["active"] = True

            grouped_items.append({
                "type": "var_group",
                "key": curr_key,
                "candidates": candidates,
                "active_idx": active_idx
            })
            i = j
        else:
            grouped_items.append(item)
            i += 1

    return grouped_items


def write_env_file(file_path, items):
    if os.path.exists(file_path):
        shutil.copy(file_path, f"{file_path}.bak")

    with open(file_path, "w", encoding="utf-8") as f:
        for item in items:
            t = item.get("type")
            if t == "empty":
                f.write("\n")
            elif t == "comment":
                f.write(f"{item.get('value')}\n")
            elif t == "var_group":
                key = item.get("key", "").strip()
                if not key:
                    continue
                candidates = item.get("candidates", [])
                active_idx = item.get("active_idx", 0)

                for idx, c in enumerate(candidates):
                    val = c.get("val", "")
                    if idx == active_idx:
                        f.write(f"{key}={val}\n")
                    else:
                        f.write(f"# {key}={val}\n")


HTML_PAGE = """<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Config Center | .env Manager</title>
    <style>
        :root {
            --slate-900: #0f172a;
            --slate-800: #1e293b;
            --slate-700: #334155;
            --slate-600: #475569;
            --slate-500: #64748b;
            --slate-400: #94a3b8;
            --slate-200: #e2e8f0;
            --slate-100: #f1f5f9;
            --slate-50:  #f8fafc;
            
            --b2b-blue: #0284c7;
            --b2b-blue-hover: #0369a1;
            --b2b-active-border: #0ea5e9;
            --b2b-emerald: #059669;
            --b2b-emerald-bg: #ecfdf5;
            --b2b-emerald-border: #10b981;
            --b2b-crimson: #e11d48;
            --b2b-crimson-bg: #ffe4e6;
            
            --mono-font: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
            --sans-font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        }

        * { box-sizing: border-box; }
        body {
            font-family: var(--sans-font);
            background-color: #f1f5f9;
            color: var(--slate-800);
            margin: 0;
            padding: 0;
            font-size: 13px;
            line-height: 1.4;
        }

        /* Top Sticky Floating Control Bar */
        .top-navbar {
            position: sticky;
            top: 0;
            z-index: 1000;
            background: #ffffff;
            border-bottom: 1px solid var(--slate-200);
            box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08);
            padding: 10px 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
        }

        .brand-section {
            display: flex;
            align-items: center;
            gap: 12px;
            min-width: 260px;
        }
        .brand-badge {
            background: var(--slate-900);
            color: #ffffff;
            font-weight: 700;
            font-size: 11px;
            letter-spacing: 0.08em;
            padding: 4px 8px;
            border-radius: 4px;
            text-transform: uppercase;
        }
        .file-path {
            font-family: var(--mono-font);
            font-size: 12px;
            color: var(--slate-600);
            background: var(--slate-100);
            padding: 3px 8px;
            border-radius: 4px;
            border: 1px solid var(--slate-200);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 280px;
        }

        .center-controls {
            display: flex;
            align-items: center;
            gap: 12px;
            flex: 1;
            max-width: 600px;
        }
        .search-wrap {
            position: relative;
            width: 100%;
        }
        .search-input {
            width: 100%;
            padding: 7px 12px 7px 32px;
            font-size: 12px;
            border: 1px solid var(--slate-200);
            border-radius: 6px;
            background: var(--slate-50);
            outline: none;
            transition: all 0.15s ease;
        }
        .search-input:focus {
            background: #ffffff;
            border-color: var(--b2b-blue);
            box-shadow: 0 0 0 3px rgba(2, 132, 199, 0.15);
        }
        .search-icon {
            position: absolute;
            left: 10px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--slate-400);
            font-size: 13px;
        }

        .view-toggle {
            display: flex;
            background: var(--slate-100);
            border: 1px solid var(--slate-200);
            border-radius: 6px;
            padding: 2px;
            gap: 2px;
        }
        .view-btn {
            border: none;
            background: transparent;
            font-size: 11px;
            font-weight: 600;
            padding: 4px 10px;
            border-radius: 4px;
            color: var(--slate-600);
            cursor: pointer;
            transition: all 0.1s ease;
        }
        .view-btn.active {
            background: #ffffff;
            color: var(--b2b-blue);
            box-shadow: 0 1px 3px rgba(0,0,0,0.06);
        }

        .right-actions {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .stat-pill {
            font-size: 12px;
            font-weight: 600;
            color: var(--slate-600);
            background: var(--slate-100);
            padding: 5px 10px;
            border-radius: 6px;
            white-space: nowrap;
        }
        .stat-pill strong {
            color: var(--b2b-blue);
        }

        /* Action Buttons */
        .btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            font-weight: 600;
            font-size: 12px;
            padding: 7px 14px;
            border-radius: 6px;
            border: 1px solid transparent;
            cursor: pointer;
            transition: all 0.15s ease;
            white-space: nowrap;
        }
        .btn-save {
            background: var(--b2b-emerald);
            color: #ffffff;
            box-shadow: 0 1px 3px rgba(5, 150, 105, 0.3);
        }
        .btn-save:hover {
            background: #047857;
            box-shadow: 0 2px 6px rgba(5, 150, 105, 0.4);
        }
        .btn-sub {
            background: #ffffff;
            border-color: var(--slate-200);
            color: var(--slate-700);
        }
        .btn-sub:hover {
            background: var(--slate-50);
            border-color: var(--slate-400);
        }

        /* Success Toast */
        .toast-banner {
            background: #ecfdf5;
            border-bottom: 1px solid #a7f3d0;
            color: #065f46;
            padding: 8px 24px;
            font-size: 12px;
            font-weight: 500;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        /* Main Workspace Container */
        .workspace {
            padding: 18px 24px 60px 24px;
        }

        /* Section Dividers */
        .section-header {
            background: var(--slate-800);
            color: #f8fafc;
            font-family: var(--mono-font);
            font-size: 12px;
            font-weight: 600;
            padding: 6px 14px;
            border-radius: 6px;
            margin: 20px 0 10px 0;
            display: flex;
            align-items: center;
            justify-content: space-between;
            letter-spacing: 0.02em;
        }
        .section-header:first-of-type {
            margin-top: 0;
        }
        .section-del-btn {
            background: transparent;
            border: none;
            color: var(--slate-400);
            font-size: 13px;
            cursor: pointer;
            padding: 2px 6px;
            border-radius: 4px;
        }
        .section-del-btn:hover {
            background: rgba(255,255,255,0.1);
            color: #fca5a5;
        }

        /* Grid System (Dense B2B layout) */
        .grid-container {
            display: grid;
            gap: 10px;
            transition: all 0.2s ease;
        }
        .grid-col-1 { grid-template-columns: 1fr; }
        .grid-col-2 { grid-template-columns: repeat(2, 1fr); }
        .grid-col-3 { grid-template-columns: repeat(3, 1fr); }

        @media (max-width: 1400px) {
            .grid-col-3 { grid-template-columns: repeat(2, 1fr); }
        }
        @media (max-width: 900px) {
            .grid-col-2, .grid-col-3 { grid-template-columns: 1fr; }
        }

        /* Dense Card Component */
        .card {
            background: #ffffff;
            border: 1px solid var(--slate-200);
            border-radius: 8px;
            padding: 10px 12px;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
            display: flex;
            flex-direction: column;
            gap: 8px;
            transition: border-color 0.15s ease, box-shadow 0.15s ease;
        }
        .card:hover {
            border-color: var(--slate-400);
            box-shadow: 0 2px 6px rgba(0, 0, 0, 0.06);
        }

        .card-head {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
        }
        .key-input {
            flex: 1;
            font-family: var(--mono-font);
            font-size: 12px;
            font-weight: 700;
            color: var(--slate-900);
            background: var(--slate-50);
            border: 1px solid var(--slate-200);
            border-radius: 4px;
            padding: 5px 8px;
            outline: none;
        }
        .key-input:focus {
            background: #ffffff;
            border-color: var(--b2b-blue);
        }

        .card-actions {
            display: flex;
            align-items: center;
            gap: 4px;
        }
        .btn-icon {
            background: transparent;
            border: 1px solid var(--slate-200);
            color: var(--slate-500);
            border-radius: 4px;
            padding: 3px 6px;
            font-size: 11px;
            cursor: pointer;
            line-height: 1;
        }
        .btn-icon:hover {
            background: var(--slate-100);
            color: var(--slate-800);
        }
        .btn-icon.del:hover {
            background: var(--b2b-crimson-bg);
            color: var(--b2b-crimson);
            border-color: #fca5a5;
        }

        /* Candidates List */
        .candidates-container {
            display: flex;
            flex-direction: column;
            gap: 5px;
        }
        .candidate-row {
            display: flex;
            align-items: center;
            gap: 6px;
            background: var(--slate-50);
            border: 1px solid var(--slate-200);
            border-radius: 5px;
            padding: 4px 6px;
            transition: all 0.15s ease;
        }
        .candidate-row.is-active {
            background: var(--b2b-emerald-bg);
            border-color: var(--b2b-emerald-border);
        }
        .candidate-row input[type="radio"] {
            margin: 0;
            cursor: pointer;
            width: 14px;
            height: 14px;
            accent-color: var(--b2b-emerald);
        }
        .state-tag {
            font-size: 10px;
            font-weight: 700;
            padding: 1px 5px;
            border-radius: 3px;
            background: var(--slate-200);
            color: var(--slate-600);
            white-space: nowrap;
            letter-spacing: 0.02em;
        }
        .candidate-row.is-active .state-tag {
            background: var(--b2b-emerald);
            color: #ffffff;
        }
        .val-field {
            flex: 1;
            font-family: var(--mono-font);
            font-size: 11.5px;
            color: var(--slate-800);
            background: #ffffff;
            border: 1px solid var(--slate-200);
            border-radius: 3px;
            padding: 4px 6px;
            outline: none;
        }
        .val-field:focus {
            border-color: var(--b2b-blue);
        }
        .btn-del-candidate {
            border: none;
            background: transparent;
            color: var(--slate-400);
            cursor: pointer;
            font-size: 13px;
            padding: 0 4px;
            line-height: 1;
        }
        .btn-del-candidate:hover {
            color: var(--b2b-crimson);
        }

        .btn-add-cand {
            align-self: flex-start;
            background: transparent;
            border: 1px dashed var(--slate-300);
            color: var(--slate-600);
            font-size: 11px;
            font-weight: 600;
            padding: 3px 8px;
            border-radius: 4px;
            cursor: pointer;
        }
        .btn-add-cand:hover {
            background: var(--slate-100);
            border-color: var(--slate-400);
            color: var(--slate-900);
        }

        /* Keyboard Shortcut Hint */
        .kbd-shortcut {
            font-family: var(--mono-font);
            font-size: 10px;
            background: rgba(255,255,255,0.25);
            padding: 2px 4px;
            border-radius: 3px;
            margin-left: 4px;
        }
    </style>
</head>
<body>

    <!-- Floating Sticky Control Bar -->
    <header class="top-navbar">
        <div class="brand-section">
            <span class="brand-badge">B2B CONFIG</span>
            <span class="file-path" title="__FULL_PATH__">📁 __FILE_NAME__</span>
        </div>

        <div class="center-controls">
            <div class="search-wrap">
                <span class="search-icon">🔍</span>
                <input type="text" id="searchInput" class="search-input" placeholder="실시간 검색 (Key, Value 또는 주석)..." onkeyup="handleFilter()">
            </div>
            
            <div class="view-toggle">
                <button type="button" class="view-btn" onclick="setViewMode(1)">1단</button>
                <button type="button" class="view-btn" onclick="setViewMode(2)">2단</button>
                <button type="button" class="view-btn active" onclick="setViewMode(3)">3단 (최대밀도)</button>
            </div>
        </div>

        <div class="right-actions">
            <div class="stat-pill">
                총 변수: <strong id="totalVarsCount">0</strong>개
            </div>
            <button type="button" class="btn btn-sub" onclick="addNewKeyCard()">+ 환경변수</button>
            <button type="button" class="btn btn-sub" onclick="addNewSectionHeader()">+ 섹션구분</button>
            <button type="button" class="btn btn-save" onclick="submitForm()">
                💾 저장하기 <span class="kbd-shortcut">⌘S</span>
            </button>
        </div>
    </header>

    __STATUS_BANNER__

    <main class="workspace">
        <form method="POST" action="/save" id="envForm">
            <input type="hidden" name="payload" id="payloadInput">
            <div id="contentRoot">
                __ITEMS_HTML__
            </div>
        </form>
    </main>

    <script>
        let groupCounter = __GROUP_COUNTER__;
        let currentCols = 3;

        // View Mode Switcher
        function setViewMode(cols) {
            currentCols = cols;
            document.querySelectorAll('.view-btn').forEach((btn, idx) => {
                btn.classList.toggle('active', (idx + 1) === cols);
            });
            document.querySelectorAll('.grid-container').forEach(grid => {
                grid.className = `grid-container grid-col-${cols}`;
            });
            localStorage.setItem('b2b_env_view_cols', cols);
        }

        // Initialize view preference
        const savedCols = parseInt(localStorage.getItem('b2b_env_view_cols') || '3');
        if ([1, 2, 3].includes(savedCols)) {
            setViewMode(savedCols);
        }

        function updateCounts() {
            const count = document.querySelectorAll('.card').length;
            document.getElementById('totalVarsCount').innerText = count;
        }
        updateCounts();

        // Keyboard Shortcut: Cmd+S / Ctrl+S
        window.addEventListener('keydown', function(e) {
            if ((e.metaKey || e.ctrlKey) && e.key === 's') {
                e.preventDefault();
                submitForm();
            }
        });

        // Search Filter
        function handleFilter() {
            const query = document.getElementById('searchInput').value.toLowerCase().trim();
            const cards = document.querySelectorAll('.card');
            
            cards.forEach(card => {
                const keyVal = card.querySelector('.key-input').value.toLowerCase();
                const candVals = Array.from(card.querySelectorAll('.val-field')).map(f => f.value.toLowerCase()).join(' ');
                const match = keyVal.includes(query) || candVals.includes(query);
                card.style.display = match ? '' : 'none';
            });

            // Sections visibility based on query
            document.querySelectorAll('.section-block').forEach(sec => {
                if (!query) {
                    sec.style.display = '';
                    return;
                }
                const secText = sec.querySelector('.section-title').innerText.toLowerCase();
                const visibleCards = sec.querySelectorAll('.card:not([style*="display: none"])');
                sec.style.display = (secText.includes(query) || visibleCards.length > 0) ? '' : 'none';
            });
        }

        // Candidate Toggle Radio
        function onCandidateRadioChange(radio) {
            const container = radio.closest('.candidates-container');
            container.querySelectorAll('.candidate-row').forEach(row => {
                const r = row.querySelector('input[type="radio"]');
                const tag = row.querySelector('.state-tag');
                if (r.checked) {
                    row.classList.add('is-active');
                    tag.innerText = 'ACTIVE';
                } else {
                    row.classList.remove('is-active');
                    tag.innerText = '주석 보관';
                }
            });
        }

        // Add Candidate to a Card
        function addCandidateToCard(btn) {
            const container = btn.previousElementSibling;
            const groupId = btn.getAttribute('data-group-id');
            const row = document.createElement('div');
            row.className = 'candidate-row';
            row.innerHTML = `
                <input type="radio" name="radio_${groupId}" onchange="onCandidateRadioChange(this)">
                <span class="state-tag">주석 보관</span>
                <input type="text" class="val-field" placeholder="새 후보 값 입력">
                <button type="button" class="btn-del-candidate" title="후보 삭제" onclick="removeCandidate(this)">×</button>
            `;
            container.appendChild(row);
            row.querySelector('.val-field').focus();
        }

        // Remove Candidate
        function removeCandidate(btn) {
            const row = btn.closest('.candidate-row');
            const container = row.closest('.candidates-container');
            const wasActive = row.querySelector('input[type="radio"]').checked;
            row.remove();

            if (wasActive) {
                const firstRadio = container.querySelector('input[type="radio"]');
                if (firstRadio) {
                    firstRadio.checked = true;
                    onCandidateRadioChange(firstRadio);
                }
            }
        }

        // Add New Key Card
        function addNewKeyCard() {
            groupCounter++;
            const firstGrid = document.querySelector('.grid-container');
            const card = document.createElement('div');
            card.className = 'card';
            card.innerHTML = `
                <div class="card-head">
                    <input type="text" class="key-input" placeholder="NEW_VARIABLE_NAME" required>
                    <div class="card-actions">
                        <button type="button" class="btn-icon del" title="변수 삭제" onclick="this.closest('.card').remove(); updateCounts();">삭제</button>
                    </div>
                </div>
                <div class="candidates-container">
                    <div class="candidate-row is-active">
                        <input type="radio" name="radio_${groupCounter}" checked onchange="onCandidateRadioChange(this)">
                        <span class="state-tag">ACTIVE</span>
                        <input type="text" class="val-field" placeholder="값 입력">
                        <button type="button" class="btn-del-candidate" title="후보 삭제" onclick="removeCandidate(this)">×</button>
                    </div>
                </div>
                <button type="button" class="btn-add-cand" data-group-id="${groupCounter}" onclick="addCandidateToCard(this)">+ 후보 추가</button>
            `;
            if (firstGrid) {
                firstGrid.prepend(card);
            } else {
                const newGrid = document.createElement('div');
                newGrid.className = `grid-container grid-col-${currentCols}`;
                newGrid.appendChild(card);
                document.getElementById('contentRoot').appendChild(newGrid);
            }
            card.querySelector('.key-input').focus();
            updateCounts();
        }

        // Add Section Header
        function addNewSectionHeader() {
            const comment = prompt("추가할 섹션 주석을 입력하세요 (예: # Database Settings):", "# ");
            if (comment === null || !comment.trim()) return;

            const block = document.createElement('div');
            block.className = 'section-block';
            block.setAttribute('data-comment', comment);
            block.innerHTML = `
                <div class="section-header">
                    <span class="section-title">${comment}</span>
                    <button type="button" class="section-del-btn" onclick="this.closest('.section-block').remove()">섹션 삭제</button>
                </div>
                <div class="grid-container grid-col-${currentCols}"></div>
            `;
            document.getElementById('contentRoot').appendChild(block);
        }

        // Submit and Serialize
        function submitForm() {
            const root = document.getElementById('contentRoot');
            const result = [];

            // Traverse DOM nodes in display order
            root.childNodes.forEach(node => {
                if (node.nodeType !== 1) return;

                if (node.classList.contains('section-block')) {
                    const comment = node.getAttribute('data-comment');
                    result.push({ type: 'comment', value: comment });

                    // Inside section grid
                    const grid = node.querySelector('.grid-container');
                    if (grid) {
                        grid.querySelectorAll('.card').forEach(card => {
                            serializeCard(card, result);
                        });
                    }
                } else if (node.classList.contains('grid-container')) {
                    node.querySelectorAll('.card').forEach(card => {
                        serializeCard(card, result);
                    });
                }
            });

            document.getElementById('payloadInput').value = JSON.stringify(result);
            document.getElementById('envForm').submit();
        }

        function serializeCard(card, resultList) {
            const key = card.querySelector('.key-input').value.trim();
            if (!key) return;

            const candRows = card.querySelectorAll('.candidate-row');
            const candidates = [];
            let activeIdx = 0;

            candRows.forEach((row, idx) => {
                const val = row.querySelector('.val-field').value;
                const radio = row.querySelector('input[type="radio"]');
                const isActive = radio && radio.checked;
                if (isActive) activeIdx = idx;
                candidates.push({ val: val, active: isActive });
            });

            resultList.push({
                type: 'var_group',
                key: key,
                candidates: candidates,
                active_idx: activeIdx
            });
        }
    </script>
</body>
</html>
"""


def render_card_html(item, group_id):
    k = item.get("key", "").replace('"', "&quot;")
    candidates = item.get("candidates", [])
    active_idx = item.get("active_idx", 0)

    rows = []
    for idx, c in enumerate(candidates):
        v = c.get("val", "").replace('"', "&quot;")
        is_active = (idx == active_idx)
        active_cls = "is-active" if is_active else ""
        checked_attr = "checked" if is_active else ""
        badge_text = "ACTIVE" if is_active else "주석 보관"

        rows.append(
            f'<div class="candidate-row {active_cls}">'
            f'<input type="radio" name="radio_{group_id}" {checked_attr} onchange="onCandidateRadioChange(this)">'
            f'<span class="state-tag">{badge_text}</span>'
            f'<input type="text" class="val-field" value="{v}">'
            f'<button type="button" class="btn-del-candidate" title="후보 삭제" onclick="removeCandidate(this)">×</button>'
            f'</div>'
        )

    return (
        f'<div class="card">'
        f'<div class="card-head">'
        f'<input type="text" class="key-input" value="{k}" required>'
        f'<div class="card-actions">'
        f'<button type="button" class="btn-icon del" title="변수 삭제" onclick="this.closest(\'.card\').remove(); updateCounts();">삭제</button>'
        f'</div>'
        f'</div>'
        f'<div class="candidates-container">'
        f'{"".join(rows)}'
        f'</div>'
        f'<button type="button" class="btn-add-cand" data-group-id="{group_id}" onclick="addCandidateToCard(this)">+ 후보 추가</button>'
        f'</div>'
    )


class EnvManagerHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed_url.query)
        saved = params.get("saved", ["0"])[0] == "1"

        items = parse_env_file(ENV_PATH)
        group_counter = 0

        # Construct structured HTML grouped by section/comment
        blocks_html = []
        current_section = None
        current_section_cards = []
        orphan_cards = []

        for item in items:
            t = item.get("type")
            if t == "comment":
                # Flush previous section
                if current_section is not None:
                    blocks_html.append(
                        f'<div class="section-block" data-comment="{current_section}">'
                        f'<div class="section-header">'
                        f'<span class="section-title">{current_section}</span>'
                        f'<button type="button" class="section-del-btn" onclick="this.closest(\'.section-block\').remove()">섹션 삭제</button>'
                        f'</div>'
                        f'<div class="grid-container grid-col-3">'
                        f'{"".join(current_section_cards)}'
                        f'</div>'
                        f'</div>'
                    )
                    current_section_cards = []
                elif orphan_cards:
                    blocks_html.append(
                        f'<div class="grid-container grid-col-3">'
                        f'{"".join(orphan_cards)}'
                        f'</div>'
                    )
                    orphan_cards = []

                current_section = item.get("value", "")
            elif t == "var_group":
                group_counter += 1
                card_html = render_card_html(item, group_counter)
                if current_section is not None:
                    current_section_cards.append(card_html)
                else:
                    orphan_cards.append(card_html)

        # Flush trailing section or orphan cards
        if current_section is not None:
            blocks_html.append(
                f'<div class="section-block" data-comment="{current_section}">'
                f'<div class="section-header">'
                f'<span class="section-title">{current_section}</span>'
                f'<button type="button" class="section-del-btn" onclick="this.closest(\'.section-block\').remove()">섹션 삭제</button>'
                f'</div>'
                f'<div class="grid-container grid-col-3">'
                f'{"".join(current_section_cards)}'
                f'</div>'
                f'</div>'
            )
        elif orphan_cards:
            blocks_html.append(
                f'<div class="grid-container grid-col-3">'
                f'{"".join(orphan_cards)}'
                f'</div>'
            )

        status_banner = ""
        if saved:
            status_banner = (
                '<div class="toast-banner">'
                '<span>✅ 변경사항이 정상 저장되었습니다. (활성 1개 반영, 후보 주석 보관, .bak 백업 완료)</span>'
                '<button type="button" style="background:none;border:none;color:#065f46;cursor:pointer;font-weight:700;" onclick="this.closest(\'.toast-banner\').remove()">✕</button>'
                '</div>'
            )

        full_path = os.path.abspath(ENV_PATH)
        file_name = os.path.basename(full_path)

        page = (
            HTML_PAGE.replace("__FULL_PATH__", full_path)
            .replace("__FILE_NAME__", file_name)
            .replace("__STATUS_BANNER__", status_banner)
            .replace("__ITEMS_HTML__", "\n".join(blocks_html))
            .replace("__GROUP_COUNTER__", str(group_counter))
        )

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page.encode("utf-8"))

    def do_POST(self):
        if self.path != "/save":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        parsed = urllib.parse.parse_qs(body, keep_blank_values=True)

        payload_json = parsed.get("payload", ["[]"])[0]
        try:
            items = json.loads(payload_json)
        except Exception:
            items = []

        write_env_file(ENV_PATH, items)

        self.send_response(303)
        self.send_header("Location", "/?saved=1")
        self.end_headers()


def run():
    server_address = (HOST, PORT)
    httpd = HTTPServer(server_address, EnvManagerHandler)
    print("=" * 60)
    print(f"🚀 Enterprise B2B .env Manager 실행 완료")
    print(f"📂 파일 경로 : {os.path.abspath(ENV_PATH)}")
    print(f"🔗 웹 콘솔  : http://{HOST}:{PORT}")
    print(f"💡 주요 기능 :")
    print(f"   - 상단 상시 고정 플로팅 바 (저장하기 버튼, 검색, 1/2/3단 레이아웃 전환)")
    print(f"   - 3단 그리드 고밀도 B2B 대시보드 UI (한 화면에 다량 표시)")
    print(f"   - 단축키: Cmd+S / Ctrl+S 로 즉시 저장")
    print("   (종료하려면 Ctrl + C 를 누르세요)")
    print("=" * 60)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 서버를 안전하게 종료합니다.")
        httpd.server_close()


if __name__ == "__main__":
    run()
