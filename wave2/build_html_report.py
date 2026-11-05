# -*- coding: utf-8 -*-
import json
import csv
import os

with open('parsed_migration_data.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# 1. Export CSV of failed attachments
csv_path = 'failed_attachments_list.csv'
with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['번호', '프로젝트', '이슈 키', '첨부파일 ID', '파일명', '확장자', '오류 코드', '타임아웃(초)', '발생 일시', '실패 원인'])
    for item in data['failed_attachments']:
        writer.writerow([
            item['id'],
            item['project'],
            item['issue_key'],
            item['attachment_id'],
            item['file_name'],
            item['extension'],
            item['code'],
            item['timeout_sec'],
            item['timestamp'],
            item['full_reason']
        ])

print("CSV exported to", csv_path)

# 2. Build HTML Report
html_content = f'''<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JCMA 마이그레이션 진단 & 첨부파일 분석 리포트 - {data['migration_name']}</title>
    <!-- Chart.js via CDN with local fallback -->
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
            padding: 0;
            margin: 0;
        }}

        /* Header */
        header {{
            background: linear-gradient(135deg, #091e42 0%, #172b4d 100%);
            color: #ffffff;
            padding: 32px 40px;
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
            font-size: 14px;
            padding: 6px 12px;
            border-radius: 6px;
            letter-spacing: 0.5px;
            text-transform: uppercase;
        }}

        h1 {{
            font-size: 24px;
            font-weight: 700;
            color: #ffffff;
            letter-spacing: -0.3px;
        }}

        .subtitle {{
            color: #b3bac5;
            font-size: 13px;
            margin-top: 4px;
            display: flex;
            gap: 16px;
            flex-wrap: wrap;
        }}

        .subtitle span {{
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }}

        .header-actions {{
            display: flex;
            gap: 10px;
        }}

        .btn {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 9px 16px;
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

        /* Container */
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 32px 30px;
        }}

        /* Status Banner */
        .status-banner {{
            background: var(--danger-bg);
            border-left: 5px solid var(--danger);
            border-radius: var(--radius-md);
            padding: 16px 20px;
            margin-bottom: 28px;
            display: flex;
            align-items: flex-start;
            gap: 14px;
            box-shadow: var(--shadow-sm);
        }}

        .status-banner-icon {{
            color: var(--danger);
            font-size: 24px;
            line-height: 1;
            margin-top: 2px;
        }}

        .status-banner h3 {{
            font-size: 16px;
            color: #bf2600;
            font-weight: 700;
            margin-bottom: 4px;
        }}

        .status-banner p {{
            font-size: 14px;
            color: #42526e;
        }}

        /* KPI Cards Grid */
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 20px;
            margin-bottom: 32px;
        }}

        .kpi-card {{
            background: var(--bg-card);
            border-radius: var(--radius-md);
            padding: 22px;
            box-shadow: var(--shadow-sm);
            border: 1px solid var(--border);
            position: relative;
            overflow: hidden;
            transition: transform 0.2s, box-shadow 0.2s;
        }}

        .kpi-card:hover {{
            transform: translateY(-2px);
            box-shadow: var(--shadow-md);
        }}

        .kpi-card.critical {{
            border-top: 4px solid var(--danger);
        }}

        .kpi-card.warning {{
            border-top: 4px solid var(--warning);
        }}

        .kpi-card.info {{
            border-top: 4px solid var(--info);
        }}

        .kpi-card.success {{
            border-top: 4px solid var(--success);
        }}

        .kpi-title {{
            font-size: 12px;
            font-weight: 700;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        .kpi-value {{
            font-size: 34px;
            font-weight: 800;
            color: var(--text-dark);
            line-height: 1.1;
            margin-bottom: 6px;
        }}

        .kpi-value.critical {{
            color: var(--danger);
        }}

        .kpi-value.warning {{
            color: #d97008;
        }}

        .kpi-desc {{
            font-size: 12px;
            color: var(--text-muted);
        }}

        /* Charts Row */
        .charts-row {{
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 24px;
            margin-bottom: 32px;
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
            margin-bottom: 32px;
            overflow: hidden;
        }}

        .card-header {{
            padding: 18px 24px;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: #fafbfc;
        }}

        .card-title {{
            font-size: 16px;
            font-weight: 700;
            color: var(--text-dark);
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .card-body {{
            padding: 24px;
        }}

        .chart-container {{
            position: relative;
            height: 280px;
            width: 100%;
        }}

        /* Interactive Table Section */
        .table-controls {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
            flex-wrap: wrap;
            gap: 12px;
        }}

        .search-box {{
            position: relative;
            min-width: 280px;
            flex-grow: 1;
            max-width: 450px;
        }}

        .search-box input {{
            width: 100%;
            padding: 9px 14px 9px 36px;
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            font-size: 13px;
            transition: border-color 0.2s;
        }}

        .search-box input:focus {{
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 2px var(--primary-light);
        }}

        .search-icon {{
            position: absolute;
            left: 12px;
            top: 50%;
            transform: translateY(-50%);
            color: var(--text-muted);
            font-size: 14px;
        }}

        .filter-group {{
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
            align-items: center;
        }}

        .filter-btn {{
            padding: 6px 12px;
            border: 1px solid var(--border);
            background: white;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            cursor: pointer;
            transition: all 0.2s ease;
        }}

        .filter-btn.active {{
            background: var(--primary);
            color: white;
            border-color: var(--primary);
        }}

        .table-responsive {{
            overflow-x: auto;
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            text-align: left;
        }}

        thead th {{
            background: #fafbfc;
            padding: 12px 14px;
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

        thead th .sort-icon {{
            margin-left: 4px;
            font-size: 10px;
            color: #97a0af;
        }}

        tbody tr {{
            border-bottom: 1px solid var(--border);
            transition: background 0.15s;
        }}

        tbody tr:hover {{
            background: #f7f9fc;
        }}

        tbody td {{
            padding: 12px 14px;
            vertical-align: middle;
        }}

        /* Badges */
        .badge {{
            display: inline-flex;
            align-items: center;
            gap: 4px;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.3px;
        }}

        .badge-danger {{
            background: var(--danger-bg);
            color: var(--danger);
        }}

        .badge-warning {{
            background: var(--warning-bg);
            color: #974f00;
        }}

        .badge-info {{
            background: var(--info-bg);
            color: var(--primary);
        }}

        .badge-neutral {{
            background: #ebecf0;
            color: #42526e;
        }}

        .badge-project {{
            background: #eae6ff;
            color: #403294;
            font-weight: 700;
        }}

        .code-tag {{
            font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
            background: #f4f5f7;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 12px;
            color: #172b4d;
            border: 1px solid #dfe1e6;
        }}

        .copy-btn {{
            background: transparent;
            border: none;
            cursor: pointer;
            color: #6b778c;
            padding: 2px 4px;
            border-radius: 3px;
            transition: all 0.2s;
            font-size: 11px;
        }}

        .copy-btn:hover {{
            background: #ebecf0;
            color: var(--primary);
        }}

        /* Missing attachments list cards */
        .missing-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 16px;
            margin-top: 16px;
        }}

        .missing-card {{
            background: #fafbfc;
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            padding: 16px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            border-left: 4px solid var(--warning);
        }}

        .missing-card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        .missing-card-title {{
            font-size: 15px;
            font-weight: 700;
            color: var(--text-dark);
        }}

        .missing-card-count {{
            font-size: 20px;
            font-weight: 800;
            color: #d97008;
        }}

        /* Solution / Root Cause Section */
        .solution-card {{
            border-left: 5px solid var(--primary);
            background: #ffffff;
            margin-bottom: 20px;
            border-radius: var(--radius-sm);
            box-shadow: var(--shadow-sm);
            border-top: 1px solid var(--border);
            border-right: 1px solid var(--border);
            border-bottom: 1px solid var(--border);
            padding: 20px;
        }}

        .solution-title {{
            font-size: 16px;
            font-weight: 700;
            color: var(--primary-dark);
            margin-bottom: 12px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .code-block {{
            background: #091e42;
            color: #f4f5f7;
            padding: 14px 18px;
            border-radius: 6px;
            font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
            font-size: 12.5px;
            line-height: 1.5;
            overflow-x: auto;
            margin: 12px 0;
            position: relative;
        }}

        .code-block .code-copy-btn {{
            position: absolute;
            top: 8px;
            right: 8px;
            background: rgba(255,255,255,0.15);
            border: 1px solid rgba(255,255,255,0.25);
            color: white;
            font-size: 11px;
            padding: 3px 8px;
            border-radius: 4px;
            cursor: pointer;
        }}

        .code-block .code-copy-btn:hover {{
            background: rgba(255,255,255,0.3);
        }}

        .steps-list {{
            margin-left: 20px;
            margin-top: 8px;
            margin-bottom: 12px;
        }}

        .steps-list li {{
            margin-bottom: 8px;
            font-size: 13.5px;
        }}

        /* Project status grid */
        .proj-status-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(310px, 1fr));
            gap: 16px;
        }}

        .proj-card {{
            background: white;
            border: 1px solid var(--border);
            border-radius: var(--radius-sm);
            padding: 18px;
            display: flex;
            flex-direction: column;
            gap: 10px;
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

        .proj-stats-row {{
            display: flex;
            gap: 12px;
            font-size: 12px;
        }}

        .stat-pill {{
            padding: 4px 8px;
            border-radius: 4px;
            background: #f4f5f7;
            display: flex;
            gap: 6px;
            align-items: center;
        }}

        /* Raw Log Tabs */
        .log-tabs {{
            display: flex;
            gap: 4px;
            border-bottom: 1px solid var(--border);
            overflow-x: auto;
            margin-bottom: 16px;
        }}

        .log-tab {{
            padding: 8px 16px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            border: none;
            background: none;
            color: var(--text-muted);
            border-bottom: 2px solid transparent;
            white-space: nowrap;
        }}

        .log-tab.active {{
            color: var(--primary);
            border-bottom-color: var(--primary);
            background: rgba(0,82,204,0.05);
        }}

        .log-content {{
            background: #172b4d;
            color: #e6fcff;
            font-family: SFMono-Regular, Consolas, Menlo, monospace;
            font-size: 12px;
            padding: 16px;
            border-radius: var(--radius-sm);
            height: 350px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-break: break-all;
            line-height: 1.6;
        }}

        .log-line-error {{
            color: #ff8f73;
            background: rgba(222, 53, 11, 0.15);
            display: block;
            padding: 2px 4px;
            border-radius: 2px;
        }}

        .log-line-warning {{
            color: #ffc400;
            background: rgba(255, 171, 0, 0.15);
            display: block;
            padding: 2px 4px;
            border-radius: 2px;
        }}

        /* Modal */
        .modal-overlay {{
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: rgba(9, 30, 66, 0.54);
            display: none;
            justify-content: center;
            align-items: center;
            z-index: 1000;
            backdrop-filter: blur(2px);
        }}

        .modal-overlay.active {{
            display: flex;
        }}

        .modal-content {{
            background: white;
            border-radius: var(--radius-md);
            max-width: 800px;
            width: 90%;
            max-height: 85vh;
            overflow-y: auto;
            padding: 28px;
            box-shadow: var(--shadow-lg);
            position: relative;
        }}

        .modal-close {{
            position: absolute;
            top: 20px;
            right: 20px;
            background: none;
            border: none;
            font-size: 20px;
            color: var(--text-muted);
            cursor: pointer;
        }}

        .modal-close:hover {{
            color: var(--text-dark);
        }}

        /* Print styles */
        @media print {{
            header {{
                position: static;
                padding: 16px 20px;
            }}
            .header-actions, .table-controls, .copy-btn, .filter-group {{
                display: none !important;
            }}
            .container {{
                padding: 10px;
                max-width: 100%;
            }}
            .card {{
                box-shadow: none;
                border: 1px solid #ccc;
                page-break-inside: avoid;
            }}
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
                        <span><strong>작업 플랜:</strong> {data['migration_name']}</span>
                        <span>&bull;</span>
                        <span><strong>세션 ID:</strong> <span class="code-tag" style="background: rgba(255,255,255,0.15); color: #fff; border-color: rgba(255,255,255,0.3);">{data['plan_id'][:12]}...</span></span>
                        <span>&bull;</span>
                        <span><strong>작업 일시:</strong> 2026-09-23 18:29 ~ 2026-09-24 11:49</span>
                        <span>&bull;</span>
                        <span><strong>리포트 생성:</strong> {data['report_date']}</span>
                    </div>
                </div>
            </div>
            <div class="header-actions">
                <button class="btn btn-outline" onclick="window.print()">
                    🖨️ PDF / 인쇄
                </button>
                <button class="btn btn-primary" onclick="downloadCSV()">
                    📥 실패 첨부파일 CSV 다운로드
                </button>
            </div>
        </div>
    </header>

    <div class="container">

        <!-- Critical Banner -->
        <div class="status-banner">
            <div class="status-banner-icon">⚠️</div>
            <div>
                <h3>총 152건의 첨부파일 마이그레이션 결손 발견 (주의 필요)</h3>
                <p>
                    전체 7개 대상 프로젝트 중 <strong>10건의 첨부파일 Export 실패(JCMA 401 - MySQL Connection Timeout)</strong>와 
                    <strong>142건의 서버 디스크 내 물리 파일 유실(JCMA 402 - Missing Attachments)</strong>이 확인되었습니다.
                    해당 첨부파일들은 Jira Cloud로 이전되지 않았으므로 조치 가이드에 따라 재시도 및 무결성 점검이 필요합니다.
                </p>
            </div>
        </div>

        <!-- KPI Summary Cards -->
        <div class="kpi-grid">
            <div class="kpi-card critical">
                <div class="kpi-title">
                    <span>총 미마이그레이션 첨부파일</span>
                    <span class="badge badge-danger">152건</span>
                </div>
                <div class="kpi-value critical">{data['total_problem_attachments']}</div>
                <div class="kpi-desc">DB 타임아웃 10건 + 파일 소실 142건</div>
            </div>

            <div class="kpi-card critical">
                <div class="kpi-title">
                    <span>DB 타임아웃 실패 (JCMA 401)</span>
                    <span class="badge badge-danger">재시도 가능</span>
                </div>
                <div class="kpi-value critical">{data['total_failed_export']}</div>
                <div class="kpi-desc">MySQL wait_timeout 초과 (상세 목록 확인 가능)</div>
            </div>

            <div class="kpi-card warning">
                <div class="kpi-title">
                    <span>물리 파일 유실 (JCMA 402)</span>
                    <span class="badge badge-warning">스토리지 점검 필요</span>
                </div>
                <div class="kpi-value warning">{data['total_missing']}</div>
                <div class="kpi-desc">Jira Server 디스크 내 파일 없음 (4개 프로젝트)</div>
            </div>

            <div class="kpi-card info">
                <div class="kpi-title">
                    <span>영향 프로젝트</span>
                    <span class="badge badge-info">100% 영향</span>
                </div>
                <div class="kpi-value">7 / 7</div>
                <div class="kpi-desc">CAQW, CQIM, ECSC, GENESISP, JGVSTAR25, RPSPEC, SPATEST</div>
            </div>
        </div>

        <!-- Charts Dashboard -->
        <div class="charts-row">
            <div class="card" style="margin-bottom: 0;">
                <div class="card-header">
                    <div class="card-title">📊 프로젝트별 미마이그레이션 첨부파일 현황</div>
                    <span class="badge badge-neutral">JCMA 401 + JCMA 402</span>
                </div>
                <div class="card-body">
                    <div class="chart-container">
                        <canvas id="projectBarChart"></canvas>
                    </div>
                </div>
            </div>

            <div class="card" style="margin-bottom: 0;">
                <div class="card-header">
                    <div class="card-title">🍩 실패 유형 분포</div>
                    <span class="badge badge-neutral">유형별 비율</span>
                </div>
                <div class="card-body">
                    <div class="chart-container">
                        <canvas id="typeDoughnutChart"></canvas>
                    </div>
                </div>
            </div>
        </div>

        <!-- SECTION 1: JCMA 401 Detail Table -->
        <div class="card" id="failed-attachments-section" style="margin-top: 32px;">
            <div class="card-header">
                <div class="card-title">
                    <span>📋 DB 타임아웃 Export 실패 첨부파일 리스트 (총 {data['total_failed_export']}건)</span>
                    <span class="badge badge-danger">JCMA 401</span>
                </div>
                <div>
                    <button class="btn btn-secondary" onclick="downloadCSV()">CSV 내보내기</button>
                </div>
            </div>
            <div class="card-body">
                <p style="font-size: 13.5px; color: var(--text-muted); margin-bottom: 16px;">
                    아래 첨부파일들은 Jira Server 데이터베이스의 <code>wait_timeout</code>(유휴 커넥션 자동 단절) 설정으로 인해 JCMA가 메타데이터를 읽어오는 도중 커넥션이 종료되어 Export에 실패한 목록입니다.
                    파일 자체의 손상이 아니므로 <strong>JCMA 콘솔에서 "Retry failed attachments"</strong>를 실행하면 즉시 복구할 수 있습니다.
                </p>

                <!-- Table Controls -->
                <div class="table-controls">
                    <div class="search-box">
                        <span class="search-icon">🔍</span>
                        <input type="text" id="searchInput" placeholder="이슈 키, 파일명, 프로젝트, 확장자 등으로 검색..." onkeyup="filterTable()">
                    </div>

                    <div class="filter-group">
                        <span style="font-size: 12px; font-weight: 700; color: var(--text-muted);">프로젝트 필터:</span>
                        <button class="filter-btn active" onclick="setProjectFilter('ALL', this)">ALL (10)</button>
                        <button class="filter-btn" onclick="setProjectFilter('CQIM', this)">CQIM (3)</button>
                        <button class="filter-btn" onclick="setProjectFilter('JGVSTAR25', this)">JGVSTAR25 (3)</button>
                        <button class="filter-btn" onclick="setProjectFilter('ECSC', this)">ECSC (1)</button>
                        <button class="filter-btn" onclick="setProjectFilter('GENESISP', this)">GENESISP (1)</button>
                        <button class="filter-btn" onclick="setProjectFilter('RPSPEC', this)">RPSPEC (1)</button>
                        <button class="filter-btn" onclick="setProjectFilter('SPATEST', this)">SPATEST (1)</button>
                    </div>
                </div>

                <!-- Table -->
                <div class="table-responsive">
                    <table id="attachmentsTable">
                        <thead>
                            <tr>
                                <th onclick="sortTable(0)"># <span class="sort-icon">▲▼</span></th>
                                <th onclick="sortTable(1)">프로젝트 <span class="sort-icon">▲▼</span></th>
                                <th onclick="sortTable(2)">이슈 키 <span class="sort-icon">▲▼</span></th>
                                <th onclick="sortTable(3)">첨부파일 ID <span class="sort-icon">▲▼</span></th>
                                <th onclick="sortTable(4)">파일명 <span class="sort-icon">▲▼</span></th>
                                <th onclick="sortTable(5)">확장자 <span class="sort-icon">▲▼</span></th>
                                <th onclick="sortTable(6)">타임아웃(초) <span class="sort-icon">▲▼</span></th>
                                <th onclick="sortTable(7)">오류 발생 일시 <span class="sort-icon">▲▼</span></th>
                                <th>조치 및 원인</th>
                            </tr>
                        </thead>
                        <tbody id="attachmentsTableBody">
'''

