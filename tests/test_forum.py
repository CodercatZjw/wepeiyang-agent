from __future__ import annotations

import unittest

from wepeiyang_agent.forum import parse_comments


class DetailParserTests(unittest.TestCase):
    def test_parses_comment(self) -> None:
        xml = b"""<?xml version='1.0' encoding='UTF-8'?>
        <hierarchy><node bounds='[0,0][900,1600]' content-desc=''>
          <node class='android.widget.Button' bounds='[700,430][880,493]' content-desc='  &#21482;&#30475;&#27004;&#20027;  '/>
          <node class='android.view.View' bounds='[78,525][227,577]' content-desc='alice'/>
          <node class='android.view.View' bounds='[248,540][291,562]' content-desc='LV46'/>
          <node class='android.view.View' bounds='[78,583][882,649]' content-desc='&#36825;&#26159;&#19968;&#26465;&#35780;&#35770;'/>
          <node class='android.view.View' bounds='[116,667][154,706]' content-desc='8'/>
          <node class='android.view.View' bounds='[779,664][882,706]' content-desc='4&#20998;&#38047;&#21069;'/>
        </node></hierarchy>"""
        comments = parse_comments(xml)
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0].author, "alice")
        self.assertEqual(comments[0].body, "这是一条评论")
        self.assertEqual(comments[0].likes, 8)
        self.assertEqual(comments[0].published_at, "4分钟前")
