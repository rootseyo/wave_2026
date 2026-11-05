-- Full context inventory: keep rows with no DefaultValue as evidence of absence.
-- Executed by default_value_policy.py with parameterized project keys.
SELECT p.pkey AS project_key, p.pname AS project_name,
    fcs.fieldid AS field_id, cf.cfname AS field_name, cf.customfieldtypekey AS field_type,
    fcs.id AS context_id, fcs.configname AS context_name,
    CASE WHEN cc.project IS NOT NULL THEN 'Project'
         WHEN cc.projectcategory IS NOT NULL THEN 'Category' ELSE 'Global' END AS context_scope,
    fcsi.issuetype AS issue_type_id,
    fc.id AS field_configuration_id, fc.configname AS field_configuration_name,
    gc.id AS default_value_id, gc.xmlvalue AS default_value_xml
FROM project p
JOIN configurationcontext cc ON cc.project=p.id OR
    (cc.project IS NULL AND cc.projectcategory IS NULL) OR
    (cc.project IS NULL AND EXISTS (
        SELECT 1 FROM nodeassociation category_link
        WHERE category_link.source_node_id=p.id AND category_link.source_node_entity='Project'
          AND category_link.sink_node_entity='ProjectCategory'
          AND category_link.sink_node_id=cc.projectcategory))
JOIN fieldconfigscheme fcs ON fcs.id=cc.fieldconfigscheme
JOIN customfield cf ON fcs.fieldid=CONCAT('customfield_', cf.id)
LEFT JOIN fieldconfigschemeissuetype fcsi ON fcsi.fieldconfigscheme=fcs.id
LEFT JOIN fieldconfiguration fc ON fc.id=fcsi.fieldconfiguration AND fc.fieldid=fcs.fieldid
LEFT JOIN genericconfiguration gc ON gc.datakey=CAST(fc.id AS CHAR) AND gc.datatype='DefaultValue'
WHERE p.pkey IN (__PROJECT_PLACEHOLDERS__);