for item in data['failed_attachments']:
    ext_badge_color = "badge-neutral"
    if item['extension'] in ['zip', 'gz']:
        ext_badge_color = "badge-info"
    elif item['extension'] in ['png', 'jpg', 'jpeg']:
        ext_badge_color = "badge-warning"
    elif item['extension'] in ['mp4']:
        ext_badge_color = "badge-danger"

    html_content += f'''
                            <tr data-project="{item['project']}" data-search="{item['project']} {item['issue_key']} {item['file_name']} {item['attachment_id']} {item['extension']}">
                                <td><strong>{item['id']}</strong></td>
                                <td><span class="badge badge-project">{item['project']}</span></td>
                                <td>
                                    <span class="code-tag">{item['issue_key']}</span>
                                    <button class="copy-btn" onclick="copyText('{item['issue_key']}', this)" title="복사">📋</button>
                                </td>
                                <td><code>{item['attachment_id']}</code></td>
                                <td>
                                    <strong style="color: #172b4d;">{item['file_name']}</strong>
                                    <button class="copy-btn" onclick="copyText('{item['file_name']}', this)" title="복사">📋</button>
                                </td>
                                <td><span class="badge {ext_badge_color}">.{item['extension']}</span></td>
                                <td>
                                    <span style="font-weight: 700; color: #d97008;">{item['timeout_sec']}s</span>
                                    <span style="font-size: 11px; color: var(--text-muted);">({round(item['timeout_sec']/60, 1)}분)</span>
                                </td>
                                <td style="white-space: nowrap; font-size: 12px; color: var(--text-muted);">{item['timestamp'].split('.')[0]}</td>
                                <td>
                                    <button class="btn btn-secondary" style="padding: 4px 10px; font-size: 11px;" onclick="openErrorModal({item['id'] - 1})">
                                        오류 상세 보기
                                    </button>
                                </td>
                            </tr>
'''

