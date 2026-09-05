from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .forum import ForumClient, SECTIONS
from .parser import ForumPost, parse_post_description, parse_posts, screen_bounds


@dataclass(frozen=True, slots=True)
class QuerySpec:
    query: str | None = None
    section: str | None = "湖底"
    min_likes: int | None = None
    count: int = 3
    since: datetime | None = None
    exclude_pinned: bool = False
    only_images: bool = False


@dataclass(slots=True)
class QueryResult:
    posts: list[dict]
    source: str
    pages_scanned: int
    run_dir: str | None
    stopped_reason: str

    def to_dict(self) -> dict:
        return {
            "posts": self.posts,
            "source": self.source,
            "pages_scanned": self.pages_scanned,
            "run_dir": self.run_dir,
            "stopped_reason": self.stopped_reason,
        }


def parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    lowered = value.strip().lower()
    if lowered.endswith("d") and lowered[:-1].isdigit():
        return datetime.now() - timedelta(days=int(lowered[:-1]))
    if lowered.endswith("h") and lowered[:-1].isdigit():
        return datetime.now() - timedelta(hours=int(lowered[:-1]))
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("--since 使用 YYYY-MM-DD、YYYY-MM-DDTHH:MM:SS、7d 或 12h。") from exc


def _published_at(post: dict) -> datetime | None:
    value = post.get("published_at")
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("/", "-"))
    except ValueError:
        return None


def matches(post: dict, spec: QuerySpec) -> bool:
    if spec.section and spec.section != "全部" and post.get("section") != spec.section:
        return False
    if spec.exclude_pinned and post.get("is_pinned"):
        return False
    if spec.min_likes is not None:
        likes = post.get("likes")
        if not isinstance(likes, int) or likes < spec.min_likes:
            return False
    if spec.only_images and not (post.get("image_bounds") or post.get("images")):
        return False
    if spec.since:
        published = _published_at(post)
        if published is None or published < spec.since:
            return False
    if spec.query:
        needles = [part.strip().casefold() for part in spec.query.split("|") if part.strip()]
        haystack = "\n".join(
            str(post.get(field, "")) for field in ("title", "body", "raw_description")
        ).casefold()
        if needles and not any(needle in haystack for needle in needles):
            return False
    return True


class LocalIndex:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.path = data_dir / "index.json"

    @staticmethod
    def _normalize(row: dict) -> dict:
        raw = row.get("raw_description")
        if isinstance(raw, str) and raw:
            parsed = parse_post_description(raw)
            if parsed:
                row = {**row, "likes": parsed.likes, "replies": parsed.replies, "views": parsed.views}
        row.setdefault("images", [])
        row.setdefault("comments", [])
        row.setdefault("image_bounds", [])
        return row

    def load(self) -> dict[str, dict]:
        items: dict[str, dict] = {}
        if self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    items.update(
                        (key, self._normalize(value))
                        for key, value in payload.items()
                        if isinstance(value, dict)
                    )
            except (OSError, json.JSONDecodeError):
                pass
        legacy = self.data_dir / "posts.jsonl"
        if legacy.exists():
            for line in legacy.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                post_id = row.get("post_id")
                if isinstance(post_id, str):
                    items.setdefault(post_id, self._normalize(row))
        return items

    def upsert(self, rows: list[dict]) -> None:
        items = self.load()
        for incoming in rows:
            post_id = incoming.get("post_id")
            if not isinstance(post_id, str):
                continue
            existing = items.get(post_id, {})
            merged = {**existing, **{key: value for key, value in incoming.items() if value not in (None, "", [], ())}}
            items[post_id] = self._normalize(merged)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def search(self, spec: QuerySpec, exclude: set[str] | None = None) -> list[dict]:
        exclude = exclude or set()
        rows = [
            row
            for post_id, row in self.load().items()
            if post_id not in exclude and matches(row, spec)
        ]
        rows.sort(key=lambda row: row.get("published_at") or "", reverse=True)
        return rows[: spec.count]


