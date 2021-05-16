import unittest

from objective.metadata import util


class TestUtilities(unittest.TestCase):
    def test_sorted_set(self):
        for items in (
            [1, 2, 3],
            [3, 2, 1],
            [1, 3, 2],
        ):
            with self.subTest(items):
                value = util.sorted_set(items)
                self.assertEqual(list(value), sorted(value))
                self.assertIsInstance(value, set)
                self.assertIsInstance(value, util.sorted_set)

        with self.subTest("duplicate items"):
            value = util.sorted_set([1, 2, 3, 2, 1])
            self.assertEqual(list(value), [1, 2, 3])

        with self.subTest("cannot compare"):
            value = util.sorted_set([1, "a"])
            with self.assertRaises(TypeError):
                list(value)
