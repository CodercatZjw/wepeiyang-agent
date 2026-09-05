from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .adb import AdbClient, AdbError, find_adb
from .agent import BrowseAgent
from .config import AppConfig
from .forum import ForumClient, SECTIONS
from .llm import LlmController, LlmError
from .query import LocalIndex, QueryEngine, QueryResult, QuerySpec, parse_since


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["find", "search", "browse", "sections", "help", "exit"],
        },
        "query": {"type": ["string", "null"]},
        "section": {"type": "string", "enum": [*SECTIONS, "全部"]},
        "min_likes": {"type": ["integer", "null"]},
        "count": {"type": "integer"},
        "since": {"type": ["string", "null"]},
        "exclude_pinned": {"type": "boolean"},
        "only_images": {"type": "boolean"},
        "include_images": {"type": "boolean"},
        "include_comments": {"type": "boolean"},
        "source": {"type": "string", "enum": ["local", "live", "hybrid"]},
        "max_pages": {"type": "integer"},
        "max_seconds": {"type": "integer"},
        "reason": {"type": "string"},
    },
    "required": [
        "action",
        "query",
        "section",
        "min_likes",
        "count",
        "since",
        "exclude_pinned",
        "only_images",
        "include_images",
        "include_comments",
        "source",
        "max_pages",
        "max_seconds",
        "reason",
    ],
    "additionalProperties": False,
}


PLAN_SYSTEM_PROMPT = """你是微北洋校园论坛只读 Agent 的指令规划器。
把用户自然语言转换为一个受限 JSON 计划，不要输出 Markdown。

允许动作：
- find：实时浏览，按分区、点赞、时间、图片等条件找帖子。
- search：关键词搜索，默认 source=hybrid（本地索引优先，不足时实时浏览）。
- browse：不设关键词，按 LLM 决策连续刷若干屏。
- sections：列出分区。
- help：说明用法。
- exit：退出程序。

规则：
- 严禁规划发帖、回复、点赞、点踩、收藏、登录、用户资料操作或任意系统命令；此类请求返回 help。
- “超过 N 赞”转换为 min_likes=N+1；“至少 N 赞”转换为 min_likes=N。
- 关键词的常见同义表达可以用 | 连接，例如“国创赛|国创|创新大赛|大创”。
- 用户说“随便找”时用 find，默认湖底、3 篇。
- 用户说“最新”表示按已有时间倒序，不要擅自添加 since；只有明确说最近几天/小时才填写 since。
- 用户要求带图帖时 only_images=true；要求保存或查看图片时同时 include_images=true。
- 用户要求评论、回复内容或完整上下文时 include_comments=true。
- 默认 count=3、max_pages=20、max_seconds=300。用户明确指定时采用其要求。
- browse 的 max_pages 表示刷帖屏数，范围仍需有限。
- reason 用一句简短中文说明计划。
"""


@dataclass(frozen=True, slots=True)
class CommandPlan:
    action: str
    query: str | None = None
    section: str = "湖底"
    min_likes: int | None = None
    count: int = 3
    since: str | None = None
    exclude_pinned: bool = False
    only_images: bool = False
    include_images: bool = False
    include_comments: bool = False
    source: str = "hybrid"
    max_pages: int = 20
    max_seconds: int = 300
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class CommandPlanner:
    def __init__(self, llm: LlmController):
        self.llm = llm

    def plan(self, instruction: str) -> CommandPlan:
        instruction = instruction.strip()
        if not instruction:
            raise ValueError("指令不能为空。")
        payload = self.llm.request_json(
            PLAN_SYSTEM_PROMPT,
            {"instruction": instruction},
            PLAN_SCHEMA,
            "forum_command",
            max_output_tokens=700,
        )
        return self._validate(payload)

    @staticmethod
    def _validate(payload: dict) -> CommandPlan:
        action = str(payload.get("action", "")).strip().lower()
        allowed_actions = {"find", "search", "browse", "sections", "help", "exit"}
        if action not in allowed_actions:
            raise LlmError(f"模型规划了未授权动作：{action or '(空)'}")

        section = str(payload.get("section", "湖底")).strip()
        if section.endswith("区") and section[:-1] in SECTIONS:
            section = section[:-1]
        if section in {"全站", "所有", "所有分区", "全部分区"}:
            section = "全部"
        if section not in {*SECTIONS, "全部"}:
            raise LlmError(f"模型返回了未知分区：{section}")

        source = str(payload.get("source", "hybrid")).strip().lower()
        if source not in {"local", "live", "hybrid"}:
            raise LlmError(f"模型返回了未知搜索来源：{source}")

        query_value = payload.get("query")
        query = query_value.strip() if isinstance(query_value, str) else None
        if action == "search" and not query:
            raise LlmError("模型选择了搜索，但没有给出关键词。")

        min_likes_value = payload.get("min_likes")
        min_likes = min_likes_value if isinstance(min_likes_value, int) else None
        if min_likes is not None:
            min_likes = max(0, min_likes)

        since_value = payload.get("since")
        since = since_value.strip() if isinstance(since_value, str) and since_value.strip() else None
        if since:
            parse_since(since)

        return CommandPlan(
            action=action,
            query=query,
            section=section,
            min_likes=min_likes,
            count=_bounded_int(payload.get("count"), default=3, minimum=1, maximum=20),
            since=since,
            exclude_pinned=bool(payload.get("exclude_pinned", False)),
            only_images=bool(payload.get("only_images", False)),
            include_images=bool(payload.get("include_images", False)),
            include_comments=bool(payload.get("include_comments", False)),
            source=source,
            max_pages=_bounded_int(
                payload.get("max_pages"), default=20, minimum=1, maximum=50
            ),
            max_seconds=_bounded_int(
                payload.get("max_seconds"), default=300, minimum=10, maximum=900
            ),
            reason=str(payload.get("reason", "")).strip(),
        )


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        value = default
    return min(maximum, max(minimum, value))


