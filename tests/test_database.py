import unittest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._setup import use_temp_db
use_temp_db()

from db import HybridRow  # noqa: E402


class TestHybridRow(unittest.TestCase):
    def test_index_and_key_access(self):
        r = HybridRow(["plan", "count"], ["1mo", 7])
        self.assertEqual(r[0], "1mo")
        self.assertEqual(r["plan"], "1mo")
        self.assertEqual(r[1], 7)
        self.assertEqual(r["count"], 7)

    def test_tuple_style_unpacking(self):
        # aiosqlite.Row iterates over VALUES — code like
        # `for plan, count in rows` depends on this.
        r = HybridRow(["plan", "count"], ["1mo", 7])
        plan, count = r
        self.assertEqual((plan, count), ("1mo", 7))

    def test_dict_conversion(self):
        r = HybridRow(["plan", "count"], ["1mo", 7])
        self.assertEqual(dict(r), {"plan": "1mo", "count": 7})

    def test_list_comprehension_positional(self):
        rows = [HybridRow(["x"], [1]), HybridRow(["x"], [2]), HybridRow(["x"], [3])]
        self.assertEqual([r[0] for r in rows], [1, 2, 3])

    def test_len_and_contains(self):
        r = HybridRow(["a", "b"], [1, 2])
        self.assertEqual(len(r), 2)
        self.assertIn("a", r)
        self.assertNotIn("z", r)


if __name__ == "__main__":
    unittest.main()
