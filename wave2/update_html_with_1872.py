# -*- coding: utf-8 -*-
import json
import os

with open('unmigrated_1872_data.json', 'r', encoding='utf-8') as f:
    data_1872 = json.load(f)

with open('parsed_migration_data.json', 'r', encoding='utf-8') as f:
    base_data = json.load(f)

# Sort projects by count
proj_counts = data_1872['project_counts']
proj_sizes = data_1872['project_sizes_mb']
sorted_projs = sorted(proj_counts.keys(), key=lambda p: proj_counts[p], reverse=True)

html_content = f'''<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JCMA 마이그레이션 진단 & 첨부파일 종합 분석 보고서 (1,872건 상세)</title>
    <!-- Chart.js via CDN -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        :root {{
            --primary: #0052cc;
            --primary-dark: #0747a6;
            --primary-light: #deebff;
            --secondary: #172b4d;
            --text-dark: #172b4d;
            --text-muted: #6b778c;
            --bg-light: #f4f5f7;
            --bg-card: #ffffff;
            --danger: #de350b;
            --danger-bg: #ffebe6;
            --warning: #ff8b00;
            --warning-bg: #fffae6;
            --success: #00875a;
            --success-bg: #e3fcef;
            --info: #0065ff;
            --info-bg: #deebff;
            --border: #dfe1e6;
            --border-hover: #b3bac5;
            --radius-sm: 6px;
            --radius-md: 10px;
            --radius-lg: 14px;
            --shadow-sm: 0 1px 3px rgba(9,30,66,0.08);
            --shadow-md: 0 4px 12px rgba(9,30,66,0.1);
            --shadow-lg: 0 8px 24px rgba(9,30,66,0.15);
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans KR", Helvetica, Arial, sans-serif;
            background-color: var(--bg-light);
            color: var(--text-dark);
            line-height: 1.55;
        }}

        header {{
            background: linear-gradient(135deg, #091e42 0%, #172b4d 100%);
            color: #ffffff;
            padding: 28px 40px;
            box-shadow: 0 4px 16px rgba(0,0,0,0.15);
            position: sticky;
            top: 0;
            z-index: 100;
        }}

        .header-top {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
        }}

        .logo-area {{
            display: flex;
            align-items: center;
            gap: 14px;
        }}

        .logo-badge {{
            background: #0052cc;
            color: white;
            font-weight: 700;
            font-size: 13px;
            padding: 5px 12px;
            border-radius: 6px;
            letter-spacing: 0.5px;
            text-transform: uppercase;
        }}

        h1 {{
            font-size: 23px;
            font-weight: 700;
            color: #ffffff;
            letter-spacing: -0.3px;
        }}

        .subtitle {{
            color: #b3bac5;
            font-size: 12.5px;
            margin-top: 4px;
            display: flex;
            gap: 14px;
            flex-wrap: wrap;
        }}

        .btn {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 8px 16px;
            font-size: 13px;
            font-weight: 600;
            border-radius: var(--radius-sm);
            border: 1px solid transparent;
            cursor: pointer;
            transition: all 0.2s ease;
            text-decoration: none;
        }}

        .btn-primary {{
            background-color: #0052cc;
            color: #ffffff;
        }}
        .btn-primary:hover {{
            background-color: #0747a6;
        }}

        .btn-outline {{
            background: rgba(255,255,255,0.12);
            color: #ffffff;
            border-color: rgba(255,255,255,0.25);
        }}
        .btn-outline:hover {{
            background: rgba(255,255,255,0.2);
        }}

        .btn-secondary {{
            background-color: var(--bg-light);
            color: var(--text-dark);
            border-color: var(--border);
        }}
        .btn-secondary:hover {{
            background-color: #ebecf0;
        }}

        .container {{
            max-width: 1440px;
            margin: 0 auto;
            padding: 30px;
        }}

        /* Banner */
        .status-banner {{
            background: var(--danger-bg);
            border-left: 5px solid var(--danger);
            border-radius: var(--radius-md);
            padding: 16px 20px;
            margin-bottom: 24px;
            display: flex;
            align-items: flex-start;
            gap: 14px;
            box-shadow: var(--shadow-sm);
        }}

        .status-banner-icon {{
            font-size: 24px;
            line-height: 1;
        }}

        /* KPI Grid */
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 18px;
            margin-bottom: 28px;
        }}

        .kpi-card {{
            background: var(--bg-card);
            border-radius: var(--radius-md);
            padding: 20px;
            box-shadow: var(--shadow-sm);
            border: 1px solid var(--border);
            transition: transform 0.2s, box-shadow 0.2s;
        }}

        .kpi-card:hover {{
            transform: translateY(-2px);
            box-shadow: var(--shadow-md);
        }}

        .kpi-card.critical {{ border-top: 4px solid var(--danger); }}
        .kpi-card.warning {{ border-top: 4px solid var(--warning); }}
        .kpi-card.info {{ border-top: 4px solid var(--info); }}
        .kpi-card.success {{ border-top: 4px solid var(--success); }}

        .kpi-title {{
            font-size: 12px;
            font-weight: 700;
            color: var(--text-muted);
            text-transform: uppercase;
            margin-bottom: 6px;
            display: flex;
            justify-content: space-between;
        }}

        .kpi-value {{
            font-size: 32px;
            font-weight: 800;
            color: var(--text-dark);
            line-height: 1.1;
            margin-bottom: 4px;
        }}

        .kpi-desc {{
            font-size: 12px;
            color: var(--text-muted);
        }}

        /* Charts Row */
        .charts-row {{
            display: grid;
            grid-template-columns: 1.8fr 1.2fr;
            gap: 20px;
            margin-bottom: 28px;
        }}

        @media (max-width: 992px) {{
            .charts-row {{
                grid-template-columns: 1fr;
            }}
        }}

        .card {{
            background: var(--bg-card);
            border-radius: var(--radius-md);
            border: 1px solid var(--border);
            box-shadow: var(--shadow-sm);
            margin-bottom: 28px;
            overflow: hidden;
        }}

        .card-header {{
            padding: 16px 22px;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: #fafbfc;
        }}

        .card-title {{
            font-size: 15px;
            font-weight: 700;
            color: var(--text-dark);
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .card-body {{
            padding: 22px;
        }}

        .chart-container {{
            position: relative;
            height: 280px;
            width: 100%;
        }}

        /* Table Controls */
        .table-controls {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 14px;
            flex-wrap: wrap;
            gap: 12px;
        }}

        .search-box {{
            position: relative;
            min-width: 280px;
            flex-grow: 1;
            max-width: 480px;
        }}

        .search-box input {{
            width: 100%;
            padding: 8px 14px 8px 34px;
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            font-size: 13px;
        }}

        .search-box input:focus {{
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 2px var(--primary-light);
        }}

        .search-icon {{
            position: absolute;
            left: 10px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-muted);
        }}

        .filter-group {{
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
            align-items: center;
        }}

        .filter-btn {{
            padding: 5px 12px;
            border: 1px solid var(--border);
            background: white;
            border-radius: 16px;
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            cursor: pointer;
            transition: all 0.15s ease;
        }}

        .filter-btn.active {{
            background: var(--primary);
            color: white;
            border-color: var(--primary);
        }}

        .pagination-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 12px 4px 0;
            font-size: 13px;
            color: var(--text-muted);
        }}

        .page-btns {{
            display: flex;
            gap: 6px;
        }}

        .table-responsive {{
            overflow-x: auto;
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 12.5px;
            text-align: left;
        }}

        thead th {{
            background: #fafbfc;
            padding: 10px 12px;
            color: var(--text-muted);
            font-weight: 700;
            border-bottom: 2px solid var(--border);
            white-space: nowrap;
            cursor: pointer;
            user-select: none;
        }}

        thead th:hover {{
            background: #f0f2f5;
        }}

        tbody tr {{
            border-bottom: 1px solid var(--border);
        }}

        tbody tr:hover {{
            background: #f7f9fc;
        }}

        tbody td {{
            padding: 9px 12px;
            vertical-align: middle;
        }}

        .badge {{
            display: inline-flex;
            align-items: center;
            gap: 4px;
            padding: 2px 7px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
        }}

        .badge-danger {{ background: var(--danger-bg); color: var(--danger); }}
        .badge-warning {{ background: var(--warning-bg); color: #974f00; }}
        .badge-info {{ background: var(--info-bg); color: var(--primary); }}
        .badge-neutral {{ background: #ebecf0; color: #42526e; }}
        .badge-project {{ background: #eae6ff; color: #403294; font-weight: 700; }}

        .code-tag {{
            font-family: SFMono-Regular, Consolas, monospace;
            background: #f4f5f7;
            padding: 2px 5px;
            border-radius: 4px;
            font-size: 11.5px;
            border: 1px solid #dfe1e6;
        }}

        .copy-btn {{
            background: transparent;
            border: none;
            cursor: pointer;
            color: #6b778c;
            padding: 1px 3px;
            font-size: 11px;
        }}
        .copy-btn:hover {{ color: var(--primary); }}

        /* Project Status Grid */
        .proj-status-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 16px;
        }}

        .proj-card {{
            background: white;
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}

        .proj-card-top {{
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        .proj-name {{
            font-size: 16px;
            font-weight: 700;
        }}

        @media print {{
            header {{ position: static; }}
            .header-actions, .table-controls, .copy-btn, .filter-group, .pagination-bar {{ display: none !important; }}
            .container {{ padding: 0; }}
            .card {{ box-shadow: none; page-break-inside: avoid; }}
        }}
    </style>
</head>
<body>

    <header>
        <div class="header-top">
            <div class="logo-area">
                <div class="logo-badge">JCMA Report</div>
                <div>
                    <h1>JCMA 마이그레이션 진단 & 첨부파일 종합 분석 보고서</h1>
                    <div class="subtitle">
                        <span><strong>작업 플랜:</strong> 26Wave2-JIRA-T1-1</span>
                        <span>&bull;</span>
                        <span><strong>총 미마이그레이션 첨부파일:</strong> <strong style="color: #ffbdad;">1,872건 (124.77 GB)</strong></span>
                        <span>&bull;</span>
                        <span><strong>추적 테이블:</strong> <code>AO_6FF49D_MIGRATED_FILE</code> 대조 검증</span>
                    </div>
                </div>
            </div>
            <div class="header-actions">
                <button class="btn btn-outline" onclick="window.print()">🖨️ PDF / 인쇄</button>
                <button class="btn btn-primary" onclick="download1872CSV()">📥 1,872건 전체 CSV 다운로드</button>
            </div>
        </div>
    </header>

    <div class="container">

        <!-- Status Banner -->
        <div class="status-banner">
            <div class="status-banner-icon">🚨</div>
            <div>
                <h3 style="color: #bf2600; font-size: 16px; font-weight: 700; margin-bottom: 4px;">
                    JCMA 실제 미마이그레이션 첨부파일 총 1,872건 (124.77 GB) 최종 확인
                </h3>
                <p style="font-size: 13.5px; color: #42526e;">
                    온프레미스 Jira DB의 <strong>JCMA 전송 완료 기록 테이블(<code>AO_6FF49D_MIGRATED_FILE</code>)</strong>과 대조한 결과, 
                    초기 에러 로그에 집계되었던 152건을 포함하여 **총 1,872건(124.77 GB)**의 첨부파일이 클라우드로 이전되지 못한 상태임이 명확히 확인되었습니다.
                    특히 <strong>CQIM (992건, 85.98 GB)</strong> 및 <strong>CAQW (676건, 39.65 GB)</strong>에 대용량 분할 압축(.z01, .zip)과 동영상(.mp4) 파일이 집중되어 있습니다.
                </p>
            </div>
        </div>

        <!-- KPI Cards Grid -->
        <div class="kpi-grid">
            <div class="kpi-card critical">
                <div class="kpi-title">
                    <span>총 미마이그레이션 파일</span>
                    <span class="badge badge-danger">최종 확정</span>
                </div>
                <div class="kpi-value critical">1,872건</div>
                <div class="kpi-desc">7개 프로젝트 합산 (32만 개 중 미이관)</div>
            </div>

            <div class="kpi-card critical">
                <div class="kpi-title">
                    <span>총 미마이그레이션 용량</span>
                    <span class="badge badge-danger">대용량</span>
                </div>
                <div class="kpi-value critical">{data_1872['total_gb']} GB</div>
                <div class="kpi-desc">총 {data_1872['total_mb']:,} MB (평균 68.2 MB/건)</div>
            </div>

            <div class="kpi-card warning">
                <div class="kpi-title">
                    <span>최대 결손 프로젝트</span>
                    <span class="badge badge-warning">CQIM</span>
                </div>
                <div class="kpi-value warning">992건</div>
                <div class="kpi-desc">CQIM 용량만 85.98 GB (전체의 68.9%)</div>
            </div>

            <div class="kpi-card info">
                <div class="kpi-title">
                    <span>2순위 결손 프로젝트</span>
                    <span class="badge badge-info">CAQW</span>
                </div>
                <div class="kpi-value">676건</div>
                <div class="kpi-desc">CAQW 용량 39.65 GB (전체의 31.0%)</div>
            </div>
        </div>

        <!-- Charts Dashboard -->
        <div class="charts-row">
            <div class="card" style="margin-bottom: 0;">
                <div class="card-header">
                    <div class="card-title">📊 프로젝트별 미마이그레이션 건수 및 용량 (GB)</div>
                    <span class="badge badge-neutral">7개 프로젝트</span>
                </div>
                <div class="card-body">
                    <div class="chart-container">
                        <canvas id="projectChart"></canvas>
                    </div>
                </div>
            </div>

            <div class="card" style="margin-bottom: 0;">
                <div class="card-header">
                    <div class="card-title">📁 주요 파일 확장자 분포 (Top Extensions)</div>
                    <span class="badge badge-neutral">대용량 파일군</span>
                </div>
                <div class="card-body">
                    <div class="chart-container">
                        <canvas id="extensionChart"></canvas>
                    </div>
                </div>
            </div>
        </div>

        <!-- Project Status Cards -->
        <div class="card" style="margin-top: 28px;">
            <div class="card-header">
                <div class="card-title">📁 프로젝트별 미마이그레이션 첨부파일 현황표</div>
                <span class="badge badge-info">1,872건 전수 분석</span>
            </div>
            <div class="card-body">
                <div class="proj-status-grid">
'''

