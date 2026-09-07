import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from wepeiyang_agent.adb import AdbClient
from wepeiyang_agent.app_ui import AppNavigator
from wepeiyang_agent.skills import SkillRegistry
from wepeiyang_agent.trace import Trace


def screen(label="成绩", class_name="android.widget.Button"):
    return (f'<hierarchy><node package="com.twt.service" bounds="[0,0][400,800]" class="android.widget.FrameLayout">'
        f'<node package="com.twt.service" bounds="[0,60][400,700]" scrollable="true" class="android.widget.ScrollView">'
        f'<node package="com.twt.service" bounds="[20,100][180,170]" text="{label}" clickable="true" class="{class_name}" />'
        '</node></node></hierarchy>').encode()


class AppUiTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.trace = Trace(self.root / "traces")
        png = io.BytesIO()
        Image.new("RGB", (400, 800)).save(png, format="PNG")
        self.adb = Mock()
        self.adb.foreground_package.return_value = "com.twt.service"
        self.adb.hierarchy.return_value = screen()
        self.adb.screenshot.return_value = png.getvalue()
        self.forum = SimpleNamespace(adb=self.adb, _sleep=lambda: None)
        self.nav = AppNavigator(self.forum, self.root, self.trace)

    def test_observe_then_tap_reads_new_screen_and_invalidates_old_cursor(self):
        initial = self.nav.observe()
        (self.root / "forum-cursor.json").write_text("{}")
        self.adb.tap.side_effect = lambda *args: setattr(self.adb.hierarchy, "return_value", screen("学期"))
        result = self.nav.invoke("tap", {"screen_id": initial["screen_id"], "node_id": "n2"})
        self.adb.tap.assert_called_once_with(100, 135)
        self.assertTrue(result["page_changed"])
        self.assertNotEqual(result["screen_id"], initial["screen_id"])
        self.assertTrue(Path(result["image"]).exists())
        self.assertFalse((self.root / "forum-cursor.json").exists())
        with self.assertRaises(ValueError):
            self.nav.invoke("tap", {"screen_id": initial["screen_id"], "node_id": "n2"})

    def test_observed_coordinate_tap(self):
        initial = self.nav.observe()
        self.nav.invoke("tap", {"screen_id": initial["screen_id"], "x": 100, "y": 130})
        self.adb.tap.assert_called_once_with(100, 130)

    def test_all_swipe_directions_and_back_use_adb(self):
        for direction in ("up", "down", "left", "right"):
            initial = self.nav.observe()
            self.nav.invoke("swipe", {"screen_id": initial["screen_id"], "direction": direction})
        self.assertEqual(self.adb.swipe.call_count, 4)
        args = self.adb.swipe.call_args_list[0].args
        self.assertGreater(args[1], args[3])
        initial = self.nav.observe()
        self.nav.invoke("back", {"screen_id": initial["screen_id"]})
        self.adb.shell.assert_called_with("input", "keyevent", "4")

    def test_other_foreground_app_is_never_captured_or_clicked(self):
        self.adb.foreground_package.return_value = "com.android.settings"
        with self.assertRaises(ValueError):
            self.nav.observe()
        self.adb.hierarchy.assert_not_called()
        self.adb.screenshot.assert_not_called()

    def test_live_hierarchy_change_rejects_old_coordinates(self):
        initial = self.nav.observe()
        self.adb.hierarchy.return_value = screen("发布")
        with self.assertRaisesRegex(ValueError, "页面已经变化"):
            self.nav.invoke("tap", {"screen_id": initial["screen_id"], "node_id": "n2"})
        self.adb.tap.assert_not_called()

    def test_unsafe_labels_and_editable_controls_are_blocked(self):
        for label, kind in [("点赞", "Button"), ("发布", "Button"), ("登录", "Button"),
                            ("提交", "Button"), ("开启通知", "Switch"), ("搜索", "EditText")]:
            self.adb.hierarchy.return_value = screen(label, "android.widget." + kind)
            initial = self.nav.observe()
            with self.subTest(label=label), self.assertRaises(ValueError):
                self.nav.invoke("tap", {"screen_id": initial["screen_id"], "node_id": "n2"})
        self.adb.tap.assert_not_called()

    def test_out_of_bounds_and_unobserved_regions_are_blocked(self):
        for point in ((400, 50), (100, 750), (-1, 50)):
            initial = self.nav.observe()
            with self.assertRaises(ValueError):
                self.nav.invoke("tap", {"screen_id": initial["screen_id"], "x": point[0], "y": point[1]})
        self.adb.tap.assert_not_called()

    def test_open_does_not_force_stop_or_reset_app(self):
        self.nav.invoke("open", {})
        self.adb.start_app.assert_not_called()
        self.assertEqual(self.adb.shell.call_args.args[:3], ("am", "start", "-W"))

    def test_leaving_app_after_back_does_not_capture_other_app(self):
        initial = self.nav.observe()
        self.adb.screenshot.reset_mock()
        self.adb.shell.side_effect = lambda *args: setattr(self.adb.foreground_package, "return_value", "com.android.launcher")
        result = self.nav.invoke("back", {"screen_id": initial["screen_id"]})
        self.assertFalse(result["in_app"])
        self.assertTrue(result["requires_user"])
        self.adb.screenshot.assert_not_called()
        self.assertFalse(self.nav.state_path.exists())

    def test_snapshot_consumed_even_if_adb_action_fails(self):
        initial = self.nav.observe()
        self.adb.tap.side_effect = RuntimeError("connection lost")
        with self.assertRaises(RuntimeError):
            self.nav.invoke("tap", {"screen_id": initial["screen_id"], "node_id": "n2"})
        self.assertFalse(self.nav.state_path.exists())

    def test_unlabelled_forum_interaction_inside_card_is_blocked(self):
        self.adb.hierarchy.return_value = ('<hierarchy><node package="com.twt.service" bounds="[0,0][400,800]">'
            '<node package="com.twt.service" text="#MP123" clickable="true" bounds="[0,100][400,400]">'
            '<node package="com.twt.service" clickable="true" bounds="[20,350][60,390]" />'
            '</node></node></hierarchy>').encode()
        initial = self.nav.observe()
        with self.assertRaises(ValueError):
            self.nav.invoke("tap", {"screen_id": initial["screen_id"], "node_id": "n2"})
        self.adb.tap.assert_not_called()

    def test_snapshot_expiry_and_missing_snapshot(self):
        with self.assertRaises(ValueError):
            self.nav.invoke("back", {"screen_id": "missing"})
        initial = self.nav.observe()
        with patch("wepeiyang_agent.app_ui.time.time", return_value=initial["captured_at"] + 301):
            with self.assertRaises(ValueError):
                self.nav.invoke("back", {"screen_id": initial["screen_id"]})

    def test_tap_arguments_require_exactly_one_target(self):
        registry = SkillRegistry(self.root, None, None, lambda: self.fail("不能连接设备"))
        for arguments in ({"screen_id": "s"}, {"screen_id": "s", "node_id": "n", "x": 1, "y": 2},
                          {"screen_id": "s", "x": 1}):
            with self.assertRaises(ValueError):
                registry.invoke("app.tap", arguments)

    def test_foreground_detection_fails_closed(self):
        client = AdbClient(Path("adb"))
        with patch.object(client, "shell", return_value="mCurrentFocus=Window{abc u0 com.twt.service/.MainActivity}"):
            self.assertEqual(client.foreground_package(), "com.twt.service")
        with patch.object(client, "shell", return_value="mCurrentFocus=null"):
            self.assertIsNone(client.foreground_package())
