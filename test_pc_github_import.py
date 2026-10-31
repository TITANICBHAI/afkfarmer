"""Offline tests for the import-page step of PCAutomation, with a hand-made fake page.

No browser is started. The real-browser versions of these scenarios (against the
simulated replit.com/import/github page) are in test_site_simulation.py.
"""

import contextlib
import io
import unittest
from unittest.mock import patch

from pc_automation import PCAutomation

LINK = "https://github.com/someone/some-repo"
IMPORT_URL = "https://replit.com/import/github"


class FakeBox:
    def __init__(self, visible=True, keeps_value=True, fails=False):
        self.visible = visible
        self.keeps_value = keeps_value
        self.fails = fails
        self.value = ""
        self.clicks = 0
        self.fills = 0

    @property
    def first(self):
        return self

    def is_visible(self, *_args, **_kwargs):
        return self.visible

    def fill(self, value):
        self.fills += 1
        if self.fails:
            raise RuntimeError("the box went away")
        if self.keeps_value:
            self.value = value

    def input_value(self):
        return self.value

    def click(self, *_args, **_kwargs):
        self.clicks += 1


class NoBox(FakeBox):
    def __init__(self):
        super().__init__(visible=False)


class FakePage:
    """Just enough of a Playwright page: a URL, goto(), timers and locators."""

    def __init__(self, box=None, lands_on=IMPORT_URL, goto_error=None):
        self.url = "about:blank"
        self.box = box if box is not None else FakeBox()
        self.lands_on = lands_on
        self.goto_error = goto_error
        self.visited = []
        self.waited_ms = 0
        self.selectors = []
        self.clicks = 0

    def set_default_timeout(self, _ms):
        pass

    def bring_to_front(self):
        pass

    def goto(self, url, wait_until=None):
        self.visited.append(url)
        if self.goto_error:
            raise self.goto_error
        self.url = self.lands_on

    def wait_for_timeout(self, ms):
        self.waited_ms += ms

    def locator(self, selector):
        self.selectors.append(selector)
        return self.box

    def get_by_label(self, text):
        self.selectors.append(f"label:{text}")
        return self.box

    def screenshot(self, **_kwargs):
        pass


