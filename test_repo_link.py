"""Tests for repo_link.py: the GitHub link kept in repo.txt. Offline, standard library only."""

import os
import tempfile
import unittest
from unittest.mock import patch

import repo_link

LINK = "https://github.com/someone/some-repo"


class RepoFileCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = os.path.join(self.temp.name, "repo.txt")
        env = patch.dict(os.environ, {"AFK_REPO_FILE": self.file})
        env.start()
        self.addCleanup(env.stop)

    def write(self, data):
        mode = "wb" if isinstance(data, bytes) else "w"
        kwargs = {} if mode == "wb" else {"encoding": "utf-8", "newline": ""}
        with open(self.file, mode, **kwargs) as handle:
            handle.write(data)


class NormalizeTests(unittest.TestCase):
    def test_the_usual_ways_of_writing_a_repository_link_are_accepted(self):
        cases = {
            LINK: LINK,
            f"  {LINK}  ": LINK,
            "http://github.com/someone/some-repo": LINK,
            "github.com/someone/some-repo": LINK,
            "www.github.com/someone/some-repo/": "https://www.github.com/someone/some-repo/",
            LINK + ".git": LINK + ".git",
            LINK + "/tree/main": LINK + "/tree/main",
            f"<{LINK}>": LINK,
            f'"{LINK}"': LINK,
            "https://GitHub.com/Someone/Some_Repo.js": "https://GitHub.com/Someone/Some_Repo.js",
        }
        for given, expected in cases.items():
            with self.subTest(given=given):
                self.assertEqual(repo_link.normalize(given), expected)

    def test_anything_that_is_not_a_repository_link_is_refused(self):
        refused = [
            "",
            None,
            "   ",
            "someone/some-repo",                       # nothing is guessed
            "https://github.com/someone",               # a profile, not a repository
            "https://github.com/",
            "https://gitlab.com/someone/some-repo",
            "https://github.com.example.test/someone/some-repo",
            "https://example.test/github.com/someone/some-repo",
            "ftp://github.com/someone/some-repo",
            "https://github.com/someone/some repo",
            "https://token@github.com/someone/some-repo",   # no credentials in a link
            "https://user:secret@github.com/someone/some-repo",
            "https://github.com/../some-repo",
            "https://github.com/someone/..",
            "javascript:alert(1)",
            "y",
        ]
        for given in refused:
            with self.subTest(given=given):
                self.assertIsNone(repo_link.normalize(given))


class LoadTests(RepoFileCase):
    def test_a_missing_file_means_no_link_yet(self):
        self.assertEqual(repo_link.load(), (None, "missing"))

    def test_the_link_is_read_from_the_file(self):
        self.write(LINK + "\n")
        self.assertEqual(repo_link.load(), (LINK, ""))

    def test_blank_lines_and_notes_before_the_link_are_skipped(self):
        self.write("\n# my project\n\n   \n" + LINK + "\n# not this one\nhttps://github.com/a/b\n")
        self.assertEqual(repo_link.load(), (LINK, ""))

    def test_windows_line_endings_and_a_missing_scheme_are_fine(self):
        self.write("github.com/someone/some-repo\r\n")
        self.assertEqual(repo_link.load(), (LINK, ""))

    def test_a_byte_order_mark_from_notepad_is_ignored(self):
        self.write(b"\xef\xbb\xbf" + LINK.encode() + b"\r\n")
        self.assertEqual(repo_link.load(), (LINK, ""))

    def test_a_file_saved_as_utf16_is_read(self):
        self.write(b"\xff\xfe" + (LINK + "\r\n").encode("utf-16-le"))
        self.assertEqual(repo_link.load(), (LINK, ""))

    def test_an_empty_file_or_one_with_only_notes_has_no_link(self):
        self.write("")
        self.assertEqual(repo_link.load(), (None, "empty"))
        self.write("# put the link below\n\n")
        self.assertEqual(repo_link.load(), (None, "empty"))

    def test_only_the_first_line_counts(self):
        self.write("hello\n" + LINK + "\n")
        self.assertEqual(repo_link.load(), (None, "invalid"))

    def test_a_file_that_cannot_be_opened_never_raises(self):
        os.mkdir(self.file)  # a folder where the file should be
        self.assertEqual(repo_link.load(), (None, "unreadable"))


class SaveTests(RepoFileCase):
    def test_saving_creates_the_file_with_the_link(self):
        self.assertTrue(repo_link.save(LINK))
        with open(self.file, encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), LINK)
        self.assertEqual(repo_link.load(), (LINK, ""))

    def test_a_link_typed_without_the_scheme_is_saved_in_full(self):
        self.assertTrue(repo_link.save("github.com/someone/some-repo"))
        self.assertEqual(repo_link.load(), (LINK, ""))

    def test_saving_replaces_an_older_link(self):
        self.write("https://github.com/old/one\n")
        self.assertTrue(repo_link.save(LINK))
        self.assertEqual(repo_link.load(), (LINK, ""))

    def test_something_that_is_not_a_link_is_never_saved(self):
        self.assertFalse(repo_link.save("not a link"))
        self.assertFalse(os.path.exists(self.file))

    def test_a_missing_folder_is_created_and_no_temp_file_is_left_behind(self):
        nested = os.path.join(self.temp.name, "a", "b", "repo.txt")
        with patch.dict(os.environ, {"AFK_REPO_FILE": nested}):
            self.assertTrue(repo_link.save(LINK))
            self.assertEqual(repo_link.load(), (LINK, ""))
        self.assertEqual([n for n in os.listdir(os.path.dirname(nested)) if n.endswith(".tmp")], [])

    def test_a_place_that_cannot_be_written_returns_false(self):
        blocker = os.path.join(self.temp.name, "blocker")
        with open(blocker, "w", encoding="utf-8") as handle:
            handle.write("a file, not a folder")
        with patch.dict(os.environ, {"AFK_REPO_FILE": os.path.join(blocker, "repo.txt")}):
            self.assertFalse(repo_link.save(LINK))


class PathTests(unittest.TestCase):
    def test_the_default_is_repo_txt_in_the_project_folder(self):
        with patch.dict(os.environ, {}, clear=False) as env:
            env.pop("AFK_REPO_FILE", None)
            with patch.object(repo_link, "_configured_name", return_value=""):
                self.assertEqual(repo_link.path(), os.path.join(repo_link.PROJECT_DIR, "repo.txt"))
        self.assertTrue(os.path.isfile(os.path.join(repo_link.PROJECT_DIR, "repo_link.py")))

    def test_config_py_can_rename_the_file(self):
        with patch.dict(os.environ, {}, clear=False) as env:
            env.pop("AFK_REPO_FILE", None)
            with patch.object(repo_link, "_configured_name", return_value="my_repo.txt"):
                self.assertEqual(repo_link.path(), os.path.join(repo_link.PROJECT_DIR, "my_repo.txt"))

    def test_the_environment_wins_over_config_py(self):
        with patch.dict(os.environ, {"AFK_REPO_FILE": "other.txt"}):
            with patch.object(repo_link, "_configured_name", return_value="my_repo.txt"):
                self.assertEqual(repo_link.path(), os.path.join(repo_link.PROJECT_DIR, "other.txt"))

    def test_the_shipped_config_names_repo_txt_and_turns_the_step_on(self):
        import config

        self.assertEqual(config.REPO_FILE, "repo.txt")
        self.assertIs(config.OPEN_GITHUB_IMPORT_PAGE, True)


if __name__ == "__main__":
    unittest.main()
