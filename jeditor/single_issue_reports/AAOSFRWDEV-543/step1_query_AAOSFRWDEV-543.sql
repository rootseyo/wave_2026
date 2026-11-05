WITH target_projects AS (
    SELECT 'AAOSFRWDEV' AS pkey
)
-- 1. Description (본문)
SELECT
    p.pkey AS project_key,
    p.pname AS project_name,
    CONCAT(p.pkey, '-', i.issuenum) AS issue_key,
    ist.pname AS issue_status,
    'description' AS field_id,
    'Description' AS field_name,
    CASE
        WHEN i.description LIKE '%jeditor_file_provider%' THEN 'Attachment'
        ELSE 'Renderer/Text'
    END AS detection_type,
    i.description AS content,
    '' AS migration_status
FROM target_projects tp
JOIN project p ON p.pkey = tp.pkey
JOIN jiraissue i ON i.project = p.id
LEFT JOIN issuestatus ist ON ist.id = i.issuestatus
WHERE (i.archived IS NULL OR i.archived <> 'Y')
  AND i.issuenum = 543
  AND i.description IS NOT NULL
  AND TRIM(i.description) <> ''

UNION ALL

-- 2. Environment (환경)
SELECT
    p.pkey AS project_key,
    p.pname AS project_name,
    CONCAT(p.pkey, '-', i.issuenum) AS issue_key,
    ist.pname AS issue_status,
    'environment' AS field_id,
    'Environment' AS field_name,
    CASE
        WHEN i.environment LIKE '%jeditor_file_provider%' THEN 'Attachment'
        ELSE 'Renderer/Text'
    END AS detection_type,
    i.environment AS content,
    '' AS migration_status
FROM target_projects tp
JOIN project p ON p.pkey = tp.pkey
JOIN jiraissue i ON i.project = p.id
LEFT JOIN issuestatus ist ON ist.id = i.issuestatus
WHERE (i.archived IS NULL OR i.archived <> 'Y')
  AND i.issuenum = 543
  AND i.environment IS NOT NULL
  AND TRIM(i.environment) <> ''

UNION ALL

-- 3. Comments (댓글)
SELECT
    p.pkey AS project_key,
    p.pname AS project_name,
    CONCAT(p.pkey, '-', i.issuenum) AS issue_key,
    ist.pname AS issue_status,
    'comment' AS field_id,
    'Comment' AS field_name,
    CASE
        WHEN ja.actionbody LIKE '%jeditor_file_provider%' THEN 'Attachment'
        ELSE 'Renderer/Text'
    END AS detection_type,
    ja.actionbody AS content,
    '' AS migration_status
FROM target_projects tp
JOIN project p ON p.pkey = tp.pkey
JOIN jiraissue i ON i.project = p.id
JOIN jiraaction ja ON ja.issueid = i.id
LEFT JOIN issuestatus ist ON ist.id = i.issuestatus
WHERE (i.archived IS NULL OR i.archived <> 'Y')
  AND i.issuenum = 543
  AND ja.actiontype = 'comment'
  AND ja.actionbody IS NOT NULL
  AND (ja.actionbody LIKE '%jeditor%' OR ja.actionbody LIKE '%<%')

UNION ALL

-- 4. Custom Fields (커스텀 필드)
SELECT
    p.pkey AS project_key,
    p.pname AS project_name,
    CONCAT(p.pkey, '-', i.issuenum) AS issue_key,
    ist.pname AS issue_status,
    CONCAT('customfield_', cf.id) AS field_id,
    cf.cfname AS field_name,
    CASE
        WHEN cfv.textvalue LIKE '%jeditor_file_provider%'
          OR cfv.stringvalue LIKE '%jeditor_file_provider%'
        THEN 'Attachment'
        ELSE 'Renderer/Text'
    END AS detection_type,
    COALESCE(cfv.textvalue, cfv.stringvalue) AS content,
    '' AS migration_status
FROM target_projects tp
JOIN project p ON p.pkey = tp.pkey
JOIN jiraissue i ON i.project = p.id
JOIN customfieldvalue cfv ON cfv.issue = i.id
JOIN customfield cf ON cf.id = cfv.customfield
LEFT JOIN issuestatus ist ON ist.id = i.issuestatus
WHERE (i.archived IS NULL OR i.archived <> 'Y')
  AND i.issuenum = 543
  AND (
      (cfv.textvalue IS NOT NULL AND TRIM(cfv.textvalue) <> '')
      OR (cf.customfieldtypekey LIKE '%jeditor%')
      OR (cfv.stringvalue IS NOT NULL AND (cfv.stringvalue LIKE '%jeditor%' OR cfv.stringvalue LIKE '%<%'))
  )
ORDER BY project_key, issue_key;
