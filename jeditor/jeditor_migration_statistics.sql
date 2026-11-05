SELECT 
    ym AS ym_period,
    COUNT(DISTINCT created_id) AS created_count,
    COUNT(DISTINCT updated_id) AS updated_count
FROM (
    SELECT 
        DATE_FORMAT(i.created, '%Y-%m') AS ym,
        i.id AS created_id,
        CAST(NULL AS UNSIGNED) AS updated_id
    FROM jiraissue i
    JOIN project p ON i.project = p.id
    LEFT JOIN jiraaction ja ON ja.issueid = i.id AND ja.actiontype = 'comment'
    LEFT JOIN customfieldvalue cfv ON cfv.issue = i.id
    WHERE p.pkey IN ('AAMSW', 'ACQT', 'ACQT2', 'ACQT3', 'AIAGENT', 'AODCS', 'ASST', 'BDD', 'BMSRSEV', 'BMSRSEVMST', 'C0207AICC', 'CARLIFE', 'CSMR', 'CTDTASK', 'DPMS', 'EFOREST', 'EPSD', 'EPSDV', 'ETD', 'EVSPTO', 'GA', 'GQCB', 'GQHR', 'HACSDEV', 'HBMS', 'HCRMHUB', 'HDOTPRD001', 'HETDA', 'HEVENT', 'HKMCKMS', 'HMCIBAT', 'HMCSA', 'HMETCEUOTA', 'HMGMA0TRA', 'HMGMAACS', 'HMGPR', 'HMIECPD', 'HMIEECD', 'HMJIM', 'HMJSOP', 'HMJVR', 'HMTCPASS01', 'HTWO', 'INBF', 'INCIDENT', 'INCSMS', 'INTPT', 'JIDW', 'KEP', 'KIACPO', 'KIAPIMS', 'KIAVIK', 'LLX3CCU2', 'MCAS', 'MECMS', 'MEEI', 'MEHM', 'MEVS', 'MRP', 'MYG', 'NBRNDPRJ', 'PLAN1FEB', 'PM00', 'QDHR', 'ROBS', 'SDFACS', 'SMEFORK', 'SMELEARN', 'SPQT', 'SWPRJ', 'SWQR', 'TECHSTRAT', 'TJNA', 'TJUCJ01', 'TQAG', 'UISP', 'UPPR', 'VAIPCTL', 'VAISIHMI', 'VCSQ', 'WEISS', 'WHD', 'YCHB', 'YNAQ', 'YNKP', 'YNSQ', 'YSAA', 'YSAB', 'YSAH', 'YSEC', 'SAMSV4', 'GW', 'P231789', 'KNGCCBT', 'HCLOUDREQU')
      AND (i.archived IS NULL OR i.archived != 'Y')
      AND (
        i.description LIKE '%jeditor%'
        OR i.environment LIKE '%jeditor%'
        OR ja.actionbody LIKE '%jeditor%'
        OR cfv.textvalue LIKE '%jeditor%'
        OR cfv.stringvalue LIKE '%jeditor%'
      )
      AND i.created IS NOT NULL

    UNION ALL

    SELECT 
        DATE_FORMAT(i.updated, '%Y-%m') AS ym,
        CAST(NULL AS UNSIGNED) AS created_id,
        i.id AS updated_id
    FROM jiraissue i
    JOIN project p ON i.project = p.id
    LEFT JOIN jiraaction ja ON ja.issueid = i.id AND ja.actiontype = 'comment'
    LEFT JOIN customfieldvalue cfv ON cfv.issue = i.id
    WHERE p.pkey IN ('AAMSW', 'ACQT', 'ACQT2', 'ACQT3', 'AIAGENT', 'AODCS', 'ASST', 'BDD', 'BMSRSEV', 'BMSRSEVMST', 'C0207AICC', 'CARLIFE', 'CSMR', 'CTDTASK', 'DPMS', 'EFOREST', 'EPSD', 'EPSDV', 'ETD', 'EVSPTO', 'GA', 'GQCB', 'GQHR', 'HACSDEV', 'HBMS', 'HCRMHUB', 'HDOTPRD001', 'HETDA', 'HEVENT', 'HKMCKMS', 'HMCIBAT', 'HMCSA', 'HMETCEUOTA', 'HMGMA0TRA', 'HMGMAACS', 'HMGPR', 'HMIECPD', 'HMIEECD', 'HMJIM', 'HMJSOP', 'HMJVR', 'HMTCPASS01', 'HTWO', 'INBF', 'INCIDENT', 'INCSMS', 'INTPT', 'JIDW', 'KEP', 'KIACPO', 'KIAPIMS', 'KIAVIK', 'LLX3CCU2', 'MCAS', 'MECMS', 'MEEI', 'MEHM', 'MEVS', 'MRP', 'MYG', 'NBRNDPRJ', 'PLAN1FEB', 'PM00', 'QDHR', 'ROBS', 'SDFACS', 'SMEFORK', 'SMELEARN', 'SPQT', 'SWPRJ', 'SWQR', 'TECHSTRAT', 'TJNA', 'TJUCJ01', 'TQAG', 'UISP', 'UPPR', 'VAIPCTL', 'VAISIHMI', 'VCSQ', 'WEISS', 'WHD', 'YCHB', 'YNAQ', 'YNKP', 'YNSQ', 'YSAA', 'YSAB', 'YSAH', 'YSEC', 'SAMSV4', 'GW', 'P231789', 'KNGCCBT', 'HCLOUDREQU')
      AND (i.archived IS NULL OR i.archived != 'Y')
      AND (
        i.description LIKE '%jeditor%'
        OR i.environment LIKE '%jeditor%'
        OR ja.actionbody LIKE '%jeditor%'
        OR cfv.textvalue LIKE '%jeditor%'
        OR cfv.stringvalue LIKE '%jeditor%'
      )
      AND i.updated IS NOT NULL
) events
GROUP BY ym
ORDER BY ym_period;
