import os
import unittest
import tempfile
import csv
import json
from unittest.mock import patch, MagicMock

import step1_export_dc_boxes as step1
import generate_step1_report as report_gen

class TestHierarchyExportAndReport(unittest.TestCase):

    def test_process_node_hierarchy_depth_and_parent(self):
        """Verify process_node correctly assigns depth, parentBoxId, and hierarchy_prefix."""
        mock_tree = {
            "boxId": "ROOT",
            "boxChildren": [
                {
                    "boxId": "100",
                    "boxName": "Parent Box Alpha",
                    "boxTypeId": "1",
                    "boxStatus": "IN_PROGRESS",
                    "boxChildren": [
                        {
                            "boxId": "101",
                            "boxName": "Child Box Beta",
                            "boxTypeId": "2",
                            "boxStatus": "IN_PROGRESS",
                            "boxChildren": [
                                {
                                    "boxId": "102",
                                    "boxName": "Grandchild Box Gamma",
                                    "boxTypeId": "2",
                                    "boxStatus": "IN_PROGRESS",
                                    "boxChildren": []
                                }
                            ]
                        }
                    ]
                }
            ]
        }

        scope_cache = {
            "100": {
                "cargo": {
                    "extPlatformScopeDefinitions": [{
                        "scopeOwner": {"key": "owner1"},
                        "scopeDefinitionElements": [{"type": "JIRA_PROJECT", "url": "/rest/api/2/project/PROJ_A"}]
                    }]
                }
            },
            "101": {
                "cargo": {
                    "extPlatformScopeDefinitions": [{
                        "scopeOwner": {"key": "owner2"},
                        "scopeDefinitionElements": [{"type": "JIRA_PROJECT", "url": "/rest/api/2/project/PROJ_B"}]
                    }]
                }
            },
            "102": {
                "cargo": {
                    "extPlatformScopeDefinitions": [{
                        "scopeOwner": {"key": "owner3"},
                        "scopeDefinitionElements": [{"type": "JIRA_PROJECT", "url": "/rest/api/2/project/PROJ_C"}]
                    }]
                }
            }
        }

        migration_boxes = []
        all_scanned_boxes = []

        with patch("step1_export_dc_boxes.get_project_status", return_value={"exists": True, "archived": False, "status": "ACTIVE", "key": "PROJ_OK"}):
            with patch("step1_export_dc_boxes.get_user_info", return_value=("test@example.com", "Test Owner")):
                step1.process_node(
                    mock_tree,
                    target_projects=None,
                    migration_boxes=migration_boxes,
                    all_scanned_boxes=all_scanned_boxes,
                    box_type_map={"1": "Program", "2": "Team"},
                    parent_id="ROOT",
                    depth=0,
                    scope_cache=scope_cache
                )

        self.assertEqual(len(all_scanned_boxes), 3)

        parent = all_scanned_boxes[0]
        self.assertEqual(str(parent["boxId"]), "100")
        self.assertEqual(parent["parentBoxId"], "ROOT")
        self.assertEqual(parent["depth"], 0)
        self.assertEqual(parent["hierarchy_prefix"], "")
        self.assertEqual(parent["displayName"], "Parent Box Alpha")

        child = all_scanned_boxes[1]
        self.assertEqual(str(child["boxId"]), "101")
        self.assertEqual(str(child["parentBoxId"]), "100")
        self.assertEqual(child["depth"], 1)
        self.assertIn("│  ⎿  ", child["hierarchy_prefix"])
        self.assertEqual(child["displayName"], "│  ⎿  Child Box Beta")

        grandchild = all_scanned_boxes[2]
        self.assertEqual(str(grandchild["boxId"]), "102")
        self.assertEqual(str(grandchild["parentBoxId"]), "101")
        self.assertEqual(grandchild["depth"], 2)
        self.assertIn("│  │  ⎿  ", grandchild["hierarchy_prefix"])
        self.assertEqual(grandchild["displayName"], "│  │  ⎿  Grandchild Box Gamma")

    def test_sort_boxes_by_hierarchy(self):
        """Verify sort_boxes_by_hierarchy places children directly below their parents in DFS order."""
        boxes = [
            {"boxId": "200", "boxName": "Root 2", "parentBoxId": "ROOT", "depth": 0, "is_migration_target": True},
            {"boxId": "101", "boxName": "Child 1-1", "parentBoxId": "100", "depth": 1, "is_migration_target": True},
            {"boxId": "100", "boxName": "Root 1", "parentBoxId": "ROOT", "depth": 0, "is_migration_target": True},
            {"boxId": "102", "boxName": "Grandchild 1-1-1", "parentBoxId": "101", "depth": 2, "is_migration_target": True},
            {"boxId": "201", "boxName": "Child 2-1", "parentBoxId": "200", "depth": 1, "is_migration_target": True},
        ]

        sorted_boxes = report_gen.sort_boxes_by_hierarchy(boxes)
        ordered_ids = [str(b["boxId"]) for b in sorted_boxes]

        # Root 1 -> Child 1-1 -> Grandchild 1-1-1 -> Root 2 -> Child 2-1
        self.assertEqual(ordered_ids, ["100", "101", "102", "200", "201"])

    def test_export_boxes_for_excel_contains_hierarchy_columns(self):
        """Verify TSV/CSV exports contain hierarchy columns and tree symbols."""
        boxes = [
            {
                "boxId": "10",
                "boxName": "Parent Program",
                "parentBoxId": "ROOT",
                "depth": 0,
                "hierarchy_prefix": "",
                "boxType": "Program",
                "jira_projects": ["PROJ_P"],
                "migration_selected": True
            },
            {
                "boxId": "11",
                "boxName": "Sub Team Box",
                "parentBoxId": "10",
                "depth": 1,
                "hierarchy_prefix": "│  ⎿  ",
                "boxType": "Agile Team",
                "jira_projects": ["PROJ_T"],
                "migration_selected": True
            }
        ]

        with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", suffix=".tsv", delete=False) as tf:
            tsv_path = tf.name
        with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", suffix=".csv", delete=False) as cf:
            csv_path = cf.name

        try:
            step1.export_boxes_for_excel(boxes, tsv_path, csv_path)

            with open(csv_path, "r", encoding="utf-8-sig") as f:
                reader = list(csv.reader(f))
                header = reader[0]
                self.assertIn("상위 Box ID (Parent Box ID)", header)
                self.assertIn("계층 레벨 (Depth)", header)

                row_parent = reader[1]
                row_child = reader[2]

                self.assertEqual(row_parent[1], "10") # Box ID
                self.assertEqual(row_parent[2], "Parent Program") # Box Name
                self.assertEqual(row_parent[16], "ROOT") # Parent Box ID
                self.assertEqual(row_parent[17], "0") # Depth

                self.assertEqual(row_child[1], "11") # Box ID
                self.assertIn("│  ⎿  ", row_child[2]) # Box Name with tree symbol
                self.assertEqual(row_child[16], "10") # Parent Box ID
                self.assertEqual(row_child[17], "1") # Depth
        finally:
            if os.path.exists(tsv_path):
                os.remove(tsv_path)
            if os.path.exists(csv_path):
                os.remove(csv_path)

    def test_report_html_contains_interactive_tree_controls(self):
        """Verify the generated Step 1 report HTML contains interactive tree toggle buttons, child badges, and JS tree logic."""
        test_boxes = [
            {
                "boxId": "100",
                "boxName": "Parent Program",
                "parentBoxId": "ROOT",
                "depth": 0,
                "hierarchy_prefix": "",
                "boxType": "Program",
                "boxStatus": "IN_PROGRESS",
                "jira_projects": ["PROJ_A"],
                "is_migration_target": True
            },
            {
                "boxId": "101",
                "boxName": "Child Team A",
                "parentBoxId": "100",
                "depth": 1,
                "hierarchy_prefix": "│  ⎿  ",
                "boxType": "Agile Team",
                "boxStatus": "IN_PROGRESS",
                "jira_projects": ["PROJ_A"],
                "is_migration_target": True
            },
            {
                "boxId": "102",
                "boxName": "Grandchild Subteam 1",
                "parentBoxId": "101",
                "depth": 2,
                "hierarchy_prefix": "│  │  ⎿  ",
                "boxType": "Agile Team",
                "boxStatus": "IN_PROGRESS",
                "jira_projects": ["PROJ_A"],
                "is_migration_target": True
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_mig = os.path.join(temp_dir, "migration_box.json")
            temp_cache = os.path.join(temp_dir, "all_boxes_cache.json")
            temp_hierarchy = os.path.join(temp_dir, "hierarchy.json")
            temp_projects = os.path.join(temp_dir, "projects.txt")
            temp_html = os.path.join(temp_dir, "report.html")
            temp_tsv = os.path.join(temp_dir, "export.tsv")
            temp_csv = os.path.join(temp_dir, "export.csv")

            with open(temp_mig, "w", encoding="utf-8") as f:
                json.dump(test_boxes, f)
            with open(temp_cache, "w", encoding="utf-8") as f:
                json.dump(test_boxes, f)
            with open(temp_hierarchy, "w", encoding="utf-8") as f:
                json.dump({
                    "100": {"parentBoxId": "ROOT", "depth": 0},
                    "101": {"parentBoxId": "100", "depth": 1},
                    "102": {"parentBoxId": "101", "depth": 2}
                }, f)
            with open(temp_projects, "w", encoding="utf-8") as f:
                f.write("PROJ_A\n")

            with patch("generate_step1_report.MIGRATION_BOX_FILE", temp_mig), \
                 patch("generate_step1_report.ALL_BOXES_CACHE_FILE", temp_cache), \
                 patch("generate_step1_report.HIERARCHY_FILE", temp_hierarchy), \
                 patch("generate_step1_report.PROJECTS_FILE", temp_projects), \
                 patch("generate_step1_report.OUTPUT_HTML", temp_html), \
                 patch("generate_step1_report.TSV_EXPORT_FILE", temp_tsv), \
                 patch("generate_step1_report.CSV_EXPORT_FILE", temp_csv):

                out_path = report_gen.generate_report()
                self.assertTrue(os.path.exists(out_path))

                with open(out_path, "r", encoding="utf-8") as f:
                    html_text = f.read()

                # Verify tree toolbar buttons
                self.assertIn("expandAllTree()", html_text)
                self.assertIn("collapseAllTree()", html_text)
                self.assertIn("전체 펼치기", html_text)
                self.assertIn("전체 접기", html_text)

                # Verify tree toggle buttons and child badges
                self.assertIn("tree-toggle-btn", html_text)
                self.assertIn("tree-toggle-icon", html_text)
                self.assertIn("child-count-pill", html_text)
                self.assertIn("parent-id-pill", html_text)
                self.assertIn("toggleTreeItem", html_text)

                # Verify data attributes for tree hierarchy
                self.assertIn('data-parent-id="ROOT"', html_text)
                self.assertIn('data-parent-id="100"', html_text)
                self.assertIn('data-parent-id="101"', html_text)
                self.assertIn('data-has-children="true"', html_text)
                self.assertIn('data-has-children="false"', html_text)
                self.assertIn('data-depth="0"', html_text)
                self.assertIn('data-depth="1"', html_text)
                self.assertIn('data-depth="2"', html_text)

                # Verify JS tree engine functions
                self.assertIn("function initTreeHierarchy()", html_text)
                self.assertIn("function isAncestorCollapsed(boxId)", html_text)
                self.assertIn("function toggleTreeItem(boxId, event)", html_text)
                self.assertIn("function updateTreeUI()", html_text)
                self.assertIn("function expandAllTree()", html_text)
                self.assertIn("function collapseAllTree()", html_text)

                # Verify tree symbol preservation
                self.assertIn("│  ⎿  ", html_text)
                self.assertIn("│  │  ⎿  ", html_text)

                # Verify Closed box section hidden by default with expand button
                self.assertIn('id="closedBoxSection"', html_text)
                self.assertIn('id="closedBoxContent" style="display: none;', html_text)
                self.assertIn("toggleClosedBoxSection()", html_text)
                self.assertIn("펼치기 ▼", html_text)

                # Selection workflow appears before analytics and diagnostics.
                self.assertLess(html_text.index('id="boxList"'), html_text.index('class="stats-grid"'))
                self.assertLess(html_text.index('id="boxTableContainer"'), html_text.index('id="closedBoxSection"'))
                # Selection guidance and Step 2 export remain available in the report.
                self.assertIn("Shift + 체크박스 클릭", html_text)
                self.assertIn("하위 Box는 자동 선택되지 않습니다.", html_text)
                self.assertIn('id="exportSelectedButton" class="btn-excel" disabled', html_text)
                self.assertIn("선택 Box CSV 내보내기 (Step 2용)", html_text)

if __name__ == "__main__":
    unittest.main()