class QueryEngine:
    def __init__(self, forum: ForumClient, data_dir: Path):
        self.forum = forum
        self.data_dir = data_dir
        self.index = LocalIndex(data_dir)

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _live_one_section(
        self,
        spec: QuerySpec,
        section: str,
        output_dir: Path,
        max_pages: int,
        max_seconds: int,
        include_images: bool,
        include_comments: bool,
        screenshots: bool,
        exclude: set[str],
    ) -> tuple[list[dict], list[dict], int, str]:
        xml_bytes = self.forum.open_forum(section=section, latest=True)
        matched: list[dict] = []
        all_seen: dict[str, dict] = {}
        pages_scanned = 0
        deadline = time.monotonic() + max_seconds
        stopped_reason = "max_pages"
        screen_dir = output_dir / "screens" / section
        if screenshots:
            screen_dir.mkdir(parents=True, exist_ok=True)

        for page in range(1, max_pages + 1):
            pages_scanned = page
            if time.monotonic() >= deadline:
                stopped_reason = "max_seconds"
                break
            if screenshots:
                (screen_dir / f"page-{page:03d}.png").write_bytes(self.forum.adb.screenshot())
            page_posts = parse_posts(xml_bytes)
            for post in page_posts:
                row = post.to_dict()
                row["captured_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                row["source_page"] = page
                all_seen[post.post_id] = row
                if post.post_id in exclude or any(item["post_id"] == post.post_id for item in matched):
                    continue
                if not matches(row, spec):
                    continue

                if include_images or include_comments:
                    current_posts = {item.post_id: item for item in parse_posts(xml_bytes)}
                    current = current_posts.get(post.post_id)
                    if current:
                        detail = self.forum.collect_detail(
                            current,
                            output_dir,
                            include_images=include_images and bool(current.image_bounds),
                            include_comments=include_comments,
                        )
                        row["images"] = detail.images
                        row["comments"] = detail.comments
                        all_seen[post.post_id] = row
                        xml_bytes = self.forum.adb.hierarchy()
                matched.append(row)
                if len(matched) >= spec.count:
                    stopped_reason = "target_count"
                    return matched, list(all_seen.values()), pages_scanned, stopped_reason

            if spec.since and page_posts:
                dated = [_published_at(post.to_dict()) for post in page_posts if not post.is_pinned]
                dated = [value for value in dated if value is not None]
                if dated and max(dated) < spec.since:
                    stopped_reason = "since_boundary"
                    break
            if page == max_pages:
                break
            width, height = screen_bounds(xml_bytes)
            self.forum.adb.swipe(width // 2, round(height * 0.82), width // 2, round(height * 0.28), 650)
            self.forum._sleep()
            xml_bytes = self.forum.adb.hierarchy()
        return matched, list(all_seen.values()), pages_scanned, stopped_reason

    def live(
        self,
        spec: QuerySpec,
        max_pages: int = 20,
        max_seconds: int = 300,
        include_images: bool = False,
        include_comments: bool = False,
        screenshots: bool = True,
        exclude: set[str] | None = None,
    ) -> QueryResult:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = self.data_dir / "queries" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        exclude = set(exclude or set())
        matched: list[dict] = []
        seen_rows: dict[str, dict] = {}
        pages_total = 0
        stopped_reason = "max_pages"
        sections = (
            [spec.section]
            if spec.section and spec.section != "全部"
            else [name for name in SECTIONS if name != "精华"]
        )
        for section in sections:
            remaining = spec.count - len(matched)
            if remaining <= 0:
                stopped_reason = "target_count"
                break
            section_spec = QuerySpec(
                query=spec.query,
                section=section,
                min_likes=spec.min_likes,
                count=remaining,
                since=spec.since,
                exclude_pinned=spec.exclude_pinned,
                only_images=spec.only_images,
            )
            found, observed, pages, reason = self._live_one_section(
                section_spec,
                section,
                run_dir,
                max_pages=max_pages,
                max_seconds=max_seconds,
                include_images=include_images,
                include_comments=include_comments,
                screenshots=screenshots,
                exclude=exclude | {row["post_id"] for row in matched},
            )
            pages_total += pages
            matched.extend(found)
            seen_rows.update((row["post_id"], row) for row in observed)
            stopped_reason = reason
            if len(matched) >= spec.count:
                stopped_reason = "target_count"
                break

        self.index.upsert(list(seen_rows.values()))
        result = QueryResult(
            posts=matched,
            source="live",
            pages_scanned=pages_total,
            run_dir=str(run_dir.resolve()),
            stopped_reason=stopped_reason,
        )
        self._write_json(run_dir / "result.json", result.to_dict())
        return result

    def search(
        self,
        spec: QuerySpec,
        source: str = "hybrid",
        **live_options,
    ) -> QueryResult:
        if source not in {"local", "live", "hybrid"}:
            raise ValueError("source 必须是 local、live 或 hybrid。")
        local_posts: list[dict] = []
        if source in {"local", "hybrid"}:
            local_posts = self.index.search(spec)
            if source == "local" or len(local_posts) >= spec.count:
                return QueryResult(
                    posts=local_posts,
                    source="local",
                    pages_scanned=0,
                    run_dir=None,
                    stopped_reason="target_count" if len(local_posts) >= spec.count else "local_exhausted",
                )
        remaining_spec = QuerySpec(
            query=spec.query,
            section=spec.section,
            min_likes=spec.min_likes,
            count=spec.count - len(local_posts),
            since=spec.since,
            exclude_pinned=spec.exclude_pinned,
            only_images=spec.only_images,
        )
        live_result = self.live(
            remaining_spec,
            exclude={row["post_id"] for row in local_posts},
            **live_options,
        )
        combined = [*local_posts, *live_result.posts]
        combined.sort(key=lambda row: row.get("published_at") or "", reverse=True)
        return QueryResult(
            posts=combined[: spec.count],
            source="hybrid" if source == "hybrid" else "live",
            pages_scanned=live_result.pages_scanned,
            run_dir=live_result.run_dir,
            stopped_reason=(
                "target_count" if len(combined) >= spec.count else live_result.stopped_reason
            ),
        )
