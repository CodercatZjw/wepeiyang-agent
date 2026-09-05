from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .adb import AdbClient, AdbError
from .parser import find_node, parse_nodes, parse_posts, screen_bounds


PACKAGE = "com.twt.service"
COMPONENT = "com.twt.service/.ICONBlue"
FORUM_MARKERS = ("默认排序", "最新发帖")


@dataclass(slots=True)
class BrowseResult:
    run_id: str
    pages_scanned: int
    posts_seen: int
    new_posts: int
    run_dir: Path


class BrowseAgent:
    def __init__(self, adb: AdbClient, data_dir: Path, settle_seconds: float = 1.8):
        self.adb = adb
        self.data_dir = data_dir
        self.settle_seconds = settle_seconds

    def _sleep(self, multiplier: float = 1.0) -> None:
        time.sleep(max(0.2, self.settle_seconds * multiplier))

    def _is_forum(self, xml_bytes: bytes) -> bool:
        descriptions = {node.description.strip() for node in parse_nodes(xml_bytes)}
        return all(marker in descriptions for marker in FORUM_MARKERS)

    def open_forum(self, latest: bool = True) -> bytes:
        if not self.adb.package_installed(PACKAGE):
            raise AdbError("模拟器里没有检测到天外天/微北洋（包名 com.twt.service）。")
        self.adb.start_app(COMPONENT)
        self._sleep(2.0)
        xml_bytes = self.adb.hierarchy()
        if not self._is_forum(xml_bytes):
            width, height = screen_bounds(xml_bytes)
            # 天外天底部四个入口中，第二个是微北洋论坛。
            self.adb.tap(round(width * 0.375), round(height * 0.958))
            self._sleep(2.0)
            xml_bytes = self.adb.hierarchy()

        if not self._is_forum(xml_bytes):
            page_text = "\n".join(node.description for node in parse_nodes(xml_bytes) if node.description)
            if any(word in page_text for word in ("登录", "统一身份认证", "验证码")):
                raise AdbError("天外天停在登录页。请先在模拟器中手动完成登录，再重新运行。")
            if "同意" in page_text and "用户" in page_text:
                raise AdbError("天外天停在首次使用协议页。请先在模拟器中确认协议，再重新运行。")
            raise AdbError("没有进入微北洋论坛，请确认应用版本和底部导航布局。")

        if latest:
            node = find_node(parse_nodes(xml_bytes), "最新发帖")
            if node and node.clickable:
                self.adb.tap(*node.center)
                self._sleep()
                xml_bytes = self.adb.hierarchy()
        return xml_bytes

    def _load_seen(self) -> set[str]:
        path = self.data_dir / "state.json"
        if not path.exists():
            return set()
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return set(value.get("seen_post_ids", []))
        except (OSError, ValueError, TypeError):
            return set()

    def _save_state(self, seen: set[str], run_id: str) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_run": run_id,
            "seen_post_ids": sorted(seen),
        }
        temp = self.data_dir / "state.json.tmp"
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.data_dir / "state.json")

    @staticmethod
    def _write_jsonl(path: Path, rows: list[dict], mode: str = "w") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open(mode, encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def browse(
        self,
        pages: int = 8,
        latest: bool = True,
        screenshots: bool = True,
        stop_after_stale_pages: int = 2,
    ) -> BrowseResult:
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = self.data_dir / "runs" / run_id
        screen_dir = run_dir / "screens"
        run_dir.mkdir(parents=True, exist_ok=True)
        if screenshots:
            screen_dir.mkdir(parents=True, exist_ok=True)

        global_seen = self._load_seen()
        run_posts: dict[str, dict] = {}
        stale_pages = 0
        xml_bytes = self.open_forum(latest=latest)
        pages_scanned = 0

        for page_number in range(1, pages + 1):
            pages_scanned = page_number
            captured_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            if screenshots:
                (screen_dir / f"page-{page_number:03d}.png").write_bytes(self.adb.screenshot())

            page_posts = parse_posts(xml_bytes)
            before = len(run_posts)
            for post in page_posts:
                row = post.to_dict()
                row["captured_at"] = captured_at
                row["source_page"] = page_number
                run_posts.setdefault(post.post_id, row)
            stale_pages = stale_pages + 1 if len(run_posts) == before else 0
            if stale_pages >= stop_after_stale_pages:
                break
            if page_number == pages:
                break

            width, height = screen_bounds(xml_bytes)
            self.adb.swipe(
                round(width * 0.50),
                round(height * 0.82),
                round(width * 0.50),
                round(height * 0.28),
            )
            self._sleep()
            xml_bytes = self.adb.hierarchy()

        all_rows = list(run_posts.values())
        new_rows = [row for row in all_rows if row["post_id"] not in global_seen]
        self._write_jsonl(run_dir / "posts.jsonl", all_rows)
        if new_rows:
            self._write_jsonl(self.data_dir / "posts.jsonl", new_rows, mode="a")
        global_seen.update(row["post_id"] for row in all_rows)
        self._save_state(global_seen, run_id)

        summary = {
            "run_id": run_id,
            "pages_scanned": pages_scanned,
            "posts_seen": len(all_rows),
            "new_posts": len(new_rows),
            "latest_sort": latest,
            "screenshots": screenshots,
        }
        (run_dir / "run.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return BrowseResult(
            run_id=run_id,
            pages_scanned=pages_scanned,
            posts_seen=len(all_rows),
            new_posts=len(new_rows),
            run_dir=run_dir,
        )