class ImportPageCase(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        # Failure evidence (a screenshot file) is not wanted in an offline test.
        evidence = patch.object(PCAutomation, "_save_failure")
        evidence.start()
        self.addCleanup(evidence.stop)

    def make(self, page):
        pc = PCAutomation("new@example.test", "pw")
        pc.context = None  # _page_for_replit() then uses the page given here
        pc.page = page
        return pc

    def call(self, fn, *args):
        with contextlib.redirect_stdout(self.output):
            return fn(*args)

    @property
    def log(self):
        return self.output.getvalue()


class OpenTests(ImportPageCase):
    def test_the_tab_is_taken_to_the_import_page(self):
        page = FakePage()
        pc = self.make(page)
        self.assertTrue(self.call(pc.open_github_import_page))
        self.assertEqual(page.visited, [IMPORT_URL])
        self.assertEqual(PCAutomation.GITHUB_IMPORT_URL, IMPORT_URL)

    def test_a_signed_out_visitor_is_sent_to_login_and_that_is_reported(self):
        page = FakePage(lands_on="https://replit.com/login?goto=%2Fimport%2Fgithub")
        pc = self.make(page)
        self.assertFalse(self.call(pc.open_github_import_page))
        self.assertIn("instead of the import page", self.log)
        self.assertIn("signed in", self.log)

    def test_it_does_not_wait_when_the_box_is_already_there(self):
        page = FakePage()
        pc = self.make(page)
        self.assertTrue(self.call(pc.open_github_import_page))
        self.assertEqual(page.waited_ms, 0)

    def test_a_page_that_is_still_loading_counts_as_open_after_the_settle_time(self):
        page = FakePage(box=NoBox())
        pc = self.make(page)
        self.assertTrue(self.call(pc.open_github_import_page))
        self.assertGreaterEqual(page.waited_ms, PCAutomation.GITHUB_IMPORT_SETTLE_MS)
        self.assertLess(page.waited_ms, PCAutomation.GITHUB_IMPORT_SETTLE_MS + 1_000)

    def test_a_redirect_that_comes_a_second_after_the_load_is_noticed(self):
        page = FakePage(box=NoBox())
        pc = self.make(page)
        original = page.wait_for_timeout

        def redirect_after_a_second(ms):
            original(ms)
            if page.waited_ms >= 1_000:
                page.url = "https://replit.com/login"

        page.wait_for_timeout = redirect_after_a_second
        self.assertFalse(self.call(pc.open_github_import_page))
        self.assertLess(page.waited_ms, PCAutomation.GITHUB_IMPORT_SETTLE_MS)
        self.assertIn("instead of the import page", self.log)

    def test_a_page_that_cannot_be_loaded_never_raises(self):
        page = FakePage(goto_error=RuntimeError("net::ERR_INTERNET_DISCONNECTED"))
        pc = self.make(page)
        self.assertFalse(self.call(pc.open_github_import_page))
        self.assertIn("ERR_INTERNET_DISCONNECTED", self.log)

    def test_no_browser_at_all_never_raises(self):
        pc = PCAutomation("new@example.test", "pw")
        pc.context = None
        pc.page = None
        self.assertFalse(self.call(pc.open_github_import_page))
        self.assertIn("Could not open", self.log)


class EnterTests(ImportPageCase):
    def on_import_page(self, box=None):
        page = FakePage(box=box)
        page.url = IMPORT_URL
        return page, self.make(page)

    def test_the_link_is_typed_into_the_box_and_nothing_is_clicked(self):
        box = FakeBox()
        page, pc = self.on_import_page(box)
        self.assertTrue(self.call(pc.enter_github_repo_url, LINK))
        self.assertEqual(box.value, LINK)
        self.assertEqual(box.clicks, 0)
        self.assertEqual(page.clicks, 0)
        self.assertIn(LINK, self.log)
        self.assertIn("nothing was clicked", self.log)

    def test_the_box_is_looked_for_by_its_placeholder_and_its_label(self):
        page, pc = self.on_import_page(FakeBox())
        self.call(pc.enter_github_repo_url, LINK)
        self.assertIn('input[placeholder*="github.com" i]', page.selectors)

    def test_a_box_that_shows_up_late_is_waited_for(self):
        box = FakeBox(visible=False)
        page, pc = self.on_import_page(box)
        original = page.wait_for_timeout

        def appear_after_three_polls(ms):
            original(ms)
            if page.waited_ms >= 3 * PCAutomation.GITHUB_IMPORT_POLL_MS:
                box.visible = True

        page.wait_for_timeout = appear_after_three_polls
        self.assertTrue(self.call(pc.enter_github_repo_url, LINK))
        self.assertEqual(box.value, LINK)

    def test_a_box_that_never_shows_gives_the_link_back_to_paste_by_hand(self):
        page, pc = self.on_import_page(NoBox())
        with patch.object(PCAutomation, "GITHUB_IMPORT_LOAD_WAIT_MS", 2_000):
            self.assertFalse(self.call(pc.enter_github_repo_url, LINK))
        self.assertGreaterEqual(page.waited_ms, 2_000)
        self.assertIn("Paste the link yourself", self.log)
        self.assertIn(LINK, self.log)

    def test_when_replit_moved_the_tab_away_it_stops_at_once(self):
        page = FakePage(box=NoBox())
        page.url = "https://replit.com/login"
        pc = self.make(page)
        self.assertFalse(self.call(pc.enter_github_repo_url, LINK))
        self.assertEqual(page.waited_ms, 0)  # no 30 second wait for a box that cannot come
        self.assertIn("not on the import page", self.log)
        self.assertIn(LINK, self.log)

    def test_a_page_that_does_not_keep_the_value_is_reported(self):
        box = FakeBox(keeps_value=False)
        _page, pc = self.on_import_page(box)
        self.assertFalse(self.call(pc.enter_github_repo_url, LINK))
        self.assertEqual(box.fills, 2)  # one retry, like every other field
        self.assertIn("did not keep the link", self.log)
        self.assertIn(LINK, self.log)

    def test_an_error_while_typing_never_raises(self):
        _page, pc = self.on_import_page(FakeBox(fails=True))
        self.assertFalse(self.call(pc.enter_github_repo_url, LINK))
        self.assertIn("the box went away", self.log)
        self.assertIn(LINK, self.log)

    def test_the_next_way_of_finding_the_box_is_tried_when_one_breaks(self):
        good = FakeBox()
        page, pc = self.on_import_page(good)

        class Mixed(FakePage):
            def locator(self, selector):
                self.selectors.append(selector)
                raise RuntimeError("selector engine error")

        mixed = Mixed(box=good)
        mixed.url = IMPORT_URL
        pc = self.make(mixed)
        self.assertTrue(self.call(pc.enter_github_repo_url, LINK))  # found through the label
        self.assertEqual(good.value, LINK)


if __name__ == "__main__":
    unittest.main()