html_content += f'''
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- SECTION 2: JCMA 402 Missing Attachments -->
        <div class="card">
            <div class="card-header">
                <div class="card-title">
                    <span>⚠️ 서버 디스크 내 물리 파일 유실 현황 (총 {data['total_missing']}건)</span>
                    <span class="badge badge-warning">JCMA 402</span>
                </div>
                <span class="badge badge-neutral">4개 프로젝트 해당</span>
            </div>
            <div class="card-body">
                <p style="font-size: 13.5px; color: var(--text-muted); margin-bottom: 16px;">
                    아래 수치는 Jira 데이터베이스(<code>fileattachment</code> 테이블)에는 첨부파일 메타데이터가 존재하지만,
                    Jira 서버의 파일 저장소(<code>$JIRA_HOME/data/attachments/</code>)에 실제 파일이 존재하지 않는 경우입니다.
                    JCMA는 파일이 없는 상태를 감지하여 마이그레이션에서 건너뛰고 경고 로그를 기록했습니다.
                </p>

                <div class="missing-grid">
                    <div class="missing-card">
                        <div class="missing-card-header">
                            <span class="badge badge-project">CAQW</span>
                            <span class="missing-card-count">63개</span>
                        </div>
                        <div style="font-size: 13px; color: var(--text-muted);">
                            <strong>상태:</strong> 물리 파일 부재로 Cloud 이전 제외<br>
                            <strong>로그 시각:</strong> 2026-09-23 18:29:25
                        </div>
                    </div>

                    <div class="missing-card">
                        <div class="missing-card-header">
                            <span class="badge badge-project">CQIM</span>
                            <span class="missing-card-count">54개</span>
                        </div>
                        <div style="font-size: 13px; color: var(--text-muted);">
                            <strong>상태:</strong> 물리 파일 부재로 Cloud 이전 제외<br>
                            <strong>로그 시각:</strong> 2026-09-24 03:53:27
                        </div>
                    </div>

                    <div class="missing-card">
                        <div class="missing-card-header">
                            <span class="badge badge-project">GENESISP</span>
                            <span class="missing-card-count">22개</span>
                        </div>
                        <div style="font-size: 13px; color: var(--text-muted);">
                            <strong>상태:</strong> 물리 파일 부재로 Cloud 이전 제외<br>
                            <strong>로그 시각:</strong> 2026-09-24 03:37:44
                        </div>
                    </div>

                    <div class="missing-card">
                        <div class="missing-card-header">
                            <span class="badge badge-project">SPATEST</span>
                            <span class="missing-card-count">3개</span>
                        </div>
                        <div style="font-size: 13px; color: var(--text-muted);">
                            <strong>상태:</strong> 물리 파일 부재로 Cloud 이전 제외<br>
                            <strong>로그 시각:</strong> 2026-09-24 05:01:18
                        </div>
                    </div>
                </div>

                <!-- SQL for finding missing attachments in Jira DB -->
                <div style="margin-top: 24px;">
                    <h4 style="font-size: 14px; font-weight: 700; margin-bottom: 8px;">🔍 Jira DB에서 해당 프로젝트의 첨부파일 목록 확인용 SQL</h4>
                    <div class="code-block">
                        <button class="code-copy-btn" onclick="copyCode(this)">SQL 복사</button>
<pre><code>-- 4개 프로젝트의 첨부파일 메타데이터 및 디스크 저장 경로 확인 쿼리
SELECT 
    p.pkey AS project_key,
    CONCAT(p.pkey, '-', i.issuenum) AS issue_key,
    fa.ID AS attachment_id,
    fa.FILENAME AS file_name,
    fa.FILESIZE AS file_size_bytes,
    fa.CREATED AS upload_date,
    -- 표준 지라 첨부파일 파일시스템 물리 경로 매핑
    CONCAT('/var/atlassian/application-data/jira/data/attachments/', p.pkey, '/10000/', p.pkey, '-', i.issuenum, '/', fa.ID) AS expected_path
FROM fileattachment fa
JOIN jiraissue i ON fa.issueid = i.ID
JOIN project p ON i.PROJECT = p.ID
WHERE p.pkey IN ('CAQW', 'CQIM', 'GENESISP', 'SPATEST')
ORDER BY p.pkey, fa.ID DESC;</code></pre>
                    </div>
                </div>
            </div>
        </div>

        <!-- SECTION 3: Root Cause Analysis & Remediation Plan -->
        <div class="card">
            <div class="card-header">
                <div class="card-title">🛠️ 문제 원인 심층 분석 및 조치 가이드 (Remediation Plan)</div>
                <span class="badge badge-info">Atlassian Best Practice</span>
            </div>
            <div class="card-body">

                <!-- 1. JCMA 401 Solution -->
                <div class="solution-card">
                    <div class="solution-title">
                        <span>1. [JCMA 401] MySQL Connection wait_timeout 해결 및 재시도 방법 (10건)</span>
                    </div>
                    <p style="font-size: 13.5px; color: #344563; margin-bottom: 12px;">
                        <strong>원인 메커니즘:</strong> JCMA가 첨부파일의 메타데이터 및 이슈 정보를 읽어올 때, 대용량 파일 또는 대기열 처리로 인해 10분(약 600초) 이상 데이터베이스 유휴 상태가 지속되었습니다. 
                        이때 MySQL의 <code>wait_timeout</code> 설정에 의해 커넥션이 강제로 끊어지면서 <code>GenericDataSourceException</code>이 발생했습니다.
                    </p>

                    <h5 style="font-size: 13px; font-weight: 700; color: #172b4d; margin-top: 10px;">[조치 1] Jira DB(MySQL) 설정 최적화 (<code>my.cnf</code>)</h5>
                    <div class="code-block">
                        <button class="code-copy-btn" onclick="copyCode(this)">설정 복사</button>
<pre><code>[mysqld]
# JCMA 대용량 데이터 및 장시간 Export 작업 시 커넥션 단절 방지 (8시간으로 증설)
wait_timeout = 28800
interactive_timeout = 28800
net_read_timeout = 600
net_write_timeout = 600
max_allowed_packet = 256M</code></pre>
                    </div>

                    <h5 style="font-size: 13px; font-weight: 700; color: #172b4d; margin-top: 14px;">[조치 2] Jira <code>dbconfig.xml</code> 커넥션 유효성 검사 활성화</h5>
                    <div class="code-block">
                        <button class="code-copy-btn" onclick="copyCode(this)">XML 복사</button>
<pre><code>&lt;!-- /var/atlassian/application-data/jira/dbconfig.xml 내 &lt;jdbc-datasource&gt; 추가 설정 --&gt;
&lt;connection-properties&gt;autoReconnect=true;tcpKeepAlive=true&lt;/connection-properties&gt;
&lt;validation-query&gt;SELECT 1&lt;/validation-query&gt;
&lt;test-while-idle&gt;true&lt;/test-while-idle&gt;
&lt;min-evictable-idle-time-millis&gt;60000&lt;/min-evictable-idle-time-millis&gt;
&lt;time-between-eviction-runs-millis&gt;30000&lt;/time-between-eviction-runs-millis&gt;</code></pre>
                    </div>

                    <h5 style="font-size: 13px; font-weight: 700; color: #172b4d; margin-top: 14px;">[조치 3] 실패한 첨부파일 10건만 선별 재마이그레이션 (전체 재작업 불필요)</h5>
                    <ol class="steps-list">
                        <li>Jira Server 관리자 메뉴 > <strong>시스템 > 클라우드 마이그레이션 어시스턴트(JCMA)</strong>로 이동합니다.</li>
                        <li>완료된 플랜 목록에서 <code>26Wave2-JIRA-T1-1</code>을 확인합니다.</li>
                        <li>플랜 우측의 <code>...</code> (메뉴) 버튼을 누르고 <strong>"Retry failed attachments (실패한 첨부 파일 재시도)"</strong>를 클릭합니다.</li>
                        <li>전체 이슈나 프로젝트를 다시 올릴 필요 없이, 타임아웃으로 실패했던 10개 파일만 Cloud로 고속 재전송됩니다.</li>
                    </ol>
                </div>

                <!-- 2. JCMA 402 Solution -->
                <div class="solution-card" style="border-left-color: var(--warning);">
                    <div class="solution-title" style="color: #974f00;">
                        <span>2. [JCMA 402] Missing Attachments (물리 파일 부재) 점검 및 복구 방법 (142건)</span>
                    </div>
                    <p style="font-size: 13.5px; color: #344563; margin-bottom: 12px;">
                        <strong>원인 메커니즘:</strong> Jira 설치 이후 파일시스템(EFS/NFS/로컬 디스크) 이전, 백업 복원 누락, 파일 권한 문제 또는 과거 디스크 정리 과정에서 실제 파일이 삭제되어 DB 레코드만 남아있는 고아 레코드(Orphan record) 상태입니다.
                    </p>

                    <ol class="steps-list">
                        <li><strong>Jira Instance Health Check 실행:</strong> Jira 관리자 메뉴 > 시스템 > 지원 도구(Support Tools) > <strong>Instance Health Check</strong>에서 <em>"Attachments integrity"</em> 항목을 실행합니다.</li>
                        <li><strong>스토리지 백업 대조:</strong> 과거 전체 백업 파일 또는 스냅샷에서 <code>CAQW</code>(63건), <code>CQIM</code>(54건), <code>GENESISP</code>(22건), <code>SPATEST</code>(3건)의 디렉터리를 복원합니다.</li>
                        <li><strong>복구가 불가능한 경우 (유실 확정 시):</strong> 이미 과거부터 원본 파일이 없던 경우이므로, 비즈니스 영향도를 검토한 후 클라우드 마이그레이션을 그대로 마무리하고 완료 처리할 수 있습니다. (이슈 본문 및 댓글 메타데이터는 클라우드로 정상 마이그레이션됨)</li>
                    </ol>
                </div>

            </div>
        </div>

        <!-- SECTION 4: Project Summary Cards -->
        <div class="card">
            <div class="card-header">
                <div class="card-title">📁 프로젝트별 종합 진단 결과 (7개 프로젝트)</div>
            </div>
            <div class="card-body">
                <div class="proj-status-grid">
'''

