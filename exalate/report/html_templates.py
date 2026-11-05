"""HTML report templates with embedded styling and interactive search."""

import html
from typing import Any, Dict, List, Optional
import pandas as pd

from exalate.models import AuditSummary

BASE_CSS = """
:root {
  --bg-page: #f8fafc;
  --bg-card: #ffffff;
  --bg-table-head: #f1f5f9;
  --border: #e2e8f0;
  --border-strong: #cbd5e1;
  --text-main: #0f172a;
  --text-muted: #64748b;
  --primary: #2563eb;
  --primary-light: #eff6ff;
  --danger: #dc2626;
  --danger-light: #fef2f2;
  --warning: #d97706;
  --warning-light: #fffbeb;
  --success: #16a34a;
  --success-light: #f0fdf4;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  background-color: var(--bg-page);
  color: var(--text-main);
  line-height: 1.5;
  padding: 32px 20px;
}
.container {
  max-width: 1200px;
  margin: 0 auto;
}
header {
  background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
  color: #ffffff;
  padding: 28px 32px;
  border-radius: 12px;
  margin-bottom: 24px;
  box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05);
}
header h1 {
  font-size: 24px;
  font-weight: 700;
  margin-bottom: 8px;
}
header p {
  color: #94a3b8;
  font-size: 14px;
}
.meta-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}
.badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 10px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  background: rgba(255,255,255,0.15);
  color: #f8fafc;
}
.badge.blue { background: #3b82f6; }
.badge.green { background: #10b981; }
.badge.amber { background: #f59e0b; }
.badge.red { background: #ef4444; }

/* KPI Metrics */
.metrics-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 16px;
  margin-bottom: 24px;
}
.metric-card {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  box-shadow: 0 1px 3px rgba(0,0,0,0.02);
}
.metric-card .title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 8px;
}
.metric-card .value {
  font-size: 32px;
  font-weight: 700;
  color: var(--text-main);
  line-height: 1.1;
}
.metric-card .sub {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-muted);
}

/* Callouts */
.notice-box {
  background: var(--warning-light);
  border: 1px solid #fef3c7;
  color: #92400e;
  padding: 14px 18px;
  border-radius: 8px;
  font-size: 13px;
  margin-bottom: 24px;
  line-height: 1.5;
}
.notice-box strong { color: #78350f; }

/* Card & Table */
.card-section {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 24px;
  margin-bottom: 24px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.02);
}
.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  flex-wrap: wrap;
  gap: 12px;
}
.section-header h2 {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-main);
}
.search-input {
  padding: 8px 14px;
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  font-size: 13px;
  min-width: 260px;
  outline: none;
}
.search-input:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 2px rgba(37,99,235,0.15);
}

.table-wrapper {
  overflow-x: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13.5px;
  text-align: left;
}
th {
  background-color: var(--bg-table-head);
  padding: 10px 14px;
  font-weight: 600;
  color: var(--text-main);
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
td {
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  color: var(--text-main);
}
tr:last-child td { border-bottom: none; }
tr:hover td { background-color: #f8fafc; }
.empty-state {
  text-align: center;
  padding: 32px 16px;
  color: var(--text-muted);
  font-size: 14px;
}
.status-pill {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
}
.pill-error { background: var(--danger-light); color: var(--danger); border: 1px solid #fecaca; }
.pill-warn { background: var(--warning-light); color: var(--warning); border: 1px solid #fef3c7; }
.pill-info { background: var(--primary-light); color: var(--primary); border: 1px solid #bfdbfe; }
.pill-default { background: #f1f5f9; color: #475569; border: 1px solid #e2e8f0; }

footer {
  text-align: center;
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 32px;
}
"""

SEARCH_SCRIPT = """
<script>
function filterTable(inputId, tableId) {
  const input = document.getElementById(inputId);
  const filter = input.value.toLowerCase();
  const table = document.getElementById(tableId);
  const tr = table.getElementsByTagName("tr");

  for (let i = 1; i < tr.length; i++) {
    let visible = false;
    const tds = tr[i].getElementsByTagName("td");
    for (let j = 0; j < tds.length; j++) {
      if (tds[j]) {
        const txtValue = tds[j].textContent || tds[j].innerText;
        if (txtValue.toLowerCase().indexOf(filter) > -1) {
          visible = true;
          break;
        }
      }
    }
    tr[i].style.display = visible ? "" : "none";
  }
}
</script>
"""


