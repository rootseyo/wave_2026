import copy
import unittest

from biggantt_field_mapping import expected_config, validate_save_payload
from field_mapping_data import NeedsReview


class SaveGuardTests(unittest.TestCase):
    def setUp(self):
        self.before = {"externalCatalogId": "10067", "projectKey": "CFMT", "extPlatformId": 2,
            "migrateValues": False, "taskComment": False, "updateNotifications": False,
            "periodCalculatedForAssignee": True, "fieldScreenValidation": False,
            "dynamicMapping": {
                "Start Date": {"primaryFieldId": "customfield_1", "secondaryFieldId": "customfield_3"},
                "End Date": {"primaryFieldId": "customfield_2", "secondaryFieldId": "none"},
                "Progress": {"primaryFieldId": "customfield_4", "secondaryFieldId": "none"}}}
        self.expected = expected_config(self.before, {"fields": {"start": {"id": "customfield_10"}, "end": {"id": "customfield_20"}}})

    def check(self, payload):
        validate_save_payload(payload, self.expected, "10067", "CFMT")

    def test_only_primary_dates_change_without_mutating_snapshot(self):
        self.check(self.expected)
        self.assertEqual(self.before["dynamicMapping"]["Start Date"]["primaryFieldId"], "customfield_1")
        self.assertEqual(self.expected["dynamicMapping"]["Start Date"]["secondaryFieldId"], "customfield_3")

    def test_unrelated_mapping_change_blocked(self):
        payload = copy.deepcopy(self.expected)
        payload["dynamicMapping"]["Progress"]["primaryFieldId"] = "none"
        with self.assertRaises(NeedsReview): self.check(payload)

    def test_overwrite_blocked(self):
        payload = copy.deepcopy(self.expected)
        payload["migrateValues"] = True
        with self.assertRaises(NeedsReview): self.check(payload)

    def test_wrong_project_blocked(self):
        payload = copy.deepcopy(self.expected)
        payload["externalCatalogId"] = "10068"
        with self.assertRaises(NeedsReview): self.check(payload)

    def test_new_unknown_payload_setting_blocked(self):
        payload = copy.deepcopy(self.expected)
        payload["newSetting"] = True
        with self.assertRaises(NeedsReview): self.check(payload)

    def test_secondary_mapping_change_blocked(self):
        payload = copy.deepcopy(self.expected)
        payload["dynamicMapping"]["Start Date"]["secondaryFieldId"] = "none"
        with self.assertRaises(NeedsReview): self.check(payload)

    def test_native_omission_only_allowed_for_matching_implicit_default(self):
        expected = copy.deepcopy(self.expected)
        default = {"primaryFieldId": "summary", "secondaryFieldId": "none"}
        expected["dynamicMapping"]["Summary"] = default
        validate_save_payload(self.expected, expected, "10067", "CFMT", {"Summary": default})

    def test_implicit_override_cannot_be_silently_reset_to_default(self):
        expected = copy.deepcopy(self.expected)
        expected["dynamicMapping"]["Summary"] = {"primaryFieldId": "customfield_77", "secondaryFieldId": "none"}
        with self.assertRaises(NeedsReview):
            validate_save_payload(self.expected, expected, "10067", "CFMT",
                                  {"Summary": {"primaryFieldId": "summary", "secondaryFieldId": "none"}})

    def test_editable_mapping_cannot_be_omitted_even_if_default(self):
        payload = copy.deepcopy(self.expected)
        del payload["dynamicMapping"]["Progress"]
        with self.assertRaises(NeedsReview):
            validate_save_payload(payload, self.expected, "10067", "CFMT", self.expected["dynamicMapping"])


if __name__ == "__main__":
    unittest.main()