for proj, stat in data['project_stats'].items():
    status_badge = '<span class="badge badge-danger">조치 필요</span>' if stat['total_issues'] > 0 else '<span class="badge badge-success">정상</span>'
    html_content += f'''
                    <div class="proj-card">
                        <div class="proj-card-top">
                            <span class="proj-name">{proj}</span>
                            {status_badge}
                        </div>
                        <div class="proj-stats-row">
                            <div class="stat-pill">
                                <span>Export 실패:</span>
                                <strong style="color: var(--danger);">{stat['export_errors']}건</strong>
                            </div>
                            <div class="stat-pill">
                                <span>물리파일 누락:</span>
                                <strong style="color: var(--warning);">{stat['missing_count']}건</strong>
                            </div>
                        </div>
                        <div style="font-size: 12px; color: var(--text-muted); margin-top: 4px;">
                            <strong>총 결손:</strong> {stat['total_issues']}건 | 
                            <strong>로그 시각:</strong> {stat['first_ts'].split('.')[0] if stat['first_ts'] else '-'}
                        </div>
                    </div>
'''

html_content += f'''
                </div>
            </div>
        </div>

        <!-- SECTION 5: Post-Migration Report Summary -->
        <div class="card">
            <div class="card-header">
                <div class="card-title">📑 Global Entities & Jira Post-Migration Report 요약</div>
                <span class="badge badge-neutral">Summary CSV 기준</span>
            </div>
            <div class="card-body">
                <p style="font-size: 13px; color: var(--text-muted); margin-bottom: 14px;">
                    JCMA 결과 보고서 디렉터리의 <code>Summary_Post-migration report</code>에 기록된 글로벌 객체 및 설정 요소의 마이그레이션 결과입니다.
                </p>
                <div class="table-responsive">
                    <table>
                        <thead>
                            <tr>
                                <th>엔티티 종류 (Entity Type)</th>
                                <th>대상 프로젝트</th>
                                <th>마이그레이션 대상 수</th>
                                <th>성공 이관</th>
                                <th>부분 손실 (Partial)</th>
                                <th>미이관 (Not migrated)</th>
                            </tr>
                        </thead>
                        <tbody>
'''