for proj in sorted_projs:
    cnt = proj_counts[proj]
    size_mb = proj_sizes[proj]
    size_gb = round(size_mb / 1024, 2)
    html_content += f'''
                    <div class="proj-card">
                        <div class="proj-card-top">
                            <span class="proj-name">{proj}</span>
                            <span class="badge badge-danger">{cnt:,}건</span>
                        </div>
                        <div style="font-size: 13px; color: var(--text-dark); margin-top: 4px;">
                            <strong>용량:</strong> {size_gb} GB ({size_mb:,} MB)
                        </div>
                        <div style="font-size: 11.5px; color: var(--text-muted);">
                            전체 미이관 파일의 {round(cnt / data_1872['total_count'] * 100, 1)}% 비중
                        </div>
                    </div>
'''

html_content += f'''
                </div>
            </div>
        </div>

        <!-- SECTION: 1,872 Interactive Table -->
        <div class="card" id="table-section">
            <div class="card-header">
                <div class="card-title">
                    <span>📋 미마이그레이션 첨부파일 전체 리스트 (총 1,872건)</span>
                    <span class="badge badge-danger">124.77 GB</span>
                </div>
                <div>
                    <button class="btn btn-primary" onclick="download1872CSV()">📥 엑셀용 CSV 다운로드</button>
                </div>
            </div>
            <div class="card-body">
                <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 14px;">
                    Jira 온프레미스 DB의 <code>fileattachment</code> 테이블과 JCMA 마이그레이션 완료 추적 테이블(<code>AO_6FF49D_MIGRATED_FILE</code>)을 대조하여
                    <strong>클라우드로 정상 이전되지 않은 모든 첨부파일 1,872건의 정확한 이슈 키와 파일명 목록</strong>입니다.
                </p>

                <!-- Table Controls -->
                <div class="table-controls">
                    <div class="search-box">
                        <span class="search-icon">🔍</span>
                        <input type="text" id="searchInput" placeholder="이슈 번호, 파일명, 프로젝트, 확장자, 작성자로 실시간 검색..." onkeyup="filterAndPaginate()">
                    </div>

                    <div class="filter-group">
                        <span style="font-size: 12px; font-weight: 700; color: var(--text-muted);">프로젝트:</span>
                        <button class="filter-btn active" onclick="setProjFilter('ALL', this)">ALL (1,872)</button>
                        <button class="filter-btn" onclick="setProjFilter('CQIM', this)">CQIM (992)</button>
                        <button class="filter-btn" onclick="setProjFilter('CAQW', this)">CAQW (676)</button>
                        <button class="filter-btn" onclick="setProjFilter('GENESISP', this)">GENESISP (106)</button>
                        <button class="filter-btn" onclick="setProjFilter('JGVSTAR25', this)">JGVSTAR25 (78)</button>
                        <button class="filter-btn" onclick="setProjFilter('ECSC', this)">ECSC (15)</button>
                        <button class="filter-btn" onclick="setProjFilter('SPATEST', this)">SPATEST (4)</button>
                        <button class="filter-btn" onclick="setProjFilter('RPSPEC', this)">RPSPEC (1)</button>
                    </div>
                </div>

                <!-- Table -->
                <div class="table-responsive">
                    <table id="attachmentsTable">
                        <thead>
                            <tr>
                                <th onclick="sortTable(0)">#</th>
                                <th onclick="sortTable(1)">프로젝트</th>
                                <th onclick="sortTable(2)">이슈 번호</th>
                                <th onclick="sortTable(3)">첨부파일 ID</th>
                                <th onclick="sortTable(4)">파일명</th>
                                <th onclick="sortTable(5)">확장자</th>
                                <th onclick="sortTable(6)">파일 크기</th>
                                <th onclick="sortTable(7)">등록 일시</th>
                                <th onclick="sortTable(8)">작성자</th>
                            </tr>
                        </thead>
                        <tbody id="tableBody">
                            <!-- Rendered by JavaScript for fast pagination -->
                        </tbody>
                    </table>
                </div>

                <!-- Pagination Bar -->
                <div class="pagination-bar">
                    <div id="pageInfo">1 - 50 / 총 1,872건</div>
                    <div class="page-btns">
                        <button class="btn btn-secondary" style="padding: 4px 10px; font-size: 12px;" onclick="prevPage()">◀ 이전</button>
                        <button class="btn btn-secondary" style="padding: 4px 10px; font-size: 12px;" onclick="nextPage()">다음 ▶</button>
                    </div>
                </div>

            </div>
        </div>

    </div>

    <!-- JavaScript Data and Logic -->
    <script>
        const allItems = {json.dumps(data_1872['items'], ensure_ascii=False)};
        let filteredItems = allItems;
        let currentPage = 1;
        const pageSize = 50;
        let currentProj = 'ALL';

        window.addEventListener('DOMContentLoaded', () => {{
            initCharts();
            renderTable();
        }});

        function initCharts() {{
            // Project Bar Chart
            const pCtx = document.getElementById('projectChart');
            if (pCtx && window.Chart) {{
                new Chart(pCtx, {{
                    type: 'bar',
                    data: {{
                        labels: {json.dumps(sorted_projs)},
                        datasets: [
                            {{
                                label: '미마이그레이션 건수 (건)',
                                data: {json.dumps([proj_counts[p] for p in sorted_projs])},
                                backgroundColor: '#de350b',
                                yAxisID: 'y'
                            }},
                            {{
                                label: '용량 (GB)',
                                data: {json.dumps([round(proj_sizes[p]/1024, 2) for p in sorted_projs])},
                                backgroundColor: '#0052cc',
                                yAxisID: 'y1'
                            }}
                        ]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                        scales: {{
                            y: {{ type: 'linear', position: 'left', title: {{ display: true, text: '건수' }} }},
                            y1: {{ type: 'linear', position: 'right', grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: 'GB' }} }}
                        }}
                    }}
                }});
            }}

            // Extension Doughnut
            const eCtx = document.getElementById('extensionChart');
            if (eCtx && window.Chart) {{
                const extLabels = {json.dumps(list(data_1872['top_extensions'].keys())[:8])};
                const extData = {json.dumps(list(data_1872['top_extensions'].values())[:8])};
                new Chart(eCtx, {{
                    type: 'doughnut',
                    data: {{
                        labels: extLabels,
                        datasets: [{{
                            data: extData,
                            backgroundColor: ['#0052cc', '#ffab00', '#de350b', '#00875a', '#6554c0', '#00b8d9', '#ff5630', '#36b37e']
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {{ legend: {{ position: 'bottom' }} }}
                    }}
                }});
            }}
        }}

        function filterAndPaginate() {{
            const query = document.getElementById('searchInput').value.toLowerCase().trim();
            filteredItems = allItems.filter(item => {{
                const matchProj = (currentProj === 'ALL' || item.project === currentProj);
                const matchQuery = !query || 
                    item.issue_key.toLowerCase().includes(query) ||
                    item.file_name.toLowerCase().includes(query) ||
                    item.attachment_id.includes(query) ||
                    item.author.toLowerCase().includes(query) ||
                    item.extension.toLowerCase().includes(query);
                return matchProj && matchQuery;
            }});

            currentPage = 1;
            renderTable();
        }}

        function setProjFilter(proj, btn) {{
            currentProj = proj;
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            filterAndPaginate();
        }}

        function renderTable() {{
            const tbody = document.getElementById('tableBody');
            const total = filteredItems.length;
            const start = (currentPage - 1) * pageSize;
            const end = Math.min(start + pageSize, total);
            const pageSlice = filteredItems.slice(start, end);

            let html = '';
            pageSlice.forEach((item, idx) => {{
                const sizeFmt = item.file_size_mb >= 1 ? `${{item.file_size_mb}} MB` : `${{Math.round(item.file_size_bytes/1024)}} KB`;
                const sizeColor = item.file_size_mb >= 100 ? '#de350b; font-weight:700;' : '#172b4d;';
                html += `
                    <tr>
                        <td>${{start + idx + 1}}</td>
                        <td><span class="badge badge-project">${{item.project}}</span></td>
                        <td>
                            <strong class="code-tag">${{item.issue_key}}</strong>
                            <button class="copy-btn" onclick="copyText('${{item.issue_key}}', this)">📋</button>
                        </td>
                        <td><code>${{item.attachment_id}}</code></td>
                        <td>
                            <span style="font-weight: 600; color: #172b4d;">${{escapeHtml(item.file_name)}}</span>
                            <button class="copy-btn" onclick="copyText('${{item.file_name.replace(/'/g, "\\\\\\'")}}', this)">📋</button>
                        </td>
                        <td><span class="badge badge-neutral">.${{item.extension}}</span></td>
                        <td style="color: ${{sizeColor}}">${{sizeFmt}}</td>
                        <td style="font-size: 11.5px; color: var(--text-muted);">${{item.created_date.replace('T', ' ')}}</td>
                        <td><code>${{item.author}}</code></td>
                    </tr>
                `;
            }});

            tbody.innerHTML = html || '<tr><td colspan="9" style="text-align: center; padding: 24px;">검색 결과가 없습니다.</td></tr>';
            document.getElementById('pageInfo').innerText = total > 0 ? `${{start + 1}} - ${{end}} / 총 ${{total.toLocaleString()}}건` : '0건';
        }}

        function prevPage() {{
            if (currentPage > 1) {{
                currentPage--;
                renderTable();
            }}
        }}

        function nextPage() {{
            const totalPages = Math.ceil(filteredItems.length / pageSize);
            if (currentPage < totalPages) {{
                currentPage++;
                renderTable();
            }}
        }}

        function copyText(text, btn) {{
            navigator.clipboard.writeText(text).then(() => {{
                const orig = btn.innerText;
                btn.innerText = '✓';
                setTimeout(() => btn.innerText = orig, 1200);
            }});
        }}

        function escapeHtml(str) {{
            return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }}

        function download1872CSV() {{
            window.location.href = 'jcma_unmigrated_attachments_1872.csv';
        }}
    </script>
</body>
</html>
'''

with open('jcma_migration_report.html', 'w', encoding='utf-8') as f:
    f.write(html_content)

with open('index.html', 'w', encoding='utf-8') as f:
    f.write(html_content)

print("Updated jcma_migration_report.html and index.html with 1872 dataset successfully!")