HELP_TEXT = """你可以直接输入，例如：
  给我在湖底找 3 篇超过 10 赞的帖子
  搜索有关国创赛的最新内容
  从学习区找两篇带图帖，把评论也带回来
  随便刷 5 屏看看
  有哪些分区

输入 help 查看帮助，输入 exit / quit / 退出 结束程序。
所有论坛操作均为只读，并受数量、页数和时间预算限制。"""


class ConsoleAgent:
    def __init__(
        self,
        config: AppConfig,
        data_dir: Path,
        adb_path: str | None = None,
        serial: str | None = None,
    ):
        self.config = config
        self.data_dir = data_dir
        self.adb_path = adb_path
        self.serial = serial
        self.llm = LlmController(config.llm)
        self.planner = CommandPlanner(self.llm)

    def _adb_client(self) -> AdbClient:
        client = AdbClient(find_adb(self.adb_path), serial=self.serial)
        client.run("start-server")
        client.select_device()
        client.wait_until_booted()
        return client

    def execute(self, plan: CommandPlan) -> bool:
        print(f"\n计划：{_plan_summary(plan)}")
        if plan.reason:
            print(f"说明：{plan.reason}")

        if plan.action == "exit":
            print("已退出。")
            return False
        if plan.action == "help":
            print(f"\n{HELP_TEXT}")
            return True
        if plan.action == "sections":
            print("\n可用分区：" + "、".join(SECTIONS))
            return True

        if plan.action == "search" and plan.source == "local":
            spec = _query_spec(plan)
            posts = LocalIndex(self.data_dir).search(spec)
            result = QueryResult(
                posts=posts,
                source="local",
                pages_scanned=0,
                run_dir=None,
                stopped_reason="target_count" if len(posts) >= plan.count else "local_exhausted",
            )
            _print_query_result(result)
            return True

        client = self._adb_client()
        if plan.action == "browse":
            agent = BrowseAgent(
                client,
                self.data_dir,
                settle_seconds=self.config.agent.settle_seconds,
                llm=self.llm,
                goal=self.config.agent.goal,
                send_body_chars=self.config.agent.send_body_chars,
            )
            result = agent.browse(
                pages=plan.max_pages,
                latest=True,
                screenshots=False,
                stop_after_stale_pages=self.config.agent.stop_after_stale_pages,
            )
            posts = _read_jsonl(result.run_dir / "posts.jsonl")
            _print_posts(posts[: plan.count])
            print(
                f"\n刷了 {result.pages_scanned} 屏，看到 {result.posts_seen} 篇，"
                f"其中 {result.new_posts} 篇为新帖。"
            )
            print(f"结果目录：{result.run_dir.resolve()}")
            return True

        forum = ForumClient(client, settle_seconds=self.config.agent.settle_seconds)
        engine = QueryEngine(forum, self.data_dir)
        spec = _query_spec(plan)
        options = {
            "max_pages": plan.max_pages,
            "max_seconds": plan.max_seconds,
            "include_images": plan.include_images,
            "include_comments": plan.include_comments,
            "screenshots": False,
        }
        if plan.action == "find":
            result = engine.live(spec, **options)
        else:
            result = engine.search(spec, source=plan.source, **options)
        _print_query_result(result)
        return True


