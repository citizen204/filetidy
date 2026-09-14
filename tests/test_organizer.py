import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from filetidy.journal import Journal
from filetidy.organizer import Organizer, undo
from filetidy.rules import Ruleset


def touch(path, content="x", age_seconds=3600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    old = time.time() - age_seconds
    os.utime(str(path), (old, old))
    return path


class OrganizerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "inbox"
        self.root.mkdir()
        self.journal = Journal(self.tmp / "state")

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def names_under(self, *parts):
        directory = self.root.joinpath(*parts)
        if not directory.is_dir():
            return []
        return sorted(p.name for p in directory.iterdir())


class PlanTests(OrganizerTestCase):
    def test_files_are_grouped_by_category(self):
        touch(self.root / "a.docx")
        touch(self.root / "b.pdf")
        touch(self.root / "c.png")
        organizer = Organizer()
        organizer.apply(organizer.plan(self.root))
        self.assertEqual(self.names_under("_Archive", "Documents"), ["a.docx"])
        self.assertEqual(self.names_under("_Archive", "PDF"), ["b.pdf"])
        self.assertEqual(self.names_under("_Archive", "Images"), ["c.png"])

    def test_dry_run_moves_nothing(self):
        touch(self.root / "a.docx")
        organizer = Organizer()
        plan = organizer.plan(self.root)
        self.assertEqual(len(plan.moves), 1)
        self.assertTrue((self.root / "a.docx").exists())
        self.assertFalse((self.root / "_Archive").exists())

    def test_recent_files_are_left_alone(self):
        touch(self.root / "fresh.docx", age_seconds=0)
        organizer = Organizer(min_age_seconds=60)
        plan = organizer.plan(self.root)
        self.assertEqual(plan.moves, [])
        self.assertEqual(len(plan.skips), 1)

    def test_incomplete_downloads_are_skipped(self):
        touch(self.root / "big.iso.crdownload")
        plan = Organizer().plan(self.root)
        self.assertEqual(plan.moves, [])
        self.assertIn("download in progress", plan.skips[0].reason)

    def test_hidden_files_skipped_by_default(self):
        touch(self.root / ".secret.txt")
        touch(self.root / ".DS_Store")
        self.assertEqual(Organizer().plan(self.root).moves, [])

    def test_hidden_files_included_on_request(self):
        touch(self.root / ".secret.txt")
        plan = Organizer(include_hidden=True).plan(self.root)
        self.assertEqual([m.source.name for m in plan.moves], [".secret.txt"])

    def test_subfolders_untouched_unless_recursive(self):
        touch(self.root / "sub" / "deep.pdf")
        self.assertEqual(Organizer().plan(self.root).moves, [])
        plan = Organizer(recursive=True).plan(self.root)
        self.assertEqual([m.source.name for m in plan.moves], ["deep.pdf"])

    def test_archive_is_never_reprocessed(self):
        touch(self.root / "a.pdf")
        organizer = Organizer(recursive=True)
        organizer.apply(organizer.plan(self.root))
        # A second pass must find nothing -- otherwise files would ping-pong
        # into _Archive/_Archive on every run.
        self.assertEqual(organizer.plan(self.root).moves, [])
        self.assertEqual(self.names_under("_Archive", "PDF"), ["a.pdf"])

    def test_date_subfolders(self):
        path = touch(self.root / "a.pdf")
        stamp = time.strftime("%Y-%m", time.localtime(path.stat().st_mtime))
        organizer = Organizer(date_subfolders=True)
        organizer.apply(organizer.plan(self.root))
        self.assertEqual(self.names_under("_Archive", "PDF", stamp), ["a.pdf"])

    def test_excludes(self):
        touch(self.root / "keep.pdf")
        touch(self.root / "~$draft.docx")
        plan = Organizer(excludes=["~$*"]).plan(self.root)
        self.assertEqual([m.source.name for m in plan.moves], ["keep.pdf"])


class UnreadableDirectoryTests(OrganizerTestCase):
    @unittest.skipIf(os.name == "nt", "POSIX permission bits do not apply")
    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores permissions")
    def test_permission_error_is_raised_not_swallowed(self):
        # Swallowing this made a folder macOS refused to let us read look
        # exactly like an empty one: no moves, no error, exit status 0.
        locked = self.root / "locked"
        locked.mkdir()
        touch(locked / "a.pdf")
        os.chmod(str(locked), 0o000)
        try:
            with self.assertRaises(OSError):
                Organizer().plan(locked)
        finally:
            os.chmod(str(locked), 0o755)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits do not apply")
    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root ignores permissions")
    def test_recursive_walk_also_raises(self):
        locked = self.root / "locked"
        locked.mkdir()
        touch(locked / "a.pdf")
        os.chmod(str(locked), 0o000)
        try:
            with self.assertRaises(OSError):
                Organizer(recursive=True).plan(locked)
        finally:
            os.chmod(str(locked), 0o755)


class CollisionTests(OrganizerTestCase):
    def test_existing_destination_gets_a_suffix(self):
        touch(self.root / "_Archive" / "PDF" / "a.pdf", content="old")
        touch(self.root / "a.pdf", content="new")
        organizer = Organizer()
        organizer.apply(organizer.plan(self.root))
        self.assertEqual(self.names_under("_Archive", "PDF"), ["a (2).pdf", "a.pdf"])
        self.assertEqual((self.root / "_Archive" / "PDF" / "a.pdf").read_text(), "old")
        self.assertEqual((self.root / "_Archive" / "PDF" / "a (2).pdf").read_text(), "new")

    def test_two_sources_with_the_same_name(self):
        touch(self.root / "one" / "report.pdf", content="1")
        touch(self.root / "two" / "report.pdf", content="2")
        organizer = Organizer(recursive=True)
        result = organizer.apply(organizer.plan(self.root))
        self.assertEqual(len(result.moved), 2)
        self.assertEqual(self.names_under("_Archive", "PDF"), ["report (2).pdf", "report.pdf"])

    def test_compound_extension_suffix_is_placed_correctly(self):
        touch(self.root / "_Archive" / "Archives" / "backup.tar.gz")
        touch(self.root / "backup.tar.gz")
        organizer = Organizer()
        organizer.apply(organizer.plan(self.root))
        self.assertIn("backup (2).tar.gz", self.names_under("_Archive", "Archives"))


class SanitizeTests(OrganizerTestCase):
    def test_narrow_no_break_space_is_normalised(self):
        # U+202F is a legal filename character on every platform, so unlike the
        # colon below this case can actually run everywhere.
        touch(self.root / "Screenshot 2026-05-22 at 1.32.23\u202fpm.png")
        organizer = Organizer(sanitize_names=True)
        organizer.apply(organizer.plan(self.root))
        self.assertEqual(
            self.names_under("_Archive", "Screenshots"),
            ["Screenshot 2026-05-22 at 1.32.23 pm.png"],
        )

    @unittest.skipIf(os.name == "nt", "Windows cannot create a file with ':' in the name")
    def test_windows_illegal_character_is_fixed_while_filing(self):
        # A name like this is created on macOS and only becomes a problem once
        # the file travels to Windows -- which is why the fix belongs here and
        # the test cannot run on Windows itself.
        touch(self.root / "rent:bills.xlsx")
        organizer = Organizer(sanitize_names=True)
        organizer.apply(organizer.plan(self.root))
        self.assertEqual(self.names_under("_Archive", "Spreadsheets"), ["rent-bills.xlsx"])

    def test_names_untouched_by_default(self):
        touch(self.root / "预算表.xlsx")
        organizer = Organizer()
        organizer.apply(organizer.plan(self.root))
        self.assertEqual(self.names_under("_Archive", "Spreadsheets"), ["预算表.xlsx"])


class UndoTests(OrganizerTestCase):
    def test_round_trip_restores_every_file(self):
        names = ["a.docx", "b.pdf", "c.png", "预算表.xlsx"]
        for name in names:
            touch(self.root / name)
        organizer = Organizer()
        result = organizer.apply(organizer.plan(self.root), journal=self.journal)
        self.assertEqual(len(result.moved), len(names))

        moves = self.journal.moves_for(result.session_id)
        undo(moves)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), sorted(names))

    def test_undo_prunes_the_emptied_archive(self):
        touch(self.root / "a.pdf")
        organizer = Organizer()
        result = organizer.apply(organizer.plan(self.root), journal=self.journal)
        undo(self.journal.moves_for(result.session_id))
        self.assertFalse((self.root / "_Archive").exists())

    def test_undo_does_not_clobber_a_file_put_back_by_hand(self):
        touch(self.root / "a.pdf", content="original")
        organizer = Organizer()
        result = organizer.apply(organizer.plan(self.root), journal=self.journal)
        touch(self.root / "a.pdf", content="replacement")
        undo(self.journal.moves_for(result.session_id))
        self.assertEqual((self.root / "a.pdf").read_text(), "replacement")
        self.assertEqual((self.root / "a (2).pdf").read_text(), "original")

    def test_undo_reports_files_that_moved_away(self):
        touch(self.root / "a.pdf")
        organizer = Organizer()
        result = organizer.apply(organizer.plan(self.root), journal=self.journal)
        (self.root / "_Archive" / "PDF" / "a.pdf").unlink()
        outcome = undo(self.journal.moves_for(result.session_id))
        self.assertEqual(outcome.moved, [])
        self.assertEqual(len(outcome.failed), 1)


