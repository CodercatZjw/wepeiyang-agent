from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .adb import AdbClient, AdbError
from .llm import LlmController
from .forum import ForumClient
from .parser import parse_posts, screen_bounds


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
    def __init__(
        self,
        adb: AdbClient,
        data_dir: Path,
        settle_seconds: float = 1.8,
        llm: LlmController | None = None,
        goal: str = "浏览微北洋最新帖子",
        send_body_chars: int = 240,
    ):
        self.adb = adb
        self.data_dir = data_dir
        self.settle_seconds = settle_seconds
        self.llm = llm
        self.goal = goal
        self.send_body_chars = send_body_chars

    def _sleep(self, multiplier: float = 1.0) -> None:
        time.sleep(max(0.2, self.settle_seconds * multiplier))

    def _is_forum(self, xml_bytes: bytes) -> bool:
        descriptions = {node.description.strip() for node in parse_nodes(xml_bytes)}
        return all(marker in descriptions for marker in FORUM_MARKERS)

    def open_forum(self, latest: bool = True) -> bytes:
        return ForumClient(self.adb, self.settle_seconds).open_forum(
            section="湖底", latest=latest
        )

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
        decisions: list[dict] = []

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
                decisions.append(
                    {
                        "page": page_number,
                        "action": "stop",
                        "reason": "安全停止条件：连续页面没有发现新帖子",
                        "source": "guardrail",
                    }
                )
                break
            if page_number == pages:
                decisions.append(
                    {
                        "page": page_number,
                        "action": "stop",
                        "reason": "安全停止条件：已达到最大页数",
                        "source": "guardrail",
                    }
                )
                break

            if self.llm is not None:
                page_rows = [run_posts[post.post_id] for post in page_posts if post.post_id in run_posts]
                observation = {
                    "goal": self.goal,
                    "page": page_number,
                    "max_pages": pages,
                    "unique_posts_seen": len(run_posts),
                    "new_unique_posts_on_page": len(run_posts) - before,
                    "consecutive_stale_pages": stale_pages,
                    "allowed_actions": ["scroll", "stop"],
                    "posts": [
                        {
                            "post_id": row["post_id"],
                            "published_at": row["published_at"],
                            "title": row["title"],
                            "body_preview": row["body"][: self.send_body_chars],
                        }
                        for row in page_rows
                    ],
                }
                decision = self.llm.decide(observation)
                decisions.append(
                    {
                        "page": page_number,
                        "action": decision.action,
                        "reason": decision.reason,
                        "source": "llm",
                    }
                )
                print(f"LLM 决策：{decision.action}｜{decision.reason}")
                if decision.action == "stop":
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
        self._write_jsonl(run_dir / "decisions.jsonl", decisions)
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
            "controller": "llm" if self.llm is not None else "deterministic",
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
