import unittest

from filetidy.rules import (
    Ruleset,
    is_incomplete,
    looks_like_screenshot,
    normalise_for_match,
    split_extension,
)


class SplitExtensionTests(unittest.TestCase):
    def test_plain_extension_is_lowercased(self):
        self.assertEqual(split_extension("Report.PDF"), ("Report", ".pdf"))

    def test_compound_extension_kept_together(self):
        self.assertEqual(split_extension("backup.tar.gz"), ("backup", ".tar.gz"))

    def test_dotfile_has_no_extension(self):
        self.assertEqual(split_extension(".gitignore"), (".gitignore", ""))

    def test_trailing_dot_has_no_extension(self):
        self.assertEqual(split_extension("weird."), ("weird.", ""))

    def test_no_dot(self):
        self.assertEqual(split_extension("Makefile"), ("Makefile", ""))


class ScreenshotDetectionTests(unittest.TestCase):
    def test_macos_narrow_no_break_space(self):
        # macOS writes U+202F between the time and am/pm.
        name = "Screenshot 2026-05-22 at 1.32.23\u202fpm.png"
        self.assertTrue(looks_like_screenshot(name))

    def test_macos_screen_recording(self):
        self.assertTrue(looks_like_screenshot("Screen Recording 2026-05-03 at 4.24.38\u202fam.mov"))

    def test_windows_style(self):
        self.assertTrue(looks_like_screenshot("Screenshot (3).png"))

    def test_chinese_names(self):
        self.assertTrue(looks_like_screenshot("截屏2026-09-14 上午10.00.00.png"))
        self.assertTrue(looks_like_screenshot("屏幕截图 2026-09-14 100000.png"))

    def test_cleanshot(self):
        self.assertTrue(looks_like_screenshot("CleanShot 2026-01-01 at 10.00.00.png"))

    def test_ordinary_image_is_not_a_screenshot(self):
        self.assertFalse(looks_like_screenshot("holiday-photo.png"))
        self.assertFalse(looks_like_screenshot("screenshots-guide.pdf"))

    def test_normalise_collapses_unicode_spaces(self):
        self.assertEqual(normalise_for_match("a\u00a0b\u3000c"), "a b c")


class IncompleteDownloadTests(unittest.TestCase):
    def test_known_partial_suffixes(self):
        for name in ("big.iso.crdownload", "x.part", "y.download", "z.aria2"):
            self.assertTrue(is_incomplete(name), name)

    def test_complete_file(self):
        self.assertFalse(is_incomplete("big.iso"))


class RulesetTests(unittest.TestCase):
    def setUp(self):
        self.rules = Ruleset()

    def test_common_extensions(self):
        expected = {
            "report.docx": "Documents",
            "sheet.xlsx": "Spreadsheets",
            "deck.pptx": "Presentations",
            "manual.pdf": "PDF",
            "photo.JPG": "Images",
            "clip.mov": "Video",
            "song.mp3": "Audio",
            "backup.tar.gz": "Archives",
            "setup.dmg": "Installers",
            "app.exe": "Installers",
            "main.py": "Code",
        }
        for name, category in expected.items():
            self.assertEqual(self.rules.category_for(name), category, name)

    def test_unknown_extension_falls_back(self):
        self.assertEqual(self.rules.category_for("mystery.xyz"), "Other")

    def test_no_extension_falls_back(self):
        self.assertEqual(self.rules.category_for("Makefile"), "Other")

    def test_screenshot_beats_extension(self):
        name = "Screenshot 2026-05-22 at 1.32.23\u202fpm.png"
        self.assertEqual(self.rules.category_for(name), "Screenshots")

    def test_screenshot_detection_can_be_disabled(self):
        rules = Ruleset(detect_screenshots=False)
        name = "Screenshot 2026-05-22 at 1.32.23\u202fpm.png"
        self.assertEqual(rules.category_for(name), "Images")

    def test_custom_categories_override(self):
        rules = Ruleset(categories={"Invoices": ["pdf"]})
        self.assertEqual(rules.category_for("bill.pdf"), "Invoices")
        self.assertEqual(rules.category_for("photo.jpg"), "Other")


class DefaultCategoryHygieneTests(unittest.TestCase):
    def test_no_extension_is_claimed_by_two_categories(self):
        # Silent shadowing: whichever category is defined first wins, so a
        # duplicate quietly sends files somewhere surprising (".ts" landing in
        # Video instead of Code was exactly this).
        from collections import defaultdict

        from filetidy.rules import DEFAULT_CATEGORIES

        owners = defaultdict(list)
        for category, extensions in DEFAULT_CATEGORIES.items():
            for ext in extensions:
                owners[ext].append(category)
        duplicates = {e: c for e, c in owners.items() if len(c) > 1}
        self.assertEqual(duplicates, {}, "extension claimed twice: %s" % duplicates)

    def test_extensions_are_stored_lowercase_and_dotless(self):
        from filetidy.rules import DEFAULT_CATEGORIES

        for category, extensions in DEFAULT_CATEGORIES.items():
            for ext in extensions:
                self.assertEqual(ext, ext.lower(), "%s: %s" % (category, ext))
                self.assertFalse(ext.startswith("."), "%s: %s" % (category, ext))


if __name__ == "__main__":
    unittest.main()
