import unittest

from mb.layout import Cluster

SCREENS = {"desk": (1920, 1080), "laptop": (2560, 1440), "tablet": (1280, 720)}
LAYOUT = {
    "desk": {"right": "laptop"},
    "laptop": {"left": "desk", "right": "tablet"},
    "tablet": {"left": "laptop"},
}


class VirtualCursor(unittest.TestCase):
    def setUp(self):
        self.cluster = Cluster(LAYOUT, SCREENS, corner_size=40)

    def test_crossing_right_lands_on_the_left_edge(self):
        node, x, y, crossed = self.cluster.step("desk", 1920, 540)
        self.assertEqual((node, x, crossed), ("laptop", 0, True))

    def test_height_is_scaled_proportionally(self):
        # No corner guard here: the guard deliberately refuses to cross at the
        # very top and bottom, which is exactly where scaling is most visible.
        cluster = Cluster(LAYOUT, SCREENS, corner_size=0)
        self.assertEqual(cluster.step("desk", 1920, 1079)[2], 1439)
        self.assertEqual(cluster.step("desk", 1920, 0)[2], 0)
        self.assertEqual(cluster.step("desk", 1920, 540)[2], 720)

    def test_corner_guard_refuses_to_cross_near_a_corner(self):
        node, _, _, crossed = self.cluster.step("desk", 1920, 1079)
        self.assertEqual((node, crossed), ("desk", False))

    def test_crossing_back_returns_to_the_same_place(self):
        node, x, y, _ = self.cluster.step("desk", 1920, 540)
        back, bx, by, _ = self.cluster.step(node, -1, y)
        self.assertEqual(back, "desk")
        self.assertEqual((bx, by), (1919, 540))

    def test_one_flick_can_cross_two_screens(self):
        node, _, _, crossed = self.cluster.step("desk", 1920 + 2600, 540)
        self.assertEqual((node, crossed), ("tablet", True))

    def test_no_neighbour_clamps_instead_of_crossing(self):
        node, x, y, crossed = self.cluster.step("tablet", 1400, 300)
        self.assertEqual((node, x, crossed), ("tablet", 1279, False))

    def test_a_cycle_in_the_layout_does_not_spin_forever(self):
        cluster = Cluster(
            {"a": {"right": "b"}, "b": {"left": "a", "right": "a"}},
            {"a": (100, 100), "b": (100, 100)}, corner_size=0)
        node, x, y, _ = cluster.step("a", 10_000, 50)
        self.assertIn(node, ("a", "b"))
        self.assertTrue(0 <= x < 100)


class PhysicalCursor(unittest.TestCase):
    """The real cursor is clamped by the OS, so it rests on the edge pixel
    rather than passing it. at_edge must notice that; step never would."""

    def setUp(self):
        self.cluster = Cluster(LAYOUT, SCREENS, corner_size=40)

    def test_resting_on_the_last_pixel_counts_as_an_edge(self):
        self.assertEqual(self.cluster.at_edge("desk", 1919, 540), "right")

    def test_one_pixel_short_does_not(self):
        self.assertIsNone(self.cluster.at_edge("desk", 1918, 540))

    def test_corners_are_reserved_for_the_local_machine(self):
        self.assertIsNone(self.cluster.at_edge("desk", 1919, 10))
        self.assertIsNone(self.cluster.at_edge("desk", 1919, 1070))

    def test_an_edge_with_no_neighbour_is_not_an_edge(self):
        self.assertIsNone(self.cluster.at_edge("desk", 0, 540))

    def test_cross_lands_just_inside_the_neighbour(self):
        self.assertEqual(self.cluster.cross("desk", "right", 1919, 540), ("laptop", 0, 720))
        self.assertEqual(self.cluster.cross("laptop", "left", 0, 720), ("desk", 1919, 540))


class Hotkeys(unittest.TestCase):
    def test_jump_moves_without_touching_the_edge(self):
        cluster = Cluster(LAYOUT, SCREENS)
        node, x, y, moved = cluster.jump("desk", "right", 960, 540)
        self.assertEqual((node, moved), ("laptop", True))
        self.assertEqual((x, y), (1280, 720))

    def test_jump_into_nothing_is_a_no_op(self):
        cluster = Cluster(LAYOUT, SCREENS)
        self.assertEqual(cluster.jump("desk", "left", 960, 540), ("desk", 960, 540, False))


if __name__ == "__main__":
    unittest.main()
