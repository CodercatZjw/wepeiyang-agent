from __future__ import annotations

import hashlib
import io
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from PIL import Image

from .adb import AdbClient, AdbError
from .parser import ForumPost, UiNode, find_node, parse_nodes, parse_posts, screen_bounds


PACKAGE = "com.twt.service"
COMPONENT = "com.twt.service/.ICONBlue"
SECTIONS = ("精华", "湖底", "校务", "学习", "Bugs", "交往", "美食", "社团")
FORUM_MARKERS = ("默认排序", "最新发帖")
RELATIVE_TIME_RE = re.compile(r"(?:刚刚|\d+\s*(?:秒|分钟|小时|天)前|\d{2,4}/\d{1,2}/\d{1,2})")


@dataclass(slots=True)
class ForumComment:
    author: str
    level: str | None
    body: str
    likes: int | None
    published_at: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class DetailResult:
    images: list[str]
    comments: list[dict]
    pages_scanned: int


def _is_forum(xml_bytes: bytes) -> bool:
    nodes = parse_nodes(xml_bytes)
    descriptions = {node.description.strip() for node in nodes}
    if all(marker in descriptions for marker in FORUM_MARKERS):
        return True
    tab_count = sum(
        1
        for node in nodes
        if "Tab " in node.description
        and node.description.splitlines()[:1]
        and node.description.splitlines()[0] in SECTIONS
    )
    has_post = any("#MP" in node.description for node in nodes)
    return tab_count >= 2 and has_post


def _portrait(xml_bytes: bytes) -> bool:
    width, height = screen_bounds(xml_bytes)
    return height > width


def _bottom_nav(nodes: list[UiNode], width: int, height: int) -> list[UiNode]:
    candidates = [
        node
        for node in nodes
        if node.clickable
        and "ImageView" in node.class_name
        and node.bounds[1] >= round(height * 0.88)
        and node.bounds[2] - node.bounds[0] >= 40
    ]
    candidates.sort(key=lambda node: node.center[0])
    return candidates


def parse_comments(xml_bytes: bytes) -> list[ForumComment]:
    nodes = parse_nodes(xml_bytes)
    filter_node = next((node for node in nodes if "只看楼主" in node.description), None)
    if filter_node is None:
        return []
    comment_top = filter_node.bounds[3]
    level_nodes = [
        node
        for node in nodes
        if node.bounds[1] >= comment_top
        and re.fullmatch(r"LV\d+", node.description.strip(), flags=re.IGNORECASE)
    ]
    level_nodes.sort(key=lambda node: node.bounds[1])
    entries: list[tuple[UiNode, UiNode | None]] = []
    for level_node in level_nodes:
        author_candidates = [
            node
            for node in nodes
            if node.bounds[1] >= comment_top
            if node.bounds[0] < level_node.bounds[0]
            and abs(node.center[1] - level_node.center[1]) <= 35
            and node.description
            and not node.description.upper().startswith("LV")
        ]
        author_node = min(
            author_candidates,
            key=lambda node: abs(node.center[1] - level_node.center[1]),
            default=None,
        )
        entries.append((level_node, author_node))

    comments: list[ForumComment] = []
    for index, (level_node, author_node) in enumerate(entries):
        top = min(level_node.bounds[1], author_node.bounds[1] if author_node else level_node.bounds[1])
        if index + 1 < len(entries):
            next_level, next_author = entries[index + 1]
            bottom = min(
                next_level.bounds[1],
                next_author.bounds[1] if next_author else next_level.bounds[1],
            )
        else:
            bottom = 1480
        peers = [node for node in nodes if top <= node.bounds[1] < bottom and node.description]
        author = author_node.description.strip() if author_node else ""
        likes: int | None = None
        published_at: str | None = None
        body_candidates: list[UiNode] = []
        for node in peers:
            value = node.description.strip()
            if not value or value == author or value.upper().startswith("LV"):
                continue
            if re.fullmatch(r"\d+", value):
                likes = int(value)
                continue
            if RELATIVE_TIME_RE.fullmatch(value):
                published_at = value
                continue
            if value in {"查看回复详情 >", "默认", "时间 ↓", "时间 ↑"}:
                continue
            if node.bounds[1] >= level_node.bounds[3] and node.bounds[0] >= 60:
                body_candidates.append(node)
        body_candidates.sort(key=lambda node: (node.bounds[1], node.bounds[0]))
        body = "\n".join(node.description.strip() for node in body_candidates)
        if author and body:
            comments.append(
                ForumComment(
                    author=author,
                    level=level_node.description.strip(),
                    body=body,
                    likes=likes,
                    published_at=published_at,
                )
            )
    return comments


