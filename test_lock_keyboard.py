import copy
import importlib.machinery
import importlib.util
from pathlib import Path
import shlex
import unittest
from unittest import mock

loader = importlib.machinery.SourceFileLoader("keyboard_lock", str(Path(__file__).with_name("lock-keyboard")))
spec = importlib.util.spec_from_loader(loader.name, loader)
lock = importlib.util.module_from_spec(spec)
loader.exec_module(lock)


class RuleSimulator:
    """Small first-match rule model; not a substitute for physical HID tests."""

    def __init__(self):
        self.rule = lock.make_rule()
        self.variables = {}
        self.held = {}

    def actions(self, actions):
        for action in actions:
            if "set_variable" in action:
                item = action["set_variable"]
                self.variables[item["name"]] = item["value"]

    def event(self, key, down=True, category="key_code", modifiers=()):
        if not down:
            rule = self.held.pop(key, None)
            if rule:
                self.actions(rule.get("to_after_key_up", []))
            return
        if key in self.held:
            return  # Repeat belongs to the original manipulated press.
        for rule in self.rule["manipulators"]:
            source = rule["from"]
            if source.get("any") != category and source.get(category) != key:
                continue
            if any((self.variables.get(c["name"], 0) == c["value"]) !=
                   (c["type"] == "variable_if") for c in rule.get("conditions", [])):
                continue
            allowed = source.get("modifiers", {}).get("optional", [])
            if "any" not in allowed and not set(modifiers).issubset(allowed):
                continue
            self.held[key] = rule
            self.actions(rule.get("to", []))
            return [a for a in rule.get("to", []) if a.get("key_code") not in (None, "vk_none")]
        return [{"key_code": key}]