def _status_pill(val: Any) -> str:
    """Format status value into a styled badge."""
    if not val or pd.isna(val):
        return '<span class="status-pill pill-default">-</span>'
    s = str(val).strip()
    s_upper = s.upper()
    if any(x in s_upper for x in ["ERROR", "FAILED", "FAIL"]):
        return f'<span class="status-pill pill-error">{html.escape(s)}</span>'
    if any(x in s_upper for x in ["IN_PROGRESS", "PROCESSING", "WAITING"]):
        return f'<span class="status-pill pill-warn">{html.escape(s)}</span>'
    if any(x in s_upper for x in ["QUEUED", "PENDING", "CREATED"]):
        return f'<span class="status-pill pill-info">{html.escape(s)}</span>'
    return f'<span class="status-pill pill-default">{html.escape(s)}</span>'


def render_summary_html(summary: AuditSummary) -> str:
    """Render exalate_summary.html."""
    out_total = summary.outgoing_total
    in_total = summary.incoming_total
    err_total = summary.errors_total

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>Exalate Migration Summary</title>
  <style>{BASE_CSS}</style>
</head>
<body>
  <div class="container">
    <header>
      <h1>Exalate Migration Audit - Summary Report</h1>
      <p>Jira Data Center → Jira Cloud 사전조사: 동기화 대기열(Queue) 및 오류 현황</p>
      <div class="meta-badges">
        <span class="badge blue">Jira: {html.escape(summary.base_url)}</span>
        <span class="badge">수집일시: {html.escape(summary.collected_at)}</span>
      </div>
    </header>

    <div class="notice-box">
      <strong>⚠️ 핵심 주의사항:</strong> Outgoing/Incoming Queue는 현재 처리 대기/진행 중인 트랜잭션 수량입니다.<br>
      전체 누적 동기화 이슈 매핑(Synced Issues / Relations)은 <strong>Phase 2 (Jira ActiveObjects DB 조회)</strong>를 통해 별도로 조사됩니다.
    </div>

    <div class="metrics-row">
      <div class="metric-card">
        <div class="title">Outgoing Queue (발신 대기열)</div>
        <div class="value">{out_total:,}</div>
        <div class="sub">수집된 레코드: {summary.outgoing_collected:,} 건</div>
      </div>
      <div class="metric-card">
        <div class="title">Incoming Queue (수신 대기열)</div>
        <div class="value">{in_total:,}</div>
        <div class="sub">수집된 레코드: {summary.incoming_collected:,} 건</div>
      </div>
      <div class="metric-card">
        <div class="title">Errors (동기화 오류)</div>
        <div class="value" style="color: {'#dc2626' if err_total > 0 else '#16a34a'};">{err_total:,}</div>
        <div class="sub">수집된 에러: {summary.errors_collected:,} 건</div>
      </div>
    </div>

    <div class="card-section">
      <div class="section-header">
        <h2>요약 통계 테이블</h2>
      </div>
      <div class="table-wrapper">
        <table>
          <thead>
            <tr>
              <th>유형 (Type)</th>
              <th>총 건수 (Total Reported)</th>
              <th>수집 건수 (Collected Records)</th>
              <th>상태 비고</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>OUTGOING</strong> (발신 동기화 큐)</td>
              <td>{out_total:,}</td>
              <td>{summary.outgoing_collected:,}</td>
              <td>{'정상 (대기열 없음)' if out_total == 0 else _status_pill('QUEUED')}</td>
            </tr>
            <tr>
              <td><strong>INCOMING</strong> (수신 동기화 큐)</td>
              <td>{in_total:,}</td>
              <td>{summary.incoming_collected:,}</td>
              <td>{'정상 (대기열 없음)' if in_total == 0 else _status_pill('QUEUED')}</td>
            </tr>
            <tr>
              <td><strong>ERROR</strong> (동기화 에러)</td>
              <td>{err_total:,}</td>
              <td>{summary.errors_collected:,}</td>
              <td>{'정상 (에러 없음)' if err_total == 0 else _status_pill('ERROR')}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <footer>
      Exalate Migration Audit Tool &copy; 2026. Confidential Migration Assessment Data.
    </footer>
  </div>
