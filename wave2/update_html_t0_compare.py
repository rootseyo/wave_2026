# -*- coding: utf-8 -*-

with open('/Users/sean/work/hmc_wave/wave2/jcma_migration_report.html', 'r', encoding='utf-8') as f:
    html = f.read()

old_snippet = """<span class="badge badge-info" style="font-size: 12px; padding: 4px 8px;">⏱️ 소요시간: 37시간 30분 (2,250분)</span>"""
new_snippet = """<span class="badge badge-info" style="font-size: 12px; padding: 4px 8px;">⏱️ 소요시간: 37시간 30분 (2,250분)</span>
                    <span class="badge badge-warning" style="font-size: 12px; padding: 4px 8px; font-weight: 700;">T0-1 대비 +7시간 23분 소요 (+24.5% 느림 / 33TB 완결 전송)</span>"""

if old_snippet in html:
    html = html.replace(old_snippet, new_snippet, 1)

old_card = """                <div style="background: #deebff; padding: 14px 16px; border-radius: 8px; border: 1px solid #b3d4ff;">
                    <div style="font-size: 11.5px; color: #0747a6; font-weight: 700; text-transform: uppercase;">용량 기준 성공률</div>
                    <div style="font-size: 22px; font-weight: 800; color: #0052cc; margin: 4px 0;">99.62%</div>
                    <div style="font-size: 12px; color: #0747a6;">미이관: 124.77 GB (0.38%)</div>
                </div>"""

new_card = """                <div style="background: #deebff; padding: 14px 16px; border-radius: 8px; border: 1px solid #b3d4ff;">
                    <div style="font-size: 11.5px; color: #0747a6; font-weight: 700; text-transform: uppercase;">작업 소요시간 (T0 대비)</div>
                    <div style="font-size: 20px; font-weight: 800; color: #0052cc; margin: 4px 0;">37h 30m <span style="font-size: 13px; color: #de350b;">(+7h 23m)</span></div>
                    <div style="font-size: 11px; color: #0747a6;">T0-1(30h 7m) 대비 +24.5% 느림</div>
                </div>"""

if old_card in html:
    html = html.replace(old_card, new_card, 1)

with open('/Users/sean/work/hmc_wave/wave2/jcma_migration_report.html', 'w', encoding='utf-8') as f:
    f.write(html)
with open('/Users/sean/work/hmc_wave/wave2/index.html', 'w', encoding='utf-8') as f:
    f.write(html)

print("HTML report successfully updated with T0 vs T1 comparison!")
