import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from wepeiyang_agent.adb import AdbClient, AdbError
from wepeiyang_agent.forum import ForumClient, _is_search_results
from wepeiyang_agent.query import QueryEngine, QuerySpec


def xml(children):
    return ('<?xml version="1.0"?><hierarchy><node bounds="[0,0][900,1600]">' + children + '</node></hierarchy>').encode()


FEED = xml('<node class="android.widget.Button" clickable="true" content-desc="为你推荐" bounds="[0,0][810,80]" />'
           '<node class="android.widget.Button" clickable="true" bounds="[810,9][857,71]" />')
EDITOR = xml('<node class="android.widget.EditText" clickable="true" text="" bounds="[57,45][837,90]" />')
TYPED = xml('<node class="android.widget.EditText" text="国创赛" bounds="[57,45][837,90]" />'
            '<node class="android.widget.Button" clickable="true" bounds="[846,45][882,90]" />')
EMPTY = xml('<node content-desc="未检索到相关问题" bounds="[0,120][900,1600]" />')


class NativeSearchTests(unittest.TestCase):
    def test_uses_search_bar_not_notification_button(self):
        adb = MagicMock()
        adb.hierarchy.return_value = TYPED
        forum = ForumClient(adb)
        with patch.object(forum, 'open_forum', return_value=FEED), patch.object(forum, '_wait_hierarchy', side_effect=[EDITOR, EMPTY]):
            self.assertEqual(forum.search('国创赛'), EMPTY)
        self.assertEqual(adb.tap.call_args_list[0].args, (405, 40))
        adb.input_unicode.assert_called_once_with('国创赛')
        adb.shell.assert_called_once_with('input', 'keyevent', '66')

    def test_mismatched_input_is_never_submitted(self):
        adb = MagicMock()
        adb.hierarchy.return_value = EDITOR
        forum = ForumClient(adb)
        with patch.object(forum, 'open_forum', return_value=FEED), patch.object(forum, '_wait_hierarchy', return_value=EDITOR):
            with self.assertRaises(AdbError):
                forum.search('国创赛')
        self.assertEqual(adb.tap.call_count, 2)

    def test_all_keywords_use_native_search_even_with_no_results(self):
        forum = MagicMock()
        forum.search.return_value = EMPTY
        forum.open_forum.side_effect = AssertionError('不允许退回刷帖')
        with tempfile.TemporaryDirectory() as d:
            result = QueryEngine(forum, Path(d)).search(QuerySpec(query='国创赛|大创', section='全部'), source='live', screenshots=False)
        self.assertEqual([call.args[0] for call in forum.search.call_args_list], ['国创赛', '大创'])
        self.assertEqual(result.source, 'native_search')

    def test_input_method_restored_after_failed_input(self):
        client = AdbClient(Path('fake-adb'))
        calls = []
        def shell(*args, **kwargs):
            calls.append(args)
            if args[:3] == ('settings', 'get', 'secure'):
                return 'old/.IME'
            return 'result=0'
        with patch.object(client, 'package_installed', return_value=True), patch.object(client, 'shell', side_effect=shell), patch('time.sleep'):
            with self.assertRaises(AdbError):
                client.input_unicode('国创赛')
        self.assertIn(('ime', 'set', 'old/.IME'), calls)
        self.assertIn(('ime', 'disable', 'org.wepeiyang.inputbridge/.InputBridge'), calls)

    def test_result_page_can_keep_search_edittext(self):
        results = xml('<node class="android.widget.EditText" text="国创赛" />'
            '<node content-desc="甲&#10;LV1&#10;2026/09/07&#10;#MP123&#10;国创赛组队&#10;详情&#10;0&#10;1&#10;3次浏览" />')
        self.assertTrue(_is_search_results(results))