</body>
</html>
"""


def render_table_html(
    title: str,
    subtitle: str,
    columns: List[str],
    df: pd.DataFrame,
    summary_metric_title: str,
    summary_count: int,
) -> str:
    """Render individual HTML reports for Outgoing, Incoming, or Errors."""
    rows_html = []
    if len(df) == 0:
        rows_html.append(f'<tr><td colspan="{len(columns)}" class="empty-state">조사된 데이터가 없습니다 (0 건).</td></tr>')
    else:
        for _, row in df.iterrows():
            tds = []
            for col in columns:
                val = row.get(col, "")
                if pd.isna(val):
                    val_str = ""
                else:
                    val_str = str(val)

                if col.lower() in ("status", "state", "errortype"):
                    tds.append(f"<td>{_status_pill(val_str)}</td>")
                elif col.lower() in ("error", "message") and val_str:
                    tds.append(f'<td style="max-width: 320px; word-break: break-word; font-size: 12.5px;">{html.escape(val_str)}</td>')
                elif col.lower() in ("issuekey", "projectkey") and val_str:
                    tds.append(f"<td><strong>{html.escape(val_str)}</strong></td>")
                else:
                    tds.append(f"<td>{html.escape(val_str)}</td>")
            rows_html.append(f"<tr>{''.join(tds)}</tr>")

    table_body = "\n".join(rows_html)
    th_headers = "".join([f"<th>{html.escape(col)}</th>" for col in columns])

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>{html.escape(title)}</title>
  <style>{BASE_CSS}</style>
</head>
<body>
  <div class="container">
    <header>
      <h1>{html.escape(title)}</h1>
      <p>{html.escape(subtitle)}</p>
      <div class="meta-badges">
        <span class="badge blue">총 {summary_count:,} 건</span>
        <span class="badge">수집: {len(df):,} 건</span>
      </div>
    </header>

    <div class="card-section">
      <div class="section-header">
        <h2>{html.escape(summary_metric_title)} ({len(df):,} 레코드)</h2>
        <input type="text" id="searchInput" class="search-input" onkeyup="filterTable('searchInput', 'reportTable')" placeholder="실시간 검색 (이슈 키, 상태 등)...">
      </div>
      <div class="table-wrapper">
        <table id="reportTable">
          <thead>
            <tr>{th_headers}</tr>
          </thead>
          <tbody>
            {table_body}
          </tbody>
        </table>
      </div>
    </div>

    <footer>
      Exalate Migration Audit Tool &copy; 2026. Confidential Migration Assessment Data.
    </footer>
  </div>
  {SEARCH_SCRIPT}
</body>
</html>
"""


