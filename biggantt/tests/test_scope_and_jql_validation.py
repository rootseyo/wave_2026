import unittest
from unittest.mock import patch, MagicMock
from step1_export_dc_boxes import (
    extract_scope_details,
    extract_project_keys,
    validate_jql,
    get_filter_status,
    export_boxes_for_excel,
    format_elapsed_time,
    jql_status_cache,
    filter_status_cache,
    board_status_cache,
    audit_scope_references,
    export_scope_audit,
    get_board_status,
    validate_box_projects_with_progress,
    prefetch_box_scopes,
    process_node,
    stats,
    excluded_invalid_boxes,
    project_status_cache,
    validate_connected_field,
    test_single_box,
    jira_fields_cache,
)
import tempfile
import os
import csv
from generate_step1_report import configured_project_references

class ScopeAndJqlValidationTests(unittest.TestCase):
    def setUp(self):
        jql_status_cache.clear()
        filter_status_cache.clear()
        board_status_cache.clear()
        jira_fields_cache.clear()
        stats["jql_configured_count"] = 0
        stats["jql_valid_count"] = 0
        stats["jql_warning_count"] = 0
        stats["jql_invalid_count"] = 0
        stats["jql_error_count"] = 0
        stats["jql_empty_result_count"] = 0
        stats["closed_boxes_count"] = 0
        excluded_invalid_boxes.clear()

    def test_extract_scope_details_with_all_elements(self):
        scope_data = {
            "cargo": {
                "extPlatformScopeDefinitions": [
                    {
                        "scopeOwner": {"key": "admin_user"},
                        "narrowingQuery": "status != Closed AND labels = 'migration'",
                        "scopeDefinitionElements": [
                            {"type": "JIRA_PROJECT", "url": "/rest/api/2/project/PROJ_A"},
                            {"type": "JIRA_PROJECT", "url": "/rest/api/2/project/PROJ_B"},
                            {"type": "JIRA_SAVED_FILTER", "id": 10500},
                            {"type": "JIRA_BOARD", "id": 42}
                        ]
                    }
                ]
            }
        }
        res = extract_scope_details(scope_data)
        self.assertEqual(res["project_keys"], ["PROJ_A", "PROJ_B"])
        self.assertEqual(res["narrowing_query"], "status != Closed AND labels = 'migration'")
        self.assertEqual(res["saved_filters"], ["10500"])
        self.assertEqual(res["boards"], ["42"])
        self.assertEqual(res["owner_key"], "admin_user")

        # Backward compatibility
        keys = extract_project_keys(scope_data)
        self.assertEqual(keys, ["PROJ_A", "PROJ_B"])

    def test_extract_project_reference_from_url_with_query_or_id(self):
        scope = {"cargo": {"extPlatformScopeDefinitions": [{"scopeDefinitionElements": [
            {"type": "JIRA_PROJECT", "url": "/rest/api/2/project/OLD/?expand=lead"},
            {"type": "JIRA_PROJECT", "id": 12345},
        ]}]}}
        self.assertEqual(extract_scope_details(scope)["project_keys"], ["OLD", "12345"])

    @patch("step1_export_dc_boxes.requests.post")
    def test_validate_jql_valid(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"total": 42}
        mock_post.return_value = mock_resp

        res = validate_jql("project = TEST")
        self.assertTrue(res["valid"])
        self.assertEqual(res["status"], "VALID")
        self.assertEqual(res["total_issues"], 42)
        self.assertEqual(stats["jql_valid_count"], 1)
        self.assertIs(mock_post.call_args.kwargs["json"]["validateQuery"], True)

    @patch("step1_export_dc_boxes.requests.get")
    @patch("step1_export_dc_boxes.requests.post")
    def test_validate_jql_invalid_syntax(self, mock_post, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "errorMessages": ["Field 'invalid_field' does not exist."],
            "errors": {}
        }
        mock_post.return_value = mock_resp
        probe_resp = MagicMock()
        probe_resp.status_code = 200
        mock_get.side_effect = [mock_resp, probe_resp]

        res = validate_jql("invalid_field = 123")
        self.assertFalse(res["valid"])
        self.assertEqual(res["status"], "INVALID")
        self.assertIn("invalid_field", res["message"])
        self.assertEqual(stats["jql_invalid_count"], 1)

    @patch("step1_export_dc_boxes.requests.get")
    @patch("step1_export_dc_boxes.requests.post")
    def test_bad_search_request_is_not_reported_as_invalid_jql(self, mock_post, mock_get):
        bad_request = MagicMock()
        bad_request.status_code = 400
        bad_request.json.return_value = {"errorMessages": ["Invalid search request"], "errors": {}}
        mock_post.return_value = bad_request
        mock_get.return_value = bad_request

        result = validate_jql("project = TEST")
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(stats["jql_invalid_count"], 0)

    @patch("step1_export_dc_boxes.requests.get")
    @patch("step1_export_dc_boxes.requests.post")
    def test_strict_rejection_accepted_by_relaxed_search_is_warning(self, mock_post, mock_get):
        strict = MagicMock()
        strict.status_code = 400
        strict.json.return_value = {"errorMessages": ["The value is not visible to this user."], "errors": {}}
        relaxed = MagicMock()
        relaxed.status_code = 200
        relaxed.json.return_value = {"total": 7, "warningMessages": ["The value is not visible to this user."]}
        mock_post.return_value = strict
        mock_get.return_value = relaxed

        result = validate_jql("project = TEST AND assignee = sample")
        self.assertEqual(result["status"], "WARNING")
        self.assertEqual(result["total_issues"], 7)
        self.assertEqual(stats["jql_invalid_count"], 0)
        self.assertEqual(stats["jql_warning_count"], 1)
        self.assertEqual(mock_get.call_args.kwargs["params"]["validateQuery"], "warn")

    def test_validate_jql_none(self):
        res = validate_jql("")
        self.assertTrue(res["valid"])
        self.assertEqual(res["status"], "NONE")
        self.assertEqual(stats["jql_configured_count"], 0)

    @patch("step1_export_dc_boxes.fetch_json")
    def test_get_filter_status_active(self, mock_fetch):
        mock_fetch.return_value = {
            "name": "My Filter",
            "jql": "project = TEST ORDER BY created DESC"
        }
        res = get_filter_status("10500")
        self.assertTrue(res["exists"])
        self.assertEqual(res["status"], "ACTIVE")
        self.assertEqual(res["name"], "My Filter")

    @patch("step1_export_dc_boxes.get_board_status")
    @patch("step1_export_dc_boxes.get_filter_status")
    @patch("step1_export_dc_boxes.get_project_status")
    def test_scope_audit_keeps_each_configured_reference(self, mock_project, mock_filter, mock_board):
        mock_project.side_effect = [
            {"status": "ACTIVE"}, {"status": "NOT_FOUND"}, {"status": "ACTIVE"}
        ]
        mock_filter.return_value = {"status": "ACTIVE"}
        mock_board.return_value = {"status": "NOT_FOUND_OR_INACCESSIBLE"}
        references = audit_scope_references(["A", "DELETED", "C"], ["100"], ["200"])
        self.assertEqual([(r["type"], r["id"], r["status"]) for r in references], [
            ("PROJECT", "A", "ACTIVE"),
            ("PROJECT", "DELETED", "NOT_FOUND"),
            ("PROJECT", "C", "ACTIVE"),
            ("FILTER", "100", "ACTIVE"),
            ("BOARD", "200", "NOT_FOUND_OR_INACCESSIBLE"),
        ])

    def test_report_preserves_deleted_project_after_active_scope_filtering(self):
        box = {
            "jira_projects": ["A", "C"],
            "configured_projects": ["A", "DELETED", "C"],
            "scope_references": [
                {"type": "PROJECT", "id": "A", "status": "ACTIVE"},
                {"type": "PROJECT", "id": "DELETED", "status": "NOT_FOUND"},
                {"type": "PROJECT", "id": "C", "status": "ACTIVE"},
            ],
        }
        self.assertEqual(
            [(ref["key"], ref["status"]) for ref in configured_project_references(box)],
            [("A", "ACTIVE"), ("DELETED", "NOT_FOUND"), ("C", "ACTIVE")],
        )

    @patch("step1_export_dc_boxes.get_project_status")
    def test_renamed_project_key_is_reported(self, mock_project):
        mock_project.return_value = {"status": "ACTIVE", "key": "NEW"}
        reference = audit_scope_references(["OLD"], [], [])[0]
        self.assertEqual(reference["status"], "KEY_CHANGED")
        self.assertEqual(reference["resolved_key"], "NEW")

    def test_scope_audit_export_includes_excluded_boxes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = os.path.join(tmp_dir, "scope.csv")
            export_scope_audit([{
                "boxId": "PROG-277", "boxName": "Example", "scope_status": "PROJECT_NOT_FOUND",
                "migration_selected": False, "configured_projects": ["DELETED"],
                "active_projects": [], "scope_references": [
                    {"type": "PROJECT", "id": "DELETED", "status": "NOT_FOUND"}
                ]
            }], path)
            with open(path, encoding="utf-8-sig", newline="") as source:
                row = next(csv.DictReader(source))
            self.assertEqual(row["Box ID"], "PROG-277")
            self.assertEqual(row["Migration Selected"], "False")
            self.assertEqual(row["Reference Status"], "NOT_FOUND")

    @patch("step1_export_dc_boxes.get_filter_status")
    @patch("step1_export_dc_boxes.fetch_json")
    def test_board_with_missing_saved_filter_is_reported(self, mock_fetch, mock_filter):
        mock_fetch.side_effect = [
            {"id": 200, "name": "Team Board"},
            {"filter": {"id": "300"}},
        ]
        mock_filter.return_value = {"status": "NOT_FOUND"}
        result = get_board_status("200")
        self.assertEqual(result["status"], "FILTER_NOT_FOUND_OR_INACCESSIBLE")
        self.assertEqual(result["filter_id"], "300")

    @patch("step1_export_dc_boxes.log")
    @patch("step1_export_dc_boxes.get_project_status")
    def test_box_project_progress_shows_total_and_counts(self, mock_status, mock_log):
        mock_status.side_effect = [
            {"status": "ACTIVE"}, {"status": "NOT_FOUND"}, {"status": "ACTIVE"}
        ]
        validate_box_projects_with_progress({"C", "A", "B"})
        self.assertEqual([call.args[0] for call in mock_status.call_args_list], ["A", "B", "C"])
        messages = [call.args[0] for call in mock_log.call_args_list]
        self.assertIn("Box-configured Project validation started: 0/3", messages)
        self.assertTrue(any("3/3 (100%)" in message and "not found 1" in message for message in messages))

    @patch("step1_export_dc_boxes.fetch_json")
    def test_prefetch_counts_projects_configured_on_boxes(self, mock_fetch):
        mock_fetch.return_value = {"cargo": {"extPlatformScopeDefinitions": [{
            "scopeDefinitionElements": [{"type": "JIRA_PROJECT", "url": "/project/BOX"}]
        }]}}
        root = {"boxId": "ROOT", "boxChildren": [
            {"boxId": "OPEN", "boxStatus": "IN_PROGRESS", "boxChildren": []},
            {"boxId": "CLOSED", "boxStatus": "CLOSED", "boxChildren": []},
        ]}
        cache, projects = prefetch_box_scopes(root)
        self.assertEqual(projects, {"BOX"})
        self.assertEqual(set(cache), {"OPEN", "CLOSED"})
        self.assertEqual(mock_fetch.call_count, 2)

    @patch("step1_export_dc_boxes.log")
    @patch("step1_export_dc_boxes.fetch_json")
    def test_closed_parent_and_child_with_target_projects_are_audited_but_not_migrated(self, mock_fetch, mock_log):
        hierarchy = {
            "boxId": "PROG-1", "boxName": "Closed parent", "boxStatus": "CLOSED",
            "boxChildren": [{
                "boxId": "PROG-2", "boxName": "Child", "boxStatus": "IN_PROGRESS",
                "boxChildren": [],
            }],
        }
        scope_cache = {
            "PROG-1": {"cargo": {"extPlatformScopeDefinitions": [{"scopeDefinitionElements": [{"type": "JIRA_PROJECT", "url": "/project/TEST"}]}]}},
            "PROG-2": {"cargo": {"extPlatformScopeDefinitions": [{"scopeDefinitionElements": [{"type": "JIRA_PROJECT", "url": "/project/TEST"}]}]}}
        }
        migration_boxes, scanned_boxes = [], []
        process_node(hierarchy, {"TEST"}, migration_boxes, scanned_boxes, {}, scope_cache=scope_cache)
        self.assertEqual(migration_boxes, [])
        self.assertEqual([box["effective_box_status"] for box in scanned_boxes], ["CLOSED", "CLOSED"])
        self.assertEqual(stats["closed_boxes_count"], 2)

    def test_box_without_scope_projects_is_completely_excluded(self):
        hierarchy = {
            "boxId": "PROG-EMPTY", "boxName": "Empty Scope Box", "boxStatus": "IN_PROGRESS",
            "boxChildren": []
        }
        scope_cache = {
            "PROG-EMPTY": {"cargo": {"extPlatformScopeDefinitions": []}}
        }
        migration_boxes, scanned_boxes = [], []
        process_node(hierarchy, {"TEST"}, migration_boxes, scanned_boxes, {}, scope_cache=scope_cache)
        self.assertEqual(migration_boxes, [])
        self.assertEqual(scanned_boxes, [])

    def test_export_boxes_for_excel_contains_jql_headers(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tsv_path = os.path.join(tmp_dir, "export.tsv")
            csv_path = os.path.join(tmp_dir, "export.csv")

            sample_boxes = [
                {
                    "boxId": 101,
                    "boxName": "Test Box",
                    "boxType": "Program",
                    "boxStartDate": "01/JAN/24",
                    "boxEndDate": "31/DEC/24",
                    "owner_name": "Admin",
                    "owner_email": "admin@example.com",
                    "jira_projects": ["PROJ_A"],
                    "scope_status": "VALID",
                    "narrowing_query": "status != Closed",
                    "jql_status": "VALID",
                    "jql_total_matched": 15,
                    "validation_status": "정상"
                }
            ]

            export_boxes_for_excel(sample_boxes, tsv_path, csv_path)

            self.assertTrue(os.path.exists(tsv_path))
            self.assertTrue(os.path.exists(csv_path))

            with open(csv_path, "r", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                header = next(reader)
                self.assertIn("Box 상태 (Status)", header)
                self.assertIn("JQL 설정 (Narrowing Query)", header)
                self.assertIn("JQL 검증 상태 (JQL Status)", header)
                self.assertIn("검증 상세 / 비고 (Validation Detail)", header)
                row = next(reader)
                self.assertEqual(row[1], "101")
                self.assertEqual(row[4], "OPEN (이관 대상)")
                self.assertEqual(row[10], "정상 (Active)")
                self.assertEqual(row[11], "status != Closed")
                self.assertIn("정상 (Matched: 15건)", row[12])

    @patch("step1_export_dc_boxes.validate_jql")
    def test_box_with_zero_jql_matched_is_excluded_from_migration(self, mock_validate_jql):
        project_status_cache["TEST"] = {"exists": True, "archived": False, "status": "ACTIVE"}
        mock_validate_jql.return_value = {
            "valid": True,
            "status": "VALID",
            "error": None,
            "total_issues": 0
        }
        hierarchy = {
            "boxId": "PROG-ZERO", "boxName": "Zero JQL Match Box", "boxStatus": "IN_PROGRESS",
            "boxChildren": []
        }
        scope_cache = {
            "PROG-ZERO": {
                "cargo": {
                    "extPlatformScopeDefinitions": [
                        {
                            "narrowingQuery": "project = TEST AND labels = non_existent",
                            "scopeDefinitionElements": [
                                {"type": "JIRA_PROJECT", "url": "/project/TEST"}
                            ]
                        }
                    ]
                }
            }
        }
        migration_boxes, scanned_boxes = [], []
        process_node(
            hierarchy,
            {"TEST"},
            migration_boxes,
            scanned_boxes,
            {},
            scope_cache=scope_cache
        )
        self.assertEqual(migration_boxes, [])
        self.assertEqual(len(scanned_boxes), 1)
        zero_box = scanned_boxes[0]
        self.assertTrue(zero_box["is_jql_empty"])
        self.assertFalse(zero_box["migration_selected"])
        self.assertIn("JQL 검색 결과 0건", zero_box["filter_reason"])
        self.assertEqual(zero_box["jql_total_matched"], 0)
        self.assertEqual(stats["jql_empty_result_count"], 1)
        self.assertEqual(len(excluded_invalid_boxes), 1)
        self.assertEqual(excluded_invalid_boxes[0]["boxId"], "PROG-ZERO")

    def test_export_boxes_for_excel_zero_jql_matched(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tsv_path = os.path.join(tmp_dir, "export.tsv")
            csv_path = os.path.join(tmp_dir, "export.csv")

            sample_boxes = [
                {
                    "boxId": 202,
                    "boxName": "Empty JQL Box",
                    "boxType": "Program",
                    "boxStartDate": "01/JAN/24",
                    "boxEndDate": "31/DEC/24",
                    "owner_name": "Admin",
                    "owner_email": "admin@example.com",
                    "jira_projects": ["PROJ_A"],
                    "scope_status": "VALID",
                    "narrowing_query": "labels = ghost",
                    "jql_status": "VALID",
                    "jql_total_matched": 0,
                    "is_jql_empty": True,
                    "migration_selected": False,
                    "filter_reason": "JQL 검색 결과 0건 (이관 대상 제외)"
                }
            ]

            export_boxes_for_excel(sample_boxes, tsv_path, csv_path)

            with open(csv_path, "r", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                header = next(reader)
                row = next(reader)
                self.assertEqual(row[1], "202")
                self.assertEqual(row[4], "OPEN (JQL 0건 제외)")
                self.assertEqual(row[11], "labels = ghost")
                self.assertIn("Matched: 0건 - 제외", row[12])

    def test_format_elapsed_time(self):
        self.assertEqual(format_elapsed_time(45.2), "45.20초")
        self.assertIn("2분 15.0초", format_elapsed_time(135.0))
        self.assertIn("1시간 5분", format_elapsed_time(3905.5))

    def test_generate_report_with_zero_jql_and_closed_boxes(self):
        import generate_step1_report as rep
        import json
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_file = os.path.join(tmp_dir, "all_boxes_cache.json")
            html_file = os.path.join(tmp_dir, "report.html")
            tsv_file = os.path.join(tmp_dir, "export.tsv")
            csv_file = os.path.join(tmp_dir, "export.csv")
            proj_file = os.path.join(tmp_dir, "projects.txt")

            with open(proj_file, "w") as f:
                f.write("TEST\n")

            mock_boxes = [
                {
                    "boxId": 1,
                    "boxName": "Active Migration Box",
                    "boxType": "Program",
                    "boxStatus": "IN_PROGRESS",
                    "effective_box_status": "IN_PROGRESS",
                    "is_scope_valid": True,
                    "is_jql_empty": False,
                    "jira_projects": ["TEST"],
                    "configured_projects": ["TEST"],
                    "scope_status": "VALID",
                    "narrowing_query": "status != Closed",
                    "jql_status": "VALID",
                    "jql_total_matched": 10,
                    "migration_selected": True
                },
                {
                    "boxId": 2,
                    "boxName": "Zero JQL Box",
                    "boxType": "Program",
                    "boxStatus": "IN_PROGRESS",
                    "effective_box_status": "IN_PROGRESS",
                    "is_scope_valid": True,
                    "is_jql_empty": True,
                    "jira_projects": ["TEST"],
                    "configured_projects": ["TEST"],
                    "scope_status": "VALID",
                    "narrowing_query": "labels = ghost",
                    "jql_status": "VALID",
                    "jql_total_matched": 0,
                    "migration_selected": False,
                    "filter_reason": "JQL 검색 결과 0건 (이관 대상 제외)"
                }
            ]
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(mock_boxes, f)

            with patch.object(rep, "ALL_BOXES_CACHE_FILE", cache_file), \
                 patch.object(rep, "OUTPUT_HTML", html_file), \
                 patch.object(rep, "TSV_EXPORT_FILE", tsv_file), \
                 patch.object(rep, "CSV_EXPORT_FILE", csv_file), \
                 patch.object(rep, "PROJECTS_FILE", proj_file), \
                 patch.object(rep, "MIGRATION_BOX_FILE", os.path.join(tmp_dir, "none.json")), \
                 patch.object(rep, "BOXES_FILE", os.path.join(tmp_dir, "none.json")), \
                 patch.object(rep, "HIERARCHY_FILE", os.path.join(tmp_dir, "none.json")), \
                 patch.object(rep, "LOG_FILE", os.path.join(tmp_dir, "none.log")):
                rep.generate_report()

            self.assertTrue(os.path.exists(html_file))
            with open(html_file, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("JQL 0건 (내용없음) Box", content)
            self.assertIn("OPEN (JQL 0건 제외)", content)
            self.assertIn("data-jqlempty=\"true\"", content)

    def test_extract_scope_details_with_connected_fields(self):
        scope_data = {
            "cargo": {
                "connectedField": "customfield_10200",
                "extPlatformScopeDefinitions": [
                    {
                        "scopeOwner": {"key": "admin"},
                        "connectedJiraField": "customfield_10500",
                        "scopeDefinitionElements": [
                            {"type": "JIRA_FIELD", "fieldId": "customfield_10999", "fieldName": "Target Box"},
                            {"type": "JIRA_PROJECT", "url": "/rest/api/2/project/PROJ_C"}
                        ]
                    }
                ]
            }
        }
        res = extract_scope_details(scope_data)
        self.assertEqual(res["project_keys"], ["PROJ_C"])
        self.assertEqual(len(res["connected_fields"]), 3)
        field_ids = [c.get("id") or str(c.get("data") or "") for c in res["connected_fields"]]
        self.assertIn("customfield_10999", field_ids)
        self.assertIn("customfield_10500", field_ids)
        self.assertIn("customfield_10200", field_ids)

    @patch("step1_export_dc_boxes.fetch_json")
    def test_validate_connected_field(self, mock_fetch):
        mock_fetch.return_value = [
            {"id": "customfield_10200", "name": "Box Link", "custom": True},
            {"id": "summary", "name": "Summary", "custom": False}
        ]
        # Active field
        res_active = validate_connected_field("customfield_10200")
        self.assertTrue(res_active["exists"])
        self.assertEqual(res_active["status"], "ACTIVE")
        self.assertEqual(res_active["name"], "Box Link")

        # By name
        res_name = validate_connected_field("Box Link")
        self.assertTrue(res_name["exists"])
        self.assertEqual(res_name["id"], "customfield_10200")

        # Not found field
        res_missing = validate_connected_field("customfield_99999")
        self.assertFalse(res_missing["exists"])
        self.assertEqual(res_missing["status"], "NOT_FOUND")

    @patch("step1_export_dc_boxes.fetch_box_types")
    @patch("step1_export_dc_boxes.validate_jql")
    @patch("step1_export_dc_boxes.fetch_json")
    def test_single_box_diagnostics(self, mock_fetch, mock_validate_jql, mock_types):
        mock_types.return_value = {"1": "Program"}
        mock_validate_jql.return_value = {"valid": True, "status": "VALID", "error": None, "total_issues": 5}
        project_status_cache["TEST"] = {"exists": True, "archived": False, "status": "ACTIVE"}

        # Hierarchy and scope returns
        hierarchy_payload = {
            "cargo": {
                "boxId": "100",
                "boxName": "Test Program Box",
                "boxStatus": "IN_PROGRESS",
                "boxTypeId": "1",
                "boxChildren": []
            }
        }
        scope_payload = {
            "cargo": {
                "extPlatformScopeDefinitions": [
                    {
                        "scopeOwner": {"key": "manager"},
                        "narrowingQuery": "project = TEST",
                        "scopeDefinitionElements": [
                            {"type": "JIRA_PROJECT", "url": "/rest/api/2/project/TEST"}
                        ]
                    }
                ]
            }
        }
        mock_fetch.side_effect = [hierarchy_payload, scope_payload]

        diag = test_single_box("100", target_projects={"TEST"})
        self.assertIsNotNone(diag)
        self.assertEqual(diag["box_id"], "100")
        self.assertEqual(diag["box_name"], "Test Program Box")
        self.assertIn("MIGRATION CANDIDATE", diag["verdict"])
        self.assertEqual(diag["issues"], [])

    def test_scope_details_with_disabled_task_types_from_user_case(self):
        # Exact payload from user's PROG-82
        user_scope_payload = {
            "cargo": {
                "boxId": "PROG-82",
                "extPlatformScopeDefinitions": [
                    {
                        "extPlatformId": 2,
                        "scopeOwner": {"id": "2@JIRAUSER217288", "key": "JIRAUSER217288", "isActive": True},
                        "taskTypes": [
                            {"key": 2, "label": "Versions", "isEnabled": False},
                            {"key": 3, "label": "Components", "isEnabled": False},
                            {"key": 5, "label": "Projects", "isEnabled": False},
                            {"key": 4, "label": "Sprints", "isEnabled": False}
                        ],
                        "availableScopeDefinitionElementTypes": [
                            "JIRA_PROJECT", "JIRA_AGILE_BOARD", "JIRA_FILTER", "OKR_O", "OKR_KR"
                        ],
                        "scopeDefinitionElements": [
                            {
                                "type": "JIRA_AGILE_BOARD",
                                "value": "5941",
                                "name": "[공식] GISTIS Sprint board",
                                "isAvailable": True,
                                "isAccessible": True,
                                "url": "https://jira.hmg-corp.io/secure/RapidBoard.jspa?rapidView=5941",
                                "isAccessibleForCurrentUser": True
                            },
                            {
                                "type": "JIRA_FILTER",
                                "value": "42998",
                                "name": "품질-교통정보",
                                "isAvailable": True,
                                "isAccessible": True,
                                "url": "https://jira.hmg-corp.io/issues/?filter=42998",
                                "isAccessibleForCurrentUser": True
                            },
                            {
                                "type": "JIRA_PROJECT",
                                "value": "21946",
                                "name": "HMC-HAE GIS/TIS Development",
                                "isAvailable": True,
                                "isAccessible": True,
                                "url": "https://jira.hmg-corp.io/projects/GISTIS",
                                "isAccessibleForCurrentUser": True
                            }
                        ],
                        "narrowingQuery": "issueLinkType = \"is child of\" AND issue in linkedIssues(GISTIS-6852)",
                        "manuallyAddedTasks": [],
                        "isCorrupted": False
                    }
                ]
            }
        }
        res = extract_scope_details(user_scope_payload)
        # Because Projects has isEnabled: False, GISTIS should NOT be in project_keys!
        self.assertEqual(res["project_keys"], [])
        self.assertIn("GISTIS", res["disabled_projects"])
        self.assertFalse(res["projects_enabled"])
        self.assertTrue(res["all_task_types_disabled"])
        self.assertEqual(res["saved_filters"], ["42998"])
        self.assertEqual(res["boards"], ["5941"])

        # When processed in process_node, it must NOT be in migration_boxes
        hierarchy = {
            "boxId": "PROG-82", "boxName": "Disabled Scope Box", "boxStatus": "IN_PROGRESS",
            "boxChildren": []
        }
        scope_cache = {"PROG-82": user_scope_payload}
        migration_boxes, scanned_boxes = [], []
        process_node(hierarchy, {"GISTIS"}, migration_boxes, scanned_boxes, {}, scope_cache=scope_cache)
        self.assertEqual(migration_boxes, [])
        self.assertEqual(len(scanned_boxes), 0)  # Excluded from both report and migration because no project scope

if __name__ == "__main__":
    unittest.main()

