import json
import shutil
import tempfile
import unittest
from pathlib import Path

from filetidy import config as config_module


class MergeTests(unittest.TestCase):
    def test_scalar_override(self):
        merged = config_module.merge({"archive_name": "_Archive"}, {"archive_name": "Sorted"})
        self.assertEqual(merged["archive_name"], "Sorted")

    def test_categories_merge_key_by_key(self):
        base = {"categories": {"PDF": ["pdf"], "Images": ["png"]}}
        merged = config_module.merge(base, {"categories": {"Invoices": ["pdf"]}})
        # Adding a category must not wipe the defaults.
        self.assertEqual(merged["categories"]["Images"], ["png"])
        self.assertEqual(merged["categories"]["Invoices"], ["pdf"])

    def test_empty_list_removes_a_category(self):
        base = {"categories": {"PDF": ["pdf"], "Images": ["png"]}}
        merged = config_module.merge(base, {"categories": {"Images": []}})
        self.assertNotIn("Images", merged["categories"])

    def test_merge_does_not_mutate_the_base(self):
        base = {"categories": {"PDF": ["pdf"]}}
        config_module.merge(base, {"categories": {"PDF": ["pdf", "ps"]}})
        self.assertEqual(base["categories"]["PDF"], ["pdf"])


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_defaults_when_no_file(self):
        loaded = config_module.load(target_dir=self.tmp)
        self.assertEqual(loaded["archive_name"], "_Archive")
        self.assertIn("PDF", loaded["categories"])

    def test_folder_config_is_picked_up(self):
        path = self.tmp / config_module.CONFIG_FILENAME
        path.write_text(json.dumps({"archive_name": "Sorted"}), encoding="utf-8")
        loaded = config_module.load(target_dir=self.tmp)
        self.assertEqual(loaded["archive_name"], "Sorted")
        self.assertIn("PDF", loaded["categories"])

    def test_starter_config_is_valid_json(self):
        path = config_module.write_starter(self.tmp / "filetidy.json")
        with path.open(encoding="utf-8") as handle:
            json.load(handle)


if __name__ == "__main__":
    unittest.main()
