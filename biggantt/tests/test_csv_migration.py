import os
import sys
import unittest
import tempfile
import csv
import json

from step2_import_cloud_boxes import load_boxes_from_csv, save_migration_results, _extract_column


class TestCSVMigration(unittest.TestCase):

    def test_extract_column(self):
        row = {"Box Name": "Test Box", "시작일 (Start Date)": "2024-01-01"}
        self.assertEqual(_extract_column(row, ["boxName", "Box Name"]), "Test Box")
        self.assertEqual(_extract_column(row, ["시작일", "boxStartDate"]), "2024-01-01")
        self.assertEqual(_extract_column(row, ["Missing"], default="DEFAULT"), "DEFAULT")

    def test_load_boxes_from_csv(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", suffix=".csv", delete=False) as f:
            writer = csv.writer(f)
            writer.writerow([
                "Box ID", "Box Name", "Box Type", "시작일 (Start Date)", "종료일 (End Date)",
                "소유자 (Owner Name)", "소유자 이메일 (Owner Email)", "연동 Jira 프로젝트 (Jira Projects)",
                "상위 Box ID (Parent Box ID)", "Narrowing Query (JQL)", "Source Base URL", "Source Environment", "New Box ID"
            ])
            writer.writerow([
                "101", "Program Alpha", "Program", "2024-01-01", "2024-12-31",
                "Hong Gildong", "hong@test.com", "PROJ_A, PROJ_B",
                "ROOT", "status = Open", "https://dc-jira.example.com", "OPS", ""
            ])
            temp_path = f.name

        try:
            boxes = load_boxes_from_csv(temp_path)
            self.assertEqual(len(boxes), 1)
            b = boxes[0]
            self.assertEqual(b["boxId"], 101)
            self.assertEqual(b["boxName"], "Program Alpha")
            self.assertEqual(b["boxType"], "Program")
            self.assertEqual(b["boxStartDate"], "2024-01-01")
            self.assertEqual(b["boxEndDate"], "2024-12-31")
            self.assertEqual(b["jira_projects"], ["PROJ_A", "PROJ_B"])
            self.assertEqual(b["parentBoxId"], "ROOT")
            self.assertEqual(b["owner_name"], "Hong Gildong")
            self.assertEqual(b["owner_email"], "hong@test.com")
            self.assertEqual(b["source_base_url"], "https://dc-jira.example.com")
            self.assertEqual(b["source_environment"], "OPS")
            self.assertEqual(b["narrowing_query"], "status = Open")
            self.assertNotIn("newBoxId", b)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_save_migration_results_updates_csv(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", suffix=".csv", delete=False) as f:
            writer = csv.writer(f)
            writer.writerow(["Box ID", "Box Name", "New Box ID"])
            writer.writerow(["201", "Beta Box", ""])
            temp_path = f.name

        try:
            boxes = [{"boxId": 201, "boxName": "Beta Box", "newBoxId": "CLOUD-999"}]
            save_migration_results(temp_path, boxes)

            # Verify CSV file is updated with New Box ID
            with open(temp_path, "r", encoding="utf-8-sig") as f:
                reader = list(csv.DictReader(f))
                self.assertEqual(len(reader), 1)
                self.assertEqual(reader[0]["New Box ID"], "CLOUD-999")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


if __name__ == "__main__":
    unittest.main()
