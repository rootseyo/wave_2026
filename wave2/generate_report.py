# -*- coding: utf-8 -*-
import glob
import re
import json
import os

logs_dir = "2026-09-28-09-34-26wave2-jira-t1-1-logs"
post_mig_dir = "26Wave2-JIRA-T1-1_85e18200b33684534d0404013cb4f736accb8c24_Post-migration report_Jira_2026-09-24_23-24-09"

failed_attachments = []
missing_attachments = []
raw_logs = {}
project_stats = {}

log_files = sorted(glob.glob(os.path.join(logs_dir, "*.log")))

for log_path in log_files:
    proj = os.path.basename(log_path).replace('.log', '')
    project_stats[proj] = {
        'project': proj,
        'export_errors': 0,
        'missing_count': 0,
        'total_issues': 0,
        'first_ts': None,
        'last_ts': None,
        'files': []
    }
    
    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
        raw_logs[proj] = content
        
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
            
        m_err = re.match(
            r'^([\d\-]+ [\d\:\.]+)\s+(ERROR)\s+([A-Z0-9_\-]+)\s+([\w\-]+)\s+\[mri:mig:jira:fileAttachment:(\d+)\]\s+We couldn\'t export Attachment \'([^\']+)\' \(on Issue ([A-Z0-9_\-]+)\)\.\s+Reason:\s*(.*)\s+\[(JCMA\s+\d+)\]$', 
            line
        )
        if m_err:
            ts, level, p, stage, att_id, fname, issue_key, reason, code = m_err.groups()
            timeout_match = re.search(r'The last packet successfully received from the server was ([\d\,]+) milliseconds ago', reason)
            timeout_ms_str = timeout_match.group(1) if timeout_match else 'N/A'
            timeout_sec = round(float(timeout_ms_str.replace(',', '')) / 1000, 1) if timeout_ms_str != 'N/A' else None
            ext = fname.split('.')[-1].lower() if '.' in fname else 'unknown'
            
            fa_item = {
                'id': len(failed_attachments) + 1,
                'timestamp': ts,
                'level': level,
                'project': p,
                'stage': stage,
                'attachment_id': att_id,
                'file_name': fname,
                'extension': ext,
                'issue_key': issue_key,
                'timeout_ms': timeout_ms_str,
                'timeout_sec': timeout_sec,
                'code': code,
                'reason_snippet': reason[:140] + '...',
                'full_reason': reason
            }
            failed_attachments.append(fa_item)
            project_stats[proj]['export_errors'] += 1
            project_stats[proj]['total_issues'] += 1
            project_stats[proj]['files'].append(fa_item)
            
            if not project_stats[proj]['first_ts'] or ts < project_stats[proj]['first_ts']:
                project_stats[proj]['first_ts'] = ts
            if not project_stats[proj]['last_ts'] or ts > project_stats[proj]['last_ts']:
                project_stats[proj]['last_ts'] = ts
            continue
            
        m_warn = re.match(
            r'^([\d\-]+ [\d\:\.]+)\s+(WARNING)\s+([A-Z0-9_\-]+)\s+([\w\-]+)\s+\[(.*?)\]\s+Found (\d+) missing attachments\.\s+\[(JCMA\s+\d+)\]\s*(.*)$', 
            line
        )
        if m_warn:
            ts, level, p, stage, mri, count, code, rec = m_warn.groups()
            ma_item = {
                'timestamp': ts,
                'level': level,
                'project': p,
                'stage': stage,
                'count': int(count),
                'code': code,
                'recommendation': rec
            }
            missing_attachments.append(ma_item)
            project_stats[proj]['missing_count'] += int(count)
            project_stats[proj]['total_issues'] += int(count)
            
            if not project_stats[proj]['first_ts'] or ts < project_stats[proj]['first_ts']:
                project_stats[proj]['first_ts'] = ts
            if not project_stats[proj]['last_ts'] or ts > project_stats[proj]['last_ts']:
                project_stats[proj]['last_ts'] = ts
            continue

# Read summary CSV
summary_csv_path = os.path.join(post_mig_dir, "Summary_Post-migration report_Jira_2026-09-24_23-24-09.csv")
summary_data = []
if os.path.exists(summary_csv_path):
    with open(summary_csv_path, 'r', encoding='utf-8') as f:
        for idx, line in enumerate(f):
            if idx == 0 or not line.strip():
                continue
            parts = [p.strip() for p in line.strip().split(',')]
            if len(parts) >= 6:
                summary_data.append({
                    'type': parts[0],
                    'project': parts[1] or '-',
                    'num_mig': parts[2],
                    'success': parts[3],
                    'partial_loss': parts[4],
                    'not_mig': parts[5]
                })

total_failed_export = len(failed_attachments)
total_missing = sum(m['count'] for m in missing_attachments)
total_problem_attachments = total_failed_export + total_missing

# Save parsed JSON for frontend
data_dump = {
    'failed_attachments': failed_attachments,
    'missing_attachments': missing_attachments,
    'project_stats': project_stats,
    'summary_data': summary_data,
    'raw_logs': raw_logs,
    'total_failed_export': total_failed_export,
    'total_missing': total_missing,
    'total_problem_attachments': total_problem_attachments,
    'migration_name': '26Wave2-JIRA-T1-1',
    'plan_id': '85e18200b33684534d0404013cb4f736accb8c24',
    'report_date': '2026-09-24 23:24:09'
}

with open('parsed_migration_data.json', 'w', encoding='utf-8') as f:
    json.dump(data_dump, f, ensure_ascii=False, indent=2)

print("Parsed data saved successfully.")