for row in data['summary_data']:
    html_content += f'''
                            <tr>
                                <td><strong>{row['type']}</strong></td>
                                <td>{row['project']}</td>
                                <td>{row['num_mig']}</td>
                                <td>{row['success']}</td>
                                <td>{row['partial_loss']}</td>
                                <td>{row['not_mig']}</td>
                            </tr>
'''

html_content += f'''
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- SECTION 6: Raw Log Explorer -->
        <div class="card">
            <div class="card-header">
                <div class="card-title">📜 원시 로그 탐색기 (Raw Log Explorer)</div>
                <button class="btn btn-secondary" onclick="copyCurrentLog()">현재 로그 복사</button>
            </div>
            <div class="card-body">
                <div class="log-tabs" id="logTabContainer">
'''

for idx, (proj, content) in enumerate(data['raw_logs'].items()):
    active_cls = "active" if idx == 0 else ""
    html_content += f'''
                    <button class="log-tab {active_cls}" onclick="switchLog('{proj}', this)">{proj}.log</button>
'''

html_content += f'''
                </div>
                <div class="log-content" id="logViewer"></div>
            </div>
        </div>

    </div>

    <!-- Error Detail Modal -->
    <div class="modal-overlay" id="errorModal">
        <div class="modal-content">
            <button class="modal-close" onclick="closeErrorModal()">&times;</button>
            <h3 id="modalTitle" style="font-size: 18px; margin-bottom: 12px; color: var(--danger);">오류 상세 내용</h3>
            <div id="modalMeta" style="margin-bottom: 16px; font-size: 13px;"></div>
            <h4 style="font-size: 13px; font-weight: 700; margin-bottom: 6px;">발생 예외 전문 (Full Stack Trace / Exception):</h4>
            <div class="code-block" style="max-height: 300px; overflow-y: auto;">
                <button class="code-copy-btn" onclick="copyModalReason()">내용 복사</button>
                <pre><code id="modalReason" style="white-space: pre-wrap; word-break: break-all;"></code></pre>
            </div>
            <div style="margin-top: 16px; text-align: right;">
                <button class="btn btn-secondary" onclick="closeErrorModal()">닫기</button>
            </div>
        </div>
    </div>

    <!-- JavaScript logic -->
    <script>
        const failedAttachments = {json.dumps(data['failed_attachments'], ensure_ascii=False)};
        const rawLogs = {json.dumps(data['raw_logs'], ensure_ascii=False)};
        let currentFilterProject = 'ALL';
        let currentLogProject = '{list(data['raw_logs'].keys())[0] if data['raw_logs'] else ''}';

        // Initialize Charts
        window.addEventListener('DOMContentLoaded', () => {{
            initCharts();
            renderLog(currentLogProject);
        }});

        function initCharts() {{
            const projectNames = {json.dumps(list(data['project_stats'].keys()))};
            const exportData = {json.dumps([data['project_stats'][p]['export_errors'] for p in data['project_stats']])};
            const missingData = {json.dumps([data['project_stats'][p]['missing_count'] for p in data['project_stats']])};

            // Project Bar Chart
            const ctxBar = document.getElementById('projectBarChart');
            if (ctxBar && window.Chart) {{
                new Chart(ctxBar, {{
                    type: 'bar',
                    data: {{
                        labels: projectNames,
                        datasets: [
                            {{
                                label: 'Export 실패 (JCMA 401)',
                                data: exportData,
                                backgroundColor: '#de350b',
                                borderRadius: 4
                            }},
                            {{
                                label: '물리파일 누락 (JCMA 402)',
                                data: missingData,
                                backgroundColor: '#ffab00',
                                borderRadius: 4
                            }}
                        ]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                        scales: {{
                            x: {{ stacked: true, grid: {{ display: false }} }},
                            y: {{ stacked: true, beginAtZero: true }}
                        }},
                        plugins: {{
                            legend: {{ position: 'top' }}
                        }}
                    }}
                }});
            }}

            // Type Doughnut Chart
            const ctxDoughnut = document.getElementById('typeDoughnutChart');
            if (ctxDoughnut && window.Chart) {{
                new Chart(ctxDoughnut, {{
                    type: 'doughnut',
                    data: {{
                        labels: ['물리파일 유실 (JCMA 402: 142건)', 'DB 타임아웃 Export 실패 (JCMA 401: 10건)'],
                        datasets: [{{
                            data: [{data['total_missing']}, {data['total_failed_export']}],
                            backgroundColor: ['#ffab00', '#de350b']
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {{
                            legend: {{ position: 'bottom' }}
                        }}
                    }}
                }});
            }}
        }}

        // Table Filtering
        function filterTable() {{
            const query = document.getElementById('searchInput').value.toLowerCase();
            const rows = document.querySelectorAll('#attachmentsTableBody tr');

            rows.forEach(row => {{
                const proj = row.getAttribute('data-project');
                const searchData = row.getAttribute('data-search').toLowerCase();

                const matchesProj = (currentFilterProject === 'ALL' || proj === currentFilterProject);
                const matchesQuery = (!query || searchData.includes(query));

                if (matchesProj && matchesQuery) {{
                    row.style.display = '';
                }} else {{
                    row.style.display = 'none';
                }}
            }});
        }}

        function setProjectFilter(proj, btn) {{
            currentFilterProject = proj;
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            filterTable();
        }}

        // Table Sorting
        let sortDirections = {{}};
        function sortTable(colIndex) {{
            const table = document.getElementById('attachmentsTable');
            const tbody = table.querySelector('tbody');
            const rows = Array.from(tbody.querySelectorAll('tr'));
            
            const isAsc = !sortDirections[colIndex];
            sortDirections[colIndex] = isAsc;

            rows.sort((a, b) => {{
                const aVal = a.cells[colIndex].innerText.trim();
                const bVal = b.cells[colIndex].innerText.trim();

                const aNum = parseFloat(aVal.replace(/[^0-9.-]/g, ''));
                const bNum = parseFloat(bVal.replace(/[^0-9.-]/g, ''));

                if (!isNaN(aNum) && !isNaN(bNum)) {{
                    return isAsc ? aNum - bNum : bNum - aNum;
                }}
                return isAsc ? aVal.localeCompare(bVal, 'ko') : bVal.localeCompare(aVal, 'ko');
            }});

            rows.forEach(r => tbody.appendChild(r));
        }}

        // Copy Helper
        function copyText(text, btn) {{
            navigator.clipboard.writeText(text).then(() => {{
                const original = btn.innerText;
                btn.innerText = '✓';
                setTimeout(() => btn.innerText = original, 1200);
            }});
        }}

        function copyCode(btn) {{
            const code = btn.parentElement.querySelector('code').innerText;
            navigator.clipboard.writeText(code).then(() => {{
                const original = btn.innerText;
                btn.innerText = '✓ 복사됨';
                setTimeout(() => btn.innerText = original, 1500);
            }});
        }}

        // Modal Logic
        let activeModalReason = '';
        function openErrorModal(index) {{
            const item = failedAttachments[index];
            if (!item) return;

            document.getElementById('modalTitle').innerText = `[${{item.project}}] ${{item.file_name}} (이슈: ${{item.issue_key}})`;
            document.getElementById('modalMeta').innerHTML = `
                <div><strong>첨부파일 ID:</strong> <code>${{item.attachment_id}}</code> &bull; 
                <strong>타임아웃 대기시간:</strong> ${{item.timeout_sec}}초 &bull; 
                <strong>오류 코드:</strong> <span class="badge badge-danger">${{item.code}}</span> &bull; 
                <strong>발생 일시:</strong> ${{item.timestamp}}</div>
            `;
            document.getElementById('modalReason').innerText = item.full_reason;
            activeModalReason = item.full_reason;
            document.getElementById('errorModal').classList.add('active');
        }}

        function closeErrorModal() {{
            document.getElementById('errorModal').classList.remove('active');
        }}

        function copyModalReason() {{
            navigator.clipboard.writeText(activeModalReason).then(() => {{
                alert('에러 전문이 클립보드에 복사되었습니다.');
            }});
        }}

        // Log Viewer Logic
        function switchLog(proj, tabBtn) {{
            currentLogProject = proj;
            document.querySelectorAll('.log-tab').forEach(t => t.classList.remove('active'));
            tabBtn.classList.add('active');
            renderLog(proj);
        }}

        function renderLog(proj) {{
            const viewer = document.getElementById('logViewer');
            const raw = rawLogs[proj] || '(로그 내용 없음)';
            
            // Highlight ERROR / WARNING
            const lines = raw.split('\\n');
            const formatted = lines.map(line => {{
                if (line.includes('ERROR')) {{
                    return `<span class="log-line-error">${{escapeHtml(line)}}</span>`;
                }} else if (line.includes('WARNING')) {{
                    return `<span class="log-line-warning">${{escapeHtml(line)}}</span>`;
                }}
                return escapeHtml(line);
            }}).join('\\n');

            viewer.innerHTML = formatted;
        }}

        function copyCurrentLog() {{
            const raw = rawLogs[currentLogProject] || '';
            navigator.clipboard.writeText(raw).then(() => {{
                alert(`${{currentLogProject}}.log 원문이 클립보드에 복사되었습니다.`);
            }});
        }}

        function escapeHtml(str) {{
            return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        }}

        // CSV Download Logic
        function downloadCSV() {{
            const rows = [
                ['번호', '프로젝트', '이슈 키', '첨부파일 ID', '파일명', '확장자', '오류 코드', '타임아웃(초)', '발생 일시', '실패 원인']
            ];
            failedAttachments.forEach(item => {{
                rows.push([
                    item.id,
                    item.project,
                    item.issue_key,
                    item.attachment_id,
                    `"${{item.file_name.replace(/"/g, '""')}}"`,
                    item.extension,
                    item.code,
                    item.timeout_sec,
                    item.timestamp,
                    `"${{item.full_reason.replace(/"/g, '""')}}"`
                ]);
            }});

            const csvContent = '\\uFEFF' + rows.map(e => e.join(',')).join('\\n');
            const blob = new Blob([csvContent], {{ type: 'text/csv;charset=utf-8;' }});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.setAttribute('href', url);
            a.setAttribute('download', 'jcma_failed_attachments_{data['migration_name']}.csv');
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        }}
    </script>
</body>
</html>
'''

with open('jcma_migration_report.html', 'w', encoding='utf-8') as f:
    f.write(html_content)

print("Generated jcma_migration_report.html successfully!")