class KeyboardLockTests(unittest.TestCase):
    def config(self):
        return {"profiles": [{"name": "Default", "selected": True,
                             "simple_modifications": [copy.deepcopy(lock.CAPS_MAPPING)],
                             "complex_modifications": {"rules": [{"description": "Keep me", "manipulators": []}]}},
                            {"name": "Other", "selected": False, "untouched": True}]}

    def test_install_preserves_unrelated_config(self):
        original = self.config()
        updated = lock.prepare_install(original)
        self.assertEqual(updated["profiles"][1], original["profiles"][1])
        self.assertEqual(updated["profiles"][0]["complex_modifications"]["rules"][1:],
                         original["profiles"][0]["complex_modifications"]["rules"])
        self.assertEqual(original, self.config())
        self.assertEqual(updated["profiles"][0]["simple_modifications"], [])

    def test_install_idempotent_and_uninstall_round_trip(self):
        original = self.config()
        installed = lock.prepare_install(original)
        self.assertEqual(lock.prepare_install(installed), installed)
        self.assertEqual(lock.prepare_uninstall(installed), original)

    def test_unfamiliar_caps_mapping_refused(self):
        config = self.config()
        config["profiles"][0]["simple_modifications"][0]["to"] = [{"key_code": "a"}]
        with self.assertRaises(RuntimeError):
            lock.prepare_install(config)

    def test_caps_is_escape_without_locking(self):
        model = RuleSimulator()
        self.assertEqual(model.event("caps_lock"), [{"key_code": "escape"}])
        self.assertEqual(model.variables.get(lock.VARIABLE, 0), 0)

    def test_lock_swallows_typing_shortcuts_media_fn_and_caps(self):
        model = RuleSimulator()
        self.assertEqual(model.event("escape"), [])
        model.event("escape", False)
        self.assertEqual(model.variables[lock.VARIABLE], 1)
        for key, category in [("a", "key_code"), ("left_command", "key_code"),
                              ("caps_lock", "key_code"), ("volume_increment", "consumer_key_code"),
                              ("mission_control", "apple_vendor_keyboard_key_code"),
                              ("keyboard_fn", "apple_vendor_top_case_key_code")]:
            self.assertEqual(model.event(key, category=category, modifiers=("command",)), [])
            model.event(key, False)
        self.assertFalse(any(m["from"].get("any") == "pointing_button"
                             for m in model.rule["manipulators"]))

    def test_either_shift_unlocks_on_escape_release(self):
        for shift in ["left_shift", "right_shift"]:
            model = RuleSimulator()
            model.event("escape")
            model.event("escape", False)
            self.assertEqual(model.event(shift), [])
            self.assertEqual(model.event("escape"), [])
            self.assertEqual(model.variables[lock.VARIABLE], 1)
            model.event(shift, False)
            model.event("escape", False)
            self.assertEqual(model.variables[lock.VARIABLE], 0)
            self.assertEqual(model.event("a"), [{"key_code": "a"}])

    def test_shift_tap_and_plain_escape_do_not_unlock(self):
        model = RuleSimulator()
        model.event("escape")
        model.event("escape", False)
        model.event("left_shift")
        model.event("left_shift", False)
        model.event("escape")
        model.event("escape", False)
        self.assertEqual(model.variables[lock.VARIABLE], 1)

    def test_modified_escape_does_not_lock_when_unlocked(self):
        model = RuleSimulator()
        self.assertEqual(model.event("escape", modifiers=("shift",)), [{"key_code": "escape"}])
        self.assertEqual(model.variables.get(lock.VARIABLE, 0), 0)

    def test_held_escape_repeat_does_not_relock(self):
        model = RuleSimulator()
        model.event("escape")
        model.event("escape", False)
        model.event("left_shift")
        model.event("escape")
        model.event("escape")
        self.assertEqual(model.variables[lock.VARIABLE], 1)
        model.event("escape", False)
        self.assertEqual(model.variables[lock.VARIABLE], 0)

    def test_unlock_does_not_depend_on_shell_command(self):
        # Karabiner kills a running shell_command when the next one starts, so
        # the lock state must live entirely in the rule's own variables.
        model = RuleSimulator()
        model.event("escape")
        model.event("escape", False)
        for manipulator in model.rule["manipulators"]:
            for action in manipulator.get("to", []) + manipulator.get("to_after_key_up", []):
                if "shell_command" in action:
                    action["shell_command"] = "exit 1"
        model.event("right_shift")
        model.event("escape")
        model.event("escape", False)
        self.assertEqual(model.variables[lock.VARIABLE], 0)

    def test_indicator_is_one_exec_of_led_helper(self):
        commands = [a["shell_command"] for m in lock.make_rule()["manipulators"]
                    for a in m.get("to", []) + m.get("to_after_key_up", [])
                    if "shell_command" in a]
        self.assertEqual(len(commands), 3)  # Lock, plus unlock for either Shift.
        for command in commands:
            argv = shlex.split(command)
            self.assertEqual(argv[:2], ["exec", str(lock.LED_HELPER)])
            self.assertIn(argv[2], ["on", "off"])
            self.assertEqual(argv[3:5], ["/usr/bin/osascript", "-e"])
            self.assertIn(lock.MESSAGES[argv[2]], argv[5])


class TransitionTests(unittest.TestCase):
    def setUp(self):
        self.operations = []
        self.led_error = None
        self.patches = [
            mock.patch.object(lock, "set_lock", side_effect=self.set_lock),
            mock.patch.object(lock, "set_led", side_effect=self.set_led),
            mock.patch.object(lock, "notify", side_effect=self.notify),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()

    def set_lock(self, state):
        self.operations.append(("locked", state))

    def set_led(self, state):
        if self.led_error:
            raise self.led_error
        self.operations.append(("led", state))

    def notify(self, state):
        self.operations.append(("notify", state))

    def test_lock_state_changes_before_led(self):
        lock.transition(True)
        lock.transition(False)
        self.assertEqual(self.operations, [("locked", True), ("led", "on"), ("notify", "on"),
                                           ("locked", False), ("led", "off"), ("notify", "off")])

    def test_led_failure_still_unlocks_and_notifies(self):
        self.led_error = OSError("Helper missing")
        with self.assertRaises(OSError):
            lock.transition(False)
        self.assertEqual(self.operations, [("locked", False), ("notify", "off")])


if __name__ == "__main__":
    unittest.main()
