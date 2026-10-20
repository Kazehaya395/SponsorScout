"""Round 10c speed: the list-page scroll stops after one jump on static listings.

Work in Austria spent ~5.7 s per list page in the fixed 4-step scroll (113
pages). A static page never grows, so the single jump is enough. A page that
grows (lazy loading) must still get the full walk.
"""
from sponsorscout.scanning.career import career_scanner as cs


class FakeTarget:
    def __init__(self, heights):
        self.heights = list(heights)
        self.scrolls = 0
        self.last = self.heights[0]

    def evaluate(self, js):
        if "scrollHeight" in js and "scrollTo" not in js:
            if self.heights:
                self.last = self.heights.pop(0)
            return self.last
        self.scrolls += 1
        return None


class FakePage:
    def __init__(self):
        self.waits = 0

    def wait_for_timeout(self, ms):
        self.waits += 1


def _scanner():
    return cs.CareerPortalScanner(input_csv="/dev/null", output_csv="/tmp/_r10c_speed.csv",
                                  skip_preflight=True)


def test_static_listing_takes_one_scroll_jump():
    # prev height 1000, after the jump still 1000 -> static, stop.
    t = FakeTarget([1000, 1000])
    page = FakePage()
    assert _scanner().progressive_scroll_and_wait(page, t) is False
    assert t.scrolls == 1
    assert page.waits == 1


def test_growing_listing_gets_full_walk():
    # prev 1000, jump grows it to 2000, then the walk ends at 2400.
    t = FakeTarget([1000, 2000, 2400])
    page = FakePage()
    assert _scanner().progressive_scroll_and_wait(page, t) is True
    assert t.scrolls == 1 + 4  # jump + the four walk steps
