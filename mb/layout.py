"""Where the cursor is, and what happens when it runs off an edge.

The cluster is a graph, not a line: each node names the neighbour on each of
its four sides. That is a superset of Mouse Without Borders' four-in-a-row and
it costs nothing extra, because the interesting logic is the same either way --
integrate the mouse delta, and when the result leaves the current screen, hand
it to the neighbour at a proportionally equivalent height.
"""

from .config import DIRECTIONS, OPPOSITE


class Cluster:
    """Screen geometry plus the edge graph. One instance per running node."""

    def __init__(self, layout, screens, corner_size=40, wrap=False):
        self.layout = layout
        self.screens = dict(screens)     # node -> (width, height)
        self.corner_size = corner_size
        self.wrap = wrap

    def known(self, node):
        return node in self.screens

    def set_screen(self, node, width, height):
        self.screens[node] = (int(width), int(height))

    def neighbour(self, node, direction):
        return self.layout.get(node, {}).get(direction)

    def _in_corner(self, node, x, y, direction):
        """True if the crossing point is close enough to a corner that the user
        was probably reaching for a corner UI element, not the next machine."""
        if self.corner_size <= 0:
            return False
        width, height = self.screens[node]
        c = self.corner_size
        if direction in ("left", "right"):
            return y < c or y > height - c
        return x < c or x > width - c

    def step(self, node, x, y):
        """Integrate a cursor position that may have left the screen.

        Returns (node, x, y, crossed) where `crossed` is True if the cursor
        changed machines. Position is clamped to the final screen either way.
        """
        crossed = False
        # Loop, so one violent flick of the mouse can cross more than one screen.
        for _ in range(len(self.screens) + 1):
            width, height = self.screens[node]
            direction = None
            if x < 0:
                direction = "left"
            elif x > width - 1:
                direction = "right"
            elif y < 0:
                direction = "up"
            elif y > height - 1:
                direction = "down"
            if direction is None:
                break

            target = self.neighbour(node, direction)
            probe_x = min(max(x, 0), width - 1)
            probe_y = min(max(y, 0), height - 1)
            if target is None or not self.known(target) or \
                    self._in_corner(node, probe_x, probe_y, direction):
                x, y = probe_x, probe_y
                break

            new_w, new_h = self.screens[target]
            if direction in ("left", "right"):
                overshoot = -x - 1 if direction == "left" else x - width
                y = _scale(y, height, new_h)
                x = new_w - 1 - overshoot if direction == "left" else overshoot
            else:
                overshoot = -y - 1 if direction == "up" else y - height
                x = _scale(x, width, new_w)
                y = new_h - 1 - overshoot if direction == "up" else overshoot
            node = target
            crossed = True
        else:
            # More transitions than there are screens: the layout has a cycle
            # the cursor is spinning around. Stop where we are.
            width, height = self.screens[node]
            x, y = min(max(x, 0), width - 1), min(max(y, 0), height - 1)
        return node, int(x), int(y), crossed

    def at_edge(self, node, x, y):
        """Which edge the *physical* cursor is resting against, or None.

        This is a different question from the one `step` answers. A real
        cursor is clamped by the OS and can never go past width-1, so waiting
        for it to exceed the bounds would wait forever; it has arrived when it
        is *on* the last pixel. The virtual cursor in `step` has no such limit,
        because we integrate its position ourselves.
        """
        width, height = self.screens[node]
        for direction, hit in (
            ("left", x <= 0), ("right", x >= width - 1),
            ("up", y <= 0), ("down", y >= height - 1),
        ):
            if not hit:
                continue
            target = self.neighbour(node, direction)
            if target is None or not self.known(target):
                continue
            if self._in_corner(node, x, y, direction):
                continue
            return direction
        return None

    def cross(self, node, direction, x, y):
        """Where the cursor lands on the neighbour after leaving `node`."""
        target = self.neighbour(node, direction)
        width, height = self.screens[node]
        new_w, new_h = self.screens[target]
        if direction in ("left", "right"):
            new_y = _scale(y, height, new_h)
            return target, (new_w - 1 if direction == "left" else 0), new_y
        new_x = _scale(x, width, new_w)
        return target, new_x, (new_h - 1 if direction == "up" else 0)

    def jump(self, node, direction, x, y):
        """Hotkey switch: go to the neighbour in `direction` regardless of where
        the cursor is, landing at the proportionally equivalent point."""
        target = self.neighbour(node, direction)
        if target is None or not self.known(target):
            return node, x, y, False
        width, height = self.screens[node]
        new_w, new_h = self.screens[target]
        return target, _scale(x, width, new_w), _scale(y, height, new_h), True

    def entry_point(self, node, direction):
        """Middle of the edge you arrive at when entering `node` from `direction`."""
        width, height = self.screens[node]
        if direction == "left":
            return 0, height // 2
        if direction == "right":
            return width - 1, height // 2
        if direction == "up":
            return width // 2, 0
        return width // 2, height - 1

    def describe(self, node):
        edges = self.layout.get(node, {})
        if not edges:
            return f"{node} (no neighbours configured)"
        parts = [f"{d}: {edges[d]}" for d in DIRECTIONS if d in edges]
        return f"{node} -> " + ", ".join(parts)


def _scale(value, old_span, new_span):
    if old_span <= 1:
        return new_span // 2
    scaled = round(value * (new_span - 1) / (old_span - 1))
    return min(max(scaled, 0), new_span - 1)