def _plan_summary(plan: CommandPlan) -> str:
    if plan.action == "find":
        parts = [f"实时查找 {plan.section} 区帖子", f"目标 {plan.count} 篇"]
    elif plan.action == "search":
        parts = [f"搜索“{plan.query}”", f"范围 {plan.section}", f"目标 {plan.count} 篇"]
    elif plan.action == "browse":
        parts = [f"最多刷 {plan.max_pages} 屏", f"返回前 {plan.count} 篇"]
    else:
        return plan.action
    if plan.min_likes is not None:
        parts.append(f"至少 {plan.min_likes} 赞")
    if plan.since:
        parts.append(f"时间 {plan.since}")
    if plan.only_images:
        parts.append("仅带图帖")
    if plan.include_images:
        parts.append("保存图片")
    if plan.include_comments:
        parts.append("读取评论")
    return "；".join(parts)


def _query_spec(plan: CommandPlan) -> QuerySpec:
    return QuerySpec(
        query=plan.query,
        section=plan.section,
        min_likes=plan.min_likes,
        count=plan.count,
        since=parse_since(plan.since),
        exclude_pinned=plan.exclude_pinned,
        only_images=plan.only_images,
    )


def _print_query_result(result: QueryResult) -> None:
    _print_posts(result.posts)
    print(
        f"\n共返回 {len(result.posts)} 篇；来源：{result.source}；"
        f"扫描：{result.pages_scanned} 屏；停止原因：{result.stopped_reason}。"
    )
    if result.run_dir:
        print(f"结果目录：{result.run_dir}")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _print_posts(posts: list[dict]) -> None:
    if not posts:
        print("\n没有找到符合条件的帖子。")
        return
    for index, post in enumerate(posts, 1):
        print("\n" + "=" * 72)
        print(f"[{index}] {post.get('title') or '(无标题)'}  #{post.get('post_id', '?')}")
        print(
            f"作者：{post.get('author', '?')}  等级：{post.get('level') or '?'}  "
            f"分区：{post.get('section') or '?'}  时间：{post.get('published_at') or '?'}"
        )
        print(
            f"点赞：{post.get('likes', '?')}  回复：{post.get('replies', '?')}  "
            f"浏览：{post.get('views', '?')}"
        )
        body = str(post.get("body") or "").strip()
        if body:
            print("-" * 72)
            print(body)
        images = post.get("images") or []
        if images:
            print("图片：")
            for path in images:
                print(f"  {path}")
        comments = post.get("comments") or []
        if comments:
            print("评论：")
            for comment in comments:
                author = comment.get("author", "?")
                level = comment.get("level") or "?"
                published = comment.get("published_at") or "?"
                likes = comment.get("likes", "?")
                print(f"  - {author} {level}｜{published}｜{likes} 赞")
                print(f"    {comment.get('body', '')}")


def run_console(agent: ConsoleAgent, ask: str | None = None, plan_only: bool = False) -> int:
    print("WePeiYang Agent CMD｜只读模式")
    print("输入自然语言指令；输入 help 查看示例，输入 exit 退出。")

    if ask is not None:
        return _run_instruction(agent, ask, plan_only)

    while True:
        try:
            instruction = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            return 0
        if not instruction:
            continue
        if instruction.casefold() in {"exit", "quit", "q", "退出"}:
            print("已退出。")
            return 0
        if instruction.casefold() in {"help", "?", "帮助"}:
            print(f"\n{HELP_TEXT}")
            continue
        exit_code = _run_instruction(agent, instruction, plan_only=False)
        if exit_code == 2:
            return 0
        if exit_code != 0:
            print("你可以修改指令后继续。")


def _run_instruction(agent: ConsoleAgent, instruction: str, plan_only: bool) -> int:
    try:
        print("\n正在理解指令……")
        plan = agent.planner.plan(instruction)
        if plan_only:
            print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
            return 0
        keep_running = agent.execute(plan)
        return 0 if keep_running else 2
    except (AdbError, LlmError, OSError, ValueError) as exc:
        print(f"\n错误：{exc}")
        return 1
