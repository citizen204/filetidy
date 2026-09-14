import unittest

from filetidy.names import diagnose, is_portable, safe_name


class SafeNameTests(unittest.TestCase):
    def test_leaves_portable_names_alone(self):
        for name in ("report.docx", "photo.JPG", "预算表.xlsx", "café.txt", "a-b_c.1.tar.gz"):
            self.assertEqual(safe_name(name), name, name)
            self.assertTrue(is_portable(name), name)

    def test_extension_case_is_preserved(self):
        # Renaming must not lowercase the extension, or a case-insensitive
        # filesystem sees a pointless collision.
        self.assertEqual(safe_name("photo.JPG"), "photo.JPG")
        self.assertEqual(safe_name("archive.TAR.GZ"), "archive.TAR.GZ")

    def test_windows_illegal_characters(self):
        self.assertEqual(safe_name("rent:bills.xlsx"), "rent-bills.xlsx")
        self.assertEqual(safe_name('a<b>c|d?e*f".txt'), "a-b-c-d-e-f-.txt")

    def test_path_separator_is_replaced_not_split(self):
        self.assertEqual(safe_name("a/b.txt"), "a-b.txt")
        self.assertEqual(safe_name("a\\b.txt"), "a-b.txt")

    def test_narrow_no_break_space_becomes_plain_space(self):
        name = "Screenshot 2026-05-22 at 1.32.23\u202fpm.png"
        self.assertEqual(safe_name(name), "Screenshot 2026-05-22 at 1.32.23 pm.png")
        self.assertFalse(is_portable(name))

    def test_windows_reserved_device_names(self):
        self.assertEqual(safe_name("CON.txt"), "CON_file.txt")
        self.assertEqual(safe_name("lpt1.log"), "lpt1_file.log")

    def test_trailing_dots_and_spaces_removed(self):
        self.assertEqual(safe_name("trailing. "), "trailing")

    def test_control_characters_removed(self):
        self.assertEqual(safe_name("bad\x07name.txt"), "badname.txt")

    def test_never_returns_empty(self):
        self.assertEqual(safe_name("..."), "untitled")
        self.assertEqual(safe_name("   "), "untitled")

    def test_long_name_trimmed_without_breaking_utf8(self):
        name = "预" * 300 + ".txt"
        result = safe_name(name)
        self.assertLessEqual(len(result.encode("utf-8")), 240)
        # Decodes cleanly -- no half-written multi-byte character.
        result.encode("utf-8").decode("utf-8")
        self.assertTrue(result.endswith(".txt"))

    def test_runs_of_plain_spaces_are_left_alone(self):
        # Two spaces in a row are legal everywhere. Collapsing them would
        # rename a file that has nothing wrong with it -- and `diagnose` would
        # have no reason to show for the change.
        self.assertEqual(safe_name("4.3 PT  IPv6 Discovery.pka"), "4.3 PT  IPv6 Discovery.pka")
        self.assertTrue(is_portable("a  b.txt"))

    def test_exotic_space_swapped_without_collapsing_neighbours(self):
        self.assertEqual(safe_name("a\u00a0 b.txt"), "a  b.txt")

    def test_every_unportable_name_has_a_stated_reason(self):
        # An empty diagnosis next to a proposed rename reads like a bug, so the
        # two must never disagree.
        names = [
            "rent:bills.xlsx",
            "Screenshot 2026-04-13 at 8.50.27\u202fam.png",
            "trailing ",
            "CON.txt",
            "bad\x07name.txt",
            "a/b.txt",
            "\u4e2d\u6587" * 200 + ".txt",
        ]
        for name in names:
            if not is_portable(name):
                self.assertTrue(diagnose(name), "no reason given for %r" % name)

    def test_idempotent(self):
        for name in ("rent:bills.xlsx", "CON.txt", "a/b.txt", "trailing. "):
            once = safe_name(name)
            self.assertEqual(safe_name(once), once, name)


class DiagnoseTests(unittest.TestCase):
    def test_reports_illegal_character(self):
        problems = diagnose("rent:bills.xlsx")
        self.assertTrue(any("Windows-illegal" in p for p in problems))

    def test_reports_exotic_space(self):
        problems = diagnose("a\u202fb.png")
        self.assertTrue(any("U+202F" in p for p in problems))

    def test_clean_name_has_no_problems(self):
        self.assertEqual(diagnose("report.docx"), ())


if __name__ == "__main__":
    unittest.main()