class JournalTests(OrganizerTestCase):
    def test_sessions_are_listed_and_forgettable(self):
        touch(self.root / "a.pdf")
        organizer = Organizer()
        result = organizer.apply(organizer.plan(self.root), journal=self.journal)
        self.assertEqual(self.journal.last_session_id(), result.session_id)
        self.assertEqual(len(self.journal.sessions()), 1)
        self.journal.forget(result.session_id)
        self.assertEqual(self.journal.sessions(), [])

    def test_truncated_line_does_not_break_reading(self):
        touch(self.root / "a.pdf")
        organizer = Organizer()
        organizer.apply(organizer.plan(self.root), journal=self.journal)
        with self.journal.path.open("a", encoding="utf-8") as handle:
            handle.write('{"v": 1, "sessi')  # simulate a power loss mid-write
        self.assertEqual(len(list(self.journal.entries())), 1)


class CustomRulesTests(OrganizerTestCase):
    def test_custom_ruleset_is_honoured(self):
        touch(self.root / "bill.pdf")
        organizer = Organizer(ruleset=Ruleset(categories={"Invoices": ["pdf"]}))
        organizer.apply(organizer.plan(self.root))
        self.assertEqual(self.names_under("_Archive", "Invoices"), ["bill.pdf"])


if __name__ == "__main__":
    unittest.main()