class ForumClient:
    def __init__(self, adb: AdbClient, settle_seconds: float = 1.8):
        self.adb = adb
        self.settle_seconds = max(0.4, settle_seconds)

    def _sleep(self, multiplier: float = 1.0) -> None:
        time.sleep(self.settle_seconds * multiplier)

    def _wait_hierarchy(self, predicate, timeout: float = 25) -> bytes:
        deadline = time.monotonic() + timeout
        last: bytes | None = None
        while time.monotonic() < deadline:
            try:
                last = self.adb.hierarchy()
                if predicate(last):
                    return last
            except (AdbError, ValueError):
                pass
            time.sleep(0.8)
        if last is not None:
            return last
        raise AdbError("等待天外天页面结构超时。")

    def open_forum(self, section: str = "湖底", latest: bool = True) -> bytes:
        if section not in SECTIONS:
            raise AdbError(f"未知分区：{section}。可选：{'、'.join(SECTIONS)}")
        if not self.adb.package_installed(PACKAGE):
            raise AdbError("模拟器里没有检测到天外天/微北洋（包名 com.twt.service）。")
        self.adb.start_app(COMPONENT)

        xml_bytes = self._wait_hierarchy(lambda xml: _portrait(xml), timeout=30)
        if not _is_forum(xml_bytes):
            width, height = screen_bounds(xml_bytes)
            nav = _bottom_nav(parse_nodes(xml_bytes), width, height)
            if len(nav) < 2:
                raise AdbError("天外天首页尚未就绪，无法识别底部导航。")
            self.adb.tap(*nav[1].center)
            xml_bytes = self._wait_hierarchy(_is_forum, timeout=15)
            if not _is_forum(xml_bytes):
                # 蓝叠冷启动后偶尔吞掉第一次点击；稳定后重试一次。
                self.adb.tap(*nav[1].center)
                xml_bytes = self._wait_hierarchy(_is_forum, timeout=15)

        if not _is_forum(xml_bytes):
            page_text = "\n".join(node.description for node in parse_nodes(xml_bytes) if node.description)
            if any(word in page_text for word in ("登录", "统一身份认证", "验证码")):
                raise AdbError("天外天停在登录页。请先在模拟器中手动完成登录。")
            raise AdbError("没有进入微北洋论坛。")

        xml_bytes = self.switch_section(section, xml_bytes)
        if latest:
            node = find_node(parse_nodes(xml_bytes), "最新发帖")
            if node and node.clickable:
                self.adb.tap(*node.center)
                self._sleep()
                xml_bytes = self.adb.hierarchy()
        return xml_bytes

    def switch_section(self, section: str, xml_bytes: bytes | None = None) -> bytes:
        if section not in SECTIONS:
            raise AdbError(f"未知分区：{section}")
        xml_bytes = xml_bytes or self.adb.hierarchy()
        target_index = SECTIONS.index(section)
        for _ in range(5):
            nodes = parse_nodes(xml_bytes)
            target = next(
                (
                    node
                    for node in nodes
                    if node.description.splitlines()[:1] == [section] and "Tab " in node.description
                ),
                None,
            )
            width, height = screen_bounds(xml_bytes)
            if target and target.bounds[2] > target.bounds[0] and target.center[0] < width:
                if target.selected:
                    return xml_bytes
                self.adb.tap(*target.center)
                self._sleep()
                return self.adb.hierarchy()
            y = round(height * 0.13)
            if target_index >= 6:
                self.adb.swipe(round(width * 0.85), y, round(width * 0.25), y, 450)
            else:
                self.adb.swipe(round(width * 0.20), y, round(width * 0.80), y, 450)
            self._sleep(0.7)
            xml_bytes = self.adb.hierarchy()
        raise AdbError(f"无法在论坛导航中找到“{section}”分区。")

    def open_post(self, post: ForumPost) -> bytes:
        x1, y1, x2, y2 = post.card_bounds
        if x2 <= x1 or y2 <= y1:
            raise AdbError(f"帖子 {post.post_id} 没有可点击区域。")
        x = (x1 + x2) // 2
        y = min(y2 - 25, y1 + 90)
        self.adb.tap(x, y)
        return self._wait_hierarchy(
            lambda xml: any(
                node.description.strip() == f"#{post.post_id}" for node in parse_nodes(xml)
            ),
            timeout=15,
        )

    def back_to_forum(self) -> bytes:
        self.adb.shell("input", "keyevent", "4")
        xml_bytes = self._wait_hierarchy(_is_forum, timeout=15)
        if not _is_forum(xml_bytes):
            raise AdbError("从帖子详情返回论坛失败。")
        return xml_bytes

    @staticmethod
    def _detail_image_bounds(xml_bytes: bytes) -> list[tuple[int, int, int, int]]:
        width, height = screen_bounds(xml_bytes)
        results: list[tuple[int, int, int, int]] = []
        for node in parse_nodes(xml_bytes):
            x1, y1, x2, y2 = node.bounds
            if (
                "ImageView" in node.class_name
                and x2 - x1 >= 180
                and y2 - y1 >= 120
                and y1 >= 240
                and y1 < height - 100
            ):
                results.append((max(0, x1), max(0, y1), min(width, x2), min(height, y2)))
        return results

    @staticmethod
    def _crop_image(png: bytes, bounds: tuple[int, int, int, int]) -> tuple[bytes, str]:
        with Image.open(io.BytesIO(png)) as source:
            cropped = source.crop(bounds).convert("RGB")
            digest_image = cropped.copy()
            digest_image.thumbnail((64, 64))
            digest = hashlib.sha256(digest_image.tobytes()).hexdigest()
            output = io.BytesIO()
            cropped.save(output, format="PNG", optimize=True)
            return output.getvalue(), digest

    def collect_detail(
        self,
        post: ForumPost,
        output_dir: Path,
        include_images: bool = False,
        include_comments: bool = False,
        max_pages: int = 6,
    ) -> DetailResult:
        xml_bytes = self.open_post(post)
        media_dir = output_dir / "media" / post.post_id
        image_paths: list[str] = []
        comments: list[dict] = []
        image_hashes: set[str] = set()
        comment_keys: set[tuple[str, str]] = set()
        stale_pages = 0
        pages_scanned = 0

        for page in range(1, max_pages + 1):
            pages_scanned = page
            before = len(image_hashes) + len(comment_keys)
            screenshot = self.adb.screenshot() if include_images else b""
            if include_images:
                for bounds in self._detail_image_bounds(xml_bytes):
                    cropped, digest = self._crop_image(screenshot, bounds)
                    if digest in image_hashes:
                        continue
                    image_hashes.add(digest)
                    media_dir.mkdir(parents=True, exist_ok=True)
                    path = media_dir / f"image-{len(image_paths) + 1:03d}.png"
                    path.write_bytes(cropped)
                    image_paths.append(str(path.resolve()))

            if include_comments:
                for comment in parse_comments(xml_bytes):
                    key = (comment.author, comment.body)
                    if key in comment_keys:
                        continue
                    comment_keys.add(key)
                    comments.append(comment.to_dict())

            if not include_images and not include_comments:
                break
            if len(image_hashes) + len(comment_keys) == before:
                stale_pages += 1
            else:
                stale_pages = 0
            if stale_pages >= 2 or page == max_pages:
                break
            width, height = screen_bounds(xml_bytes)
            self.adb.swipe(width // 2, round(height * 0.82), width // 2, round(height * 0.28), 650)
            self._sleep()
            xml_bytes = self.adb.hierarchy()

        self.back_to_forum()
        return DetailResult(images=image_paths, comments=comments, pages_scanned=pages_scanned)
