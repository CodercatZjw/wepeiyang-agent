from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict


BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
POST_ID_RE = re.compile(r"^#MP(\d+)$", re.IGNORECASE)
VIEW_RE = re.compile(r"^(\d+)\s*次浏览$")
NUMBER_RE = re.compile(r"^\d+$")


@dataclass(slots=True)
class UiNode:
    description: str
    bounds: tuple[int, int, int, int]
    class_name: str
    clickable: bool
    scrollable: bool
    selected: bool = False

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return ((x1 + x2) // 2, (y1 + y2) // 2)


@dataclass(slots=True)
class ForumPost:
    post_id: str
    author: str
    level: str | None
    published_at: str | None
    title: str
    body: str
    is_pinned: bool
    likes: int | None
    replies: int | None
    views: int | None
    raw_description: str
    section: str | None = None
    card_bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    image_bounds: tuple[tuple[int, int, int, int], ...] = ()
    images: tuple[str, ...] = ()
    comments: tuple[dict, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


def parse_bounds(value: str) -> tuple[int, int, int, int]:
    match = BOUNDS_RE.fullmatch(value or "")
    if not match:
        return (0, 0, 0, 0)
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def parse_nodes(xml_bytes: bytes) -> list[UiNode]:
    root = ET.fromstring(xml_bytes)
    nodes: list[UiNode] = []
    for element in root.iter("node"):
        nodes.append(
            UiNode(
                description=element.attrib.get("content-desc", ""),
                bounds=parse_bounds(element.attrib.get("bounds", "")),
                class_name=element.attrib.get("class", ""),
                clickable=element.attrib.get("clickable") == "true",
                scrollable=element.attrib.get("scrollable") == "true",
                selected=element.attrib.get("selected") == "true",
            )
        )
    return nodes


def screen_bounds(xml_bytes: bytes) -> tuple[int, int]:
    root = ET.fromstring(xml_bytes)
    first = next(root.iter("node"))
    _, _, width, height = parse_bounds(first.attrib.get("bounds", ""))
    if width <= 0 or height <= 0:
        raise ValueError("页面结构中没有有效的屏幕尺寸")
    return width, height


def find_node(nodes: list[UiNode], description: str) -> UiNode | None:
    exact = [node for node in nodes if node.description.strip() == description]
    if exact:
        return exact[0]
    partial = [node for node in nodes if description in node.description]
    return partial[0] if partial else None


def parse_post_description(description: str) -> ForumPost | None:
    lines = [line.strip() for line in description.splitlines() if line.strip()]
    id_index = next((i for i, line in enumerate(lines) if POST_ID_RE.match(line)), None)
    if id_index is None:
        return None
    id_match = POST_ID_RE.match(lines[id_index])
    assert id_match is not None

    cursor = id_index + 1
    is_pinned = cursor < len(lines) and lines[cursor] == "置顶"
    if is_pinned:
        cursor += 1

    views: int | None = None
    likes: int | None = None
    replies: int | None = None
    content_end = len(lines)
    view_index = next(
        (i for i in range(len(lines) - 1, cursor - 1, -1) if VIEW_RE.match(lines[i])),
        None,
    )
    if view_index is not None:
        view_match = VIEW_RE.match(lines[view_index])
        assert view_match is not None
        views = int(view_match.group(1))
        metric_numbers: list[int] = []
        scan = view_index - 1
        while scan >= cursor and len(metric_numbers) < 2 and NUMBER_RE.match(lines[scan]):
            metric_numbers.append(int(lines[scan]))
            scan -= 1
        if metric_numbers:
            likes = metric_numbers[0]
        if len(metric_numbers) > 1:
            replies = metric_numbers[1]
        content_end = scan + 1

    content = lines[cursor:content_end]
    title = content[0] if content else ""
    body = "\n".join(content[1:]) if len(content) > 1 else ""
    author = lines[0] if lines else ""
    level = lines[1] if len(lines) > 1 and lines[1].upper().startswith("LV") else None
    published_at = lines[2] if len(lines) > 2 else None
    return ForumPost(
        post_id=f"MP{id_match.group(1)}",
        author=author,
        level=level,
        published_at=published_at,
        title=title,
        body=body,
        is_pinned=is_pinned,
        likes=likes,
        replies=replies,
        views=views,
        raw_description=description,
    )


def parse_posts(xml_bytes: bytes) -> list[ForumPost]:
    root = ET.fromstring(xml_bytes)
    section = selected_section(xml_bytes)
    posts: list[ForumPost] = []
    seen: set[str] = set()
    for element in root.iter("node"):
        description = element.attrib.get("content-desc", "")
        if "#MP" not in description:
            continue
        post = parse_post_description(description)
        if post and post.post_id not in seen:
            post.section = section
            post.card_bounds = parse_bounds(element.attrib.get("bounds", ""))
            image_bounds: list[tuple[int, int, int, int]] = []
            for child in element.iter("node"):
                if "ImageView" not in child.attrib.get("class", ""):
                    continue
                bounds = parse_bounds(child.attrib.get("bounds", ""))
                x1, y1, x2, y2 = bounds
                if x2 - x1 >= 180 and y2 - y1 >= 100:
                    image_bounds.append(bounds)
            post.image_bounds = tuple(image_bounds)
            seen.add(post.post_id)
            posts.append(post)
    return posts


def selected_section(xml_bytes: bytes) -> str | None:
    section_names = {"精华", "湖底", "校务", "学习", "Bugs", "交往", "美食", "社团"}
    fallback: str | None = None
    for node in parse_nodes(xml_bytes):
        if "Tab " not in node.description:
            continue
        name = node.description.splitlines()[0].strip()
        if name not in section_names:
            continue
        if node.selected:
            return name
        if fallback is None and node.description.startswith("湖底\n"):
            fallback = "湖底"
    return fallback
