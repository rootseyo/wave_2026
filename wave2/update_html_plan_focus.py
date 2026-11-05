# -*- coding: utf-8 -*-

with open('/Users/sean/work/hmc_wave/wave2/jcma_migration_report.html', 'r', encoding='utf-8') as f:
    html = f.read()

target_anchor = '<div class="container">'
plan_box = """    <div class="container">

        <!-- 26Wave2-JIRA-T1-1 Plan Overview Summary Box -->
        <div style="background: white; border-radius: var(--radius-md); padding: 22px 26px; margin-bottom: 24px; border: 1px solid var(--border); box-shadow: var(--shadow-sm);">
            <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 14px; margin-bottom: 18px; flex-wrap: wrap; gap: 10px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <span style="background: #0052cc; color: white; font-size: 12px; font-weight: 800; padding: 4px 8px; border-radius: 4px;">TARGET PLAN</span>
                    <span style="font-size: 18px; font-weight: 700; color: var(--text-dark);">마이그레이션 플랜: <code>26Wave2-JIRA-T1-1</code> 단독 결과 리포트</span>
                </div>
                <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                    <span class="badge badge-neutral" style="font-size: 12px; padding: 4px 8px;">세션 ID: 85e18200b33684534d0404013cb4f736accb8c24</span>
                    <span class="badge badge-info" style="font-size: 12px; padding: 4px 8px;">⏱️ 소요시간: 37시간 30분 (2,250분)</span>
                </div>
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 18px;">
                <div style="background: #fafbfc; padding: 14px 16px; border-radius: 8px; border: 1px solid #ebecf0;">
                    <div style="font-size: 11.5px; color: var(--text-muted); font-weight: 700; text-transform: uppercase;">플랜 대상 규모</div>
                    <div style="font-size: 22px; font-weight: 800; color: var(--text-dark); margin: 4px 0;">624개 프로젝트</div>
                    <div style="font-size: 12px; color: var(--text-muted);">총 약 33 TB 대상</div>
                </div>
                <div style="background: #e3fcef; padding: 14px 16px; border-radius: 8px; border: 1px solid #abf5d1;">
                    <div style="font-size: 11.5px; color: #006644; font-weight: 700; text-transform: uppercase;">100% 정상 이관 프로젝트</div>
                    <div style="font-size: 22px; font-weight: 800; color: #006644; margin: 4px 0;">617개 (98.9%)</div>
                    <div style="font-size: 12px; color: #006644;">첨부파일 오류 0건 완료</div>
                </div>
                <div style="background: #deebff; padding: 14px 16px; border-radius: 8px; border: 1px solid #b3d4ff;">
                    <div style="font-size: 11.5px; color: #0747a6; font-weight: 700; text-transform: uppercase;">용량 기준 성공률</div>
                    <div style="font-size: 22px; font-weight: 800; color: #0052cc; margin: 4px 0;">99.62%</div>
                    <div style="font-size: 12px; color: #0747a6;">미이관: 124.77 GB (0.38%)</div>
                </div>
                <div style="background: #ffebe6; padding: 14px 16px; border-radius: 8px; border: 1px solid #ffbdad;">
                    <div style="font-size: 11.5px; color: #bf2600; font-weight: 700; text-transform: uppercase;">미완료 발생 프로젝트</div>
                    <div style="font-size: 22px; font-weight: 800; color: #de350b; margin: 4px 0;">7개 (1.1%)</div>
                    <div style="font-size: 12px; color: #bf2600;">CQIM, CAQW 2개사 99.9%</div>
                </div>
                <div style="background: #e3fcef; padding: 14px 16px; border-radius: 8px; border: 1px solid #abf5d1;">
                    <div style="font-size: 11.5px; color: #006644; font-weight: 700; text-transform: uppercase;">온프레미스 디스크 원본</div>
                    <div style="font-size: 22px; font-weight: 800; color: #006644; margin: 4px 0;">영구 유실 0건</div>
                    <div style="font-size: 12px; color: #006644;">1,872건 전량 보존 (재전송 가능)</div>
                </div>
            </div>
        </div>"""

if target_anchor in html:
    html = html.replace(target_anchor, plan_box, 1)
    with open('/Users/sean/work/hmc_wave/wave2/jcma_migration_report.html', 'w', encoding='utf-8') as f:
        f.write(html)
    with open('/Users/sean/work/hmc_wave/wave2/index.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print("Successfully updated jcma_migration_report.html and index.html!")
else:
    print("Target anchor not found!")
