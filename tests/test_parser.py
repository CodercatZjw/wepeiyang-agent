from __future__ import annotations

import unittest

from wepeiyang_agent.parser import parse_posts


class ParserTests(unittest.TestCase):
    def test_parses_post_card(self) -> None:
        description = (
            "alice\nLV12\n2026-09-05 18:14:33\n#MP453317\n"
            "测试标题\n第一行\n第二行\n21\n23\n1021次浏览"
        )
        escaped_description = description.replace("\n", "&#10;")
        xml = (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<hierarchy><node bounds='[0,0][900,1600]' content-desc=''>"
            f"<node bounds='[0,0][900,900]' content-desc='{escaped_description}'/>"
            "</node></hierarchy>"
        ).encode()
        posts = parse_posts(xml)
        self.assertEqual(len(posts), 1)
        post = posts[0]
        self.assertEqual(post.post_id, "MP453317")
        self.assertEqual(post.title, "测试标题")
        self.assertEqual(post.body, "第一行\n第二行")
        self.assertEqual(post.likes, 23)
        self.assertEqual(post.replies, 21)
        self.assertEqual(post.views, 1021)

    def test_detects_large_post_image(self) -> None:
        description = (
            "alice&#10;LV12&#10;2026-09-05 18:14:33&#10;#MP453318&#10;"
            "带图帖子&#10;正文&#10;2&#10;12&#10;100次浏览"
        )
        xml = (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<hierarchy><node bounds='[0,0][900,1600]' content-desc=''>"
            "<node selected='true' bounds='[0,0][100,80]' content-desc='学习&#10;Tab 4 of 9'/>"
            f"<node bounds='[0,300][900,900]' content-desc='{description}'>"
            "<node class='android.widget.ImageView' bounds='[20,500][880,800]' content-desc=''/>"
            "</node></node></hierarchy>"
        ).encode()
        post = parse_posts(xml)[0]
        self.assertEqual(post.section, "学习")
        self.assertEqual(post.likes, 12)
        self.assertEqual(post.replies, 2)
        self.assertEqual(post.image_bounds, ((20, 500, 880, 800),))


if __name__ == "__main__":
    unittest.main()
