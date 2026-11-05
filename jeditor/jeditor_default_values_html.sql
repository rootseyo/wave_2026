-- ==============================================================================
-- Jira Data Center / MySQL: 커스텀 필드 HTML 기본값(Default Value) 설정 점검(Audit) 쿼리
--
-- [역할 분리 안내]
-- 1. 기존 이슈 데이터(본문/댓글/커스텀필드 값)의 HTML -> ADF 마이그레이션은
--    `jeditor_mysql_queries.sql`에서 프로젝트 단위로 일괄 추출·변환합니다.
-- 2. 이 쿼리는 이슈 테이블(jiraissue, customfieldvalue)을 중복 스캔하지 않고,
--    오직 Jira 필드 설정 메타데이터(genericconfiguration)만 조회하여
--    "어떤 커스텀 필드의 기본값(DefaultValue)에 HTML/JEditor 양식이 걸려있는지"를
--    1초 이내에 찾아내는 관리자 설정 변경·점검용(Audit) 쿼리입니다.
-- ==============================================================================

SELECT DISTINCT
    p.pkey AS project_key,
    p.pname AS project_name,
    fc.fieldid AS field_id,
    cf.cfname AS field_name,
    cf.customfieldtypekey AS field_type,
    fcs.id AS context_id,
    fcs.configname AS context_name,
    CASE WHEN cc.project IS NULL THEN 'Global' ELSE 'Project' END AS context_scope,
    fc.id AS field_configuration_id,
    fc.configname AS field_configuration_name,
    gc.id AS default_value_id,
    CASE
        WHEN LOWER(gc.xmlvalue) LIKE '%jeditor_file_provider%' THEN 'Attachment'
        ELSE 'HTML/Text'
    END AS detection_type,
    gc.xmlvalue AS default_value_xml
FROM project p
JOIN configurationcontext cc ON (cc.project = p.id OR (cc.project IS NULL AND cc.projectcategory IS NULL))
JOIN fieldconfigscheme fcs ON fcs.id = cc.fieldconfigscheme
JOIN fieldconfigschemeissuetype fcsi ON fcsi.fieldconfigscheme = fcs.id
JOIN fieldconfiguration fc ON fc.id = fcsi.fieldconfiguration AND fc.fieldid = fcs.fieldid
JOIN genericconfiguration gc ON gc.datakey = CAST(fc.id AS CHAR) AND gc.datatype = 'DefaultValue'
JOIN customfield cf ON fc.fieldid = CONCAT('customfield_', cf.id)
WHERE p.pkey IN (
-- PROJECT_KEYS_START
    '__NO_PROJECT_SELECTED__'
-- PROJECT_KEYS_END
)
  AND gc.xmlvalue IS NOT NULL
  -- 1차 고속 LIKE 필터 (무거운 REGEXP 연산 최소화)
  AND (
      gc.xmlvalue LIKE '%<%'
      OR gc.xmlvalue LIKE '%&lt;%'
      OR LOWER(gc.xmlvalue) LIKE '%jeditor%'
  )
  -- 2차 정밀 HTML 태그 / JEditor 마크업 판별
  AND (
      LOWER(gc.xmlvalue) LIKE '%jeditor%'
      OR LOWER(gc.xmlvalue) REGEXP '(<|&lt;)[/ ]*(p|div|span|table|tr|td|th|br|ul|ol|li|h1|h2|h3|h4|h5|h6|a|img|b|strong|i|em|u|font)([[:space:]/>]|&gt;)'
  )
ORDER BY project_key, field_id, field_configuration_id, default_value_id;