def render_master_report_html(
    summary: AuditSummary,
    outgoing_df: Optional[pd.DataFrame] = None,
    incoming_df: Optional[pd.DataFrame] = None,
    errors_df: Optional[pd.DataFrame] = None,
) -> str:
    """Render unified master report (exalate_report.html)."""
    out_df = outgoing_df if outgoing_df is not None else pd.DataFrame()
    in_df = incoming_df if incoming_df is not None else pd.DataFrame()
    err_df = errors_df if errors_df is not None else pd.DataFrame()

    def make_rows(df, cols):
        if len(df) == 0:
            return f'<tr><td colspan="{len(cols)}" class="empty-state">해당 대기열에 항목이 없습니다 (0 건).</td></tr>'
        res = []
        for _, r in df.iterrows():
            tds = []
            for c in cols:
                v = r.get(c, "")
                v_str = "" if pd.isna(v) else str(v)
                if c.lower() in ("status", "errortype"):
                    tds.append(f"<td>{_status_pill(v_str)}</td>")
                elif c.lower() in ("error", "message") and v_str:
                    tds.append(f'<td style="max-width: 300px; word-break: break-word; font-size: 12px;">{html.escape(v_str)}</td>')
                elif c.lower() in ("issuekey", "projectkey") and v_str:
                    tds.append(f"<td><strong>{html.escape(v_str)}</strong></td>")
                else:
                    tds.append(f"<td>{html.escape(v_str)}</td>")
            res.append(f"<tr>{''.join(tds)}</tr>")
        return "\n".join(res)

    from report.exporter import ERROR_COLUMNS, INCOMING_COLUMNS, OUTGOING_COLUMNS

    out_rows = make_rows(out_df, OUTGOING_COLUMNS)
    in_rows = make_rows(in_df, INCOMING_COLUMNS)
    err_rows = make_rows(err_df, ERROR_COLUMNS)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>Exalate Migration Audit - Master Report</title>
  <style>
    {BASE_CSS}
    .tab-bar {{
      display: flex;
      gap: 8px;
      margin-bottom: 20px;
      border-bottom: 2px solid var(--border);
      padding-bottom: 8px;
    }}
    .tab-btn {{
      background: none;
      border: none;
      padding: 8px 16px;
      font-size: 14px;
      font-weight: 600;
      color: var(--text-muted);
      cursor: pointer;
      border-radius: 6px;
      transition: all 0.15s ease;
    }}
    .tab-btn:hover {{
      background: var(--border);
      color: var(--text-main);
    }}
    .tab-btn.active {{
      background: var(--primary);
      color: #ffffff;
    }}
    .tab-content {{
      display: none;
    }}
    .tab-content.active {{
      display: block;
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>Exalate Migration Audit - Master Dashboard</h1>
      <p>Jira Data Center → Jira Cloud 사전조사: Outgoing Queue, Incoming Queue, Errors 통합 리포트</p>
      <div class="meta-badges">
        <span class="badge blue">Jira Base URL: {html.escape(summary.base_url)}</span>
        <span class="badge">수집일시: {html.escape(summary.collected_at)}</span>
      </div>
    </header>

    <div class="notice-box">
      <strong>📌 마이그레이션 핵심 안내:</strong> Outgoing / Incoming Queue는 현재 처리 대기/진행 중인 트랜잭션입니다. 전체 누적 동기화 이슈 목록(Synced Issues)은 <strong>Phase 2 (Jira DB AO_08F1AF_* 조회)</strong>를 통해 인벤토리를 확장합니다.
    </div>

    <div class="metrics-row">
      <div class="metric-card">
        <div class="title">Outgoing Queue (발신 큐)</div>
        <div class="value">{summary.outgoing_total:,}</div>
        <div class="sub">수집: {len(out_df):,} 건</div>
      </div>
      <div class="metric-card">
        <div class="title">Incoming Queue (수신 큐)</div>
        <div class="value">{summary.incoming_total:,}</div>
        <div class="sub">수집: {len(in_df):,} 건</div>
      </div>
      <div class="metric-card">
        <div class="title">Errors (동기화 오류)</div>
        <div class="value" style="color: {'#dc2626' if summary.errors_total > 0 else '#16a34a'};">{summary.errors_total:,}</div>
        <div class="sub">수집: {len(err_df):,} 건</div>
      </div>
    </div>

    <!-- Tab Navigation -->
    <div class="tab-bar">
      <button class="tab-btn active" onclick="switchTab('tab-outgoing', this)">Outgoing Queue ({len(out_df)})</button>
      <button class="tab-btn" onclick="switchTab('tab-incoming', this)">Incoming Queue ({len(in_df)})</button>
      <button class="tab-btn" onclick="switchTab('tab-errors', this)">Errors ({len(err_df)})</button>
    </div>

    <!-- Tab 1: Outgoing -->
    <div id="tab-outgoing" class="tab-content active">
      <div class="card-section">
        <div class="section-header">
          <h2>Outgoing Sync Events</h2>
          <input type="text" id="searchOut" class="search-input" onkeyup="filterTable('searchOut', 'tblOut')" placeholder="Outgoing 검색 (이슈 키, 커넥션 등)...">
        </div>
        <div class="table-wrapper">
          <table id="tblOut">
            <thead>
              <tr>{''.join([f'<th>{html.escape(c)}</th>' for c in OUTGOING_COLUMNS])}</tr>
            </thead>
            <tbody>
              {out_rows}
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Tab 2: Incoming -->
    <div id="tab-incoming" class="tab-content">
      <div class="card-section">
        <div class="section-header">
          <h2>Incoming Sync Requests</h2>
          <input type="text" id="searchIn" class="search-input" onkeyup="filterTable('searchIn', 'tblIn')" placeholder="Incoming 검색 (이슈 키, 커넥션 등)...">
        </div>
        <div class="table-wrapper">
          <table id="tblIn">
            <thead>
              <tr>{''.join([f'<th>{html.escape(c)}</th>' for c in INCOMING_COLUMNS])}</tr>
            </thead>
            <tbody>
              {in_rows}
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Tab 3: Errors -->
    <div id="tab-errors" class="tab-content">
      <div class="card-section">
        <div class="section-header">
          <h2>Exalate Errors</h2>
          <input type="text" id="searchErr" class="search-input" onkeyup="filterTable('searchErr', 'tblErr')" placeholder="에러 검색 (이슈 키, 에러 메시지 등)...">
        </div>
        <div class="table-wrapper">
          <table id="tblErr">
            <thead>
              <tr>{''.join([f'<th>{html.escape(c)}</th>' for c in ERROR_COLUMNS])}</tr>
            </thead>
            <tbody>
              {err_rows}
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <footer>
      Exalate Migration Audit Tool &copy; 2026. Confidential Migration Assessment Data.
    </footer>
  </div>

  <script>
  function switchTab(tabId, btn) {{
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    document.getElementById(tabId).classList.add('active');
    btn.classList.add('active');
  }}
  </script>
  {SEARCH_SCRIPT}
</body>
</html>
"""
