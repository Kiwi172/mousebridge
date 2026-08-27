import json
import os
import tempfile
import unittest

from mb import config as config_module
from mb.config import ConfigError


def write(cfg):
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(cfg, handle)
    handle.close()
    return handle.name


class Validation(unittest.TestCase):
    def base(self, **overrides):
        cfg = {
            "cluster": "test", "node": "desk",
            "peers": {"laptop": "10.0.0.5"},
            "layout": {"desk": {"right": "laptop"}, "laptop": {"left": "desk"}},
        }
        cfg.update(overrides)
        return config_module._merge(config_module.DEFAULTS, cfg)

    def test_a_good_config_validates(self):
        config_module.validate(self.base())

    def test_asymmetric_layout_is_rejected(self):
        """If the cursor can go right it must be able to come back left, or it
        would be possible to strand it on a machine with no way home."""
        cfg = self.base(layout={"desk": {"right": "laptop"}, "laptop": {}})
        with self.assertRaises(ConfigError) as caught:
            config_module.validate(cfg)
        self.assertIn("asymmetric", str(caught.exception))

    def test_layout_naming_an_unknown_machine_is_rejected(self):
        cfg = self.base(layout={"desk": {"right": "ghost"}, "laptop": {"left": "desk"}})
        with self.assertRaises(ConfigError):
            config_module.validate(cfg)

    def test_layout_entry_without_an_address_is_rejected(self):
        cfg = self.base(peers={})
        with self.assertRaises(ConfigError) as caught:
            config_module.validate(cfg)
        self.assertIn("no address", str(caught.exception))

    def test_this_node_must_appear_in_the_layout(self):
        cfg = self.base(node="somewhere-else")
        with self.assertRaises(ConfigError):
            config_module.validate(cfg)

    def test_bad_direction_is_rejected(self):
        cfg = self.base(layout={"desk": {"sideways": "laptop"}, "laptop": {"left": "desk"}})
        with self.assertRaises(ConfigError):
            config_module.validate(cfg)

    def test_bad_hotkey_is_rejected(self):
        cfg = self.base(hotkeys={"switch_left": "ctrl+nonsense"})
        with self.assertRaises(ValueError):
            config_module.validate(cfg)

    def test_peer_address_defaults_to_the_cluster_port(self):
        cfg = self.base()
        self.assertEqual(config_module.peer_address(cfg, "laptop"),
                         ("10.0.0.5", config_module.DEFAULT_PORT))
        cfg["peers"]["laptop"] = "10.0.0.5:9999"
        self.assertEqual(config_module.peer_address(cfg, "laptop"), ("10.0.0.5", 9999))

    def test_defaults_are_filled_in(self):
        path = write({"cluster": "c", "node": "desk", "peers": {}, "layout": {}})
        try:
            cfg = config_module.load(path)
            self.assertTrue(cfg["clipboard"]["enabled"])
            self.assertEqual(cfg["port"], config_module.DEFAULT_PORT)
        finally:
            os.unlink(path)


class Secrets(unittest.TestCase):
    def test_generated_secret_has_enough_entropy(self):
        secret = config_module.generate_secret()
        # 16 characters from a 31-symbol alphabet is a little over 79 bits.
        self.assertEqual(len(secret.replace("-", "")), 16)
        self.assertNotEqual(secret, config_module.generate_secret())

    def test_secrets_are_not_stored_in_the_config(self):
        """The config is meant to be copyable; the key to your keyboard is not."""
        self.assertNotIn("secret", config_module.DEFAULTS)


if __name__ == "__main__":
    unittest.main()
