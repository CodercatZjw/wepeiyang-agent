from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .adb import AdbClient, AdbError, find_adb
from .agent import BrowseAgent, PACKAGE
from .config import ConfigError, load_config
from .llm import LlmController, LlmError
from .forum import ForumClient, SECTIONS
from .query import QueryEngine, QuerySpec, parse_since


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wpy-agent",
        description="在蓝叠中只读浏览微北洋论坛，并把看到的帖子保存为 JSONL。",
    )
    parser.add_argument("--adb", help="ADB/HD-Adb.exe 的路径；通常可自动发现")
    parser.add_argument("--serial", help="安卓设备序列号；只有一个设备时无需填写")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data",
        help="采集结果目录（默认：项目下的 data）",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config.json",
        help="LLM 和 Agent 配置文件（默认：项目下的 config.json）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="检查蓝叠、ADB、设备和天外天安装状态")
    sections = subparsers.add_parser("sections", help="列出可用论坛分区")
    sections.add_argument("--json", action="store_true", help="输出 JSON")
    browse = subparsers.add_parser("browse", help="开始一次只读刷帖")
    browse.add_argument("--pages", type=int, help="最多滚动采集多少屏（默认读取配置）")
    browse.add_argument("--keep-sort", action="store_true", help="保留当前排序，不切到“最新发帖”")
    browse.add_argument("--no-screenshots", action="store_true", help="不保存每屏截图")
    browse.add_argument("--no-llm", action="store_true", help="调试用：不用 LLM，按固定规则滚动")
    browse.add_argument(
        "--stop-after-stale-pages",
        type=int,
        help="连续多少屏没有新帖子后停止（默认读取配置）",
    )
    browse.add_argument(
        "--settle-seconds",
        type=float,
        help="每次页面操作后等待秒数（默认读取配置）",
    )
    subparsers.add_parser("llm-test", help="调用一次 LLM API，检查 URL、Key 和返回格式")

    def add_query_arguments(command: argparse.ArgumentParser, search: bool = False) -> None:
        command.add_argument(
            "--query", required=search, help="标题或正文关键词；用 | 分隔同义词（任一匹配）"
        )
        command.add_argument(
            "--section",
            choices=[*SECTIONS, "全部"],
            default="全部" if search else "湖底",
            help="论坛分区",
        )
        command.add_argument("--min-likes", type=int, help="最低点赞数")
        command.add_argument("--count", type=int, default=3, help="目标帖子数（默认 3）")
        command.add_argument("--since", help="最早时间：YYYY-MM-DD、7d 或 12h")
        command.add_argument("--exclude-pinned", action="store_true", help="排除置顶帖")
        command.add_argument("--only-images", action="store_true", help="只返回含图片的帖子")
        command.add_argument("--include-images", action="store_true", help="进入详情并保存图片区域")
        command.add_argument("--include-comments", action="store_true", help="进入详情并采集评论")
        command.add_argument("--max-pages", type=int, default=20, help="每个分区最多扫描屏数")
        command.add_argument("--max-seconds", type=int, default=300, help="每个分区最长运行秒数")
        command.add_argument("--settle-seconds", type=float, default=2.5, help="页面稳定等待秒数")
        command.add_argument("--no-screenshots", action="store_true", help="不保存信息流截图")
        command.add_argument("--json", action="store_true", help="输出结构化 JSON")

    find = subparsers.add_parser("find", help="实时浏览并按条件寻找帖子")
    add_query_arguments(find)
    search = subparsers.add_parser("search", help="在本地索引和实时论坛中搜索")
    add_query_arguments(search, search=True)
    search.add_argument(
        "--source", choices=["local", "live", "hybrid"], default="hybrid", help="搜索来源"
    )
    return parser


def create_client(args: argparse.Namespace) -> AdbClient:
    adb_path = find_adb(args.adb)
    client = AdbClient(adb_path, serial=args.serial)
    client.run("start-server")
    client.select_device()
    client.wait_until_booted()
    return client


def doctor(args: argparse.Namespace) -> int:
    client = create_client(args)
    installed = client.package_installed(PACKAGE)
    print(f"ADB：{client.adb_path}")
    print(f"设备：{client.serial}")
    print(f"天外天：{'已安装' if installed else '未安装'}")
    if not installed:
        return 2
    print("检查通过，可以运行 browse。")
    return 0


def llm_test(args: argparse.Namespace) -> int:
    config = load_config(args.config.resolve(), require_llm=True)
    controller = LlmController(config.llm)
    decision = controller.decide(
        {
            "goal": config.agent.goal,
            "page": 1,
            "max_pages": 2,
            "unique_posts_seen": 3,
            "new_unique_posts_on_page": 3,
            "consecutive_stale_pages": 0,
            "allowed_actions": ["scroll", "stop"],
            "posts": [{"post_id": "MP_TEST", "title": "连接测试", "body_preview": ""}],
        }
    )
    print(f"LLM 连接成功：{decision.action}｜{decision.reason}")
    return 0


def browse(args: argparse.Namespace) -> int:
    config = load_config(args.config.resolve(), require_llm=not args.no_llm)
    pages = args.pages if args.pages is not None else config.agent.max_pages
    stale_pages = (
        args.stop_after_stale_pages
        if args.stop_after_stale_pages is not None
        else config.agent.stop_after_stale_pages
    )
    settle_seconds = (
        args.settle_seconds
        if args.settle_seconds is not None
        else config.agent.settle_seconds
    )
    if pages < 1 or stale_pages < 1:
        raise AdbError("--pages 和 --stop-after-stale-pages 必须大于 0。")
    client = create_client(args)
    controller = None if args.no_llm else LlmController(config.llm)
    agent = BrowseAgent(
        client,
        args.data_dir.resolve(),
        settle_seconds=settle_seconds,
        llm=controller,
        goal=config.agent.goal,
        send_body_chars=config.agent.send_body_chars,
    )
    result = agent.browse(
        pages=pages,
        latest=not args.keep_sort,
        screenshots=config.agent.save_screenshots and not args.no_screenshots,
        stop_after_stale_pages=stale_pages,
    )
    print(f"本次刷了 {result.pages_scanned} 屏，看到 {result.posts_seen} 篇帖子。")
    print(f"其中 {result.new_posts} 篇是此前未记录的新帖。")
    print(f"结果：{result.run_dir}")
    return 0


def _query_spec(args: argparse.Namespace) -> QuerySpec:
    if args.count < 1 or args.max_pages < 1 or args.max_seconds < 1:
        raise ValueError("count、max-pages 和 max-seconds 必须大于 0。")
    if args.min_likes is not None and args.min_likes < 0:
        raise ValueError("min-likes 不能小于 0。")
    return QuerySpec(
        query=args.query,
        section=args.section,
        min_likes=args.min_likes,
        count=args.count,
        since=parse_since(args.since),
        exclude_pinned=args.exclude_pinned,
        only_images=args.only_images,
    )


def _print_query_result(result, as_json: bool) -> None:
    payload = result.to_dict()
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(
        f"找到 {len(result.posts)} 篇帖子；来源 {result.source}；"
        f"扫描 {result.pages_scanned} 屏；停止原因 {result.stopped_reason}。"
    )
    for index, post in enumerate(result.posts, 1):
        metrics = f"👍 {post.get('likes', '?')}｜💬 {post.get('replies', '?')}｜👁 {post.get('views', '?')}"
        print(f"{index}. [{post.get('post_id')}] {post.get('title')}（{metrics}）")
        if post.get("images"):
            print(f"   图片：{', '.join(post['images'])}")
        if post.get("comments"):
            print(f"   评论：{len(post['comments'])} 条")
    if result.run_dir:
        print(f"结果：{result.run_dir}")


def run_query(args: argparse.Namespace, source: str) -> int:
    client = create_client(args)
    forum = ForumClient(client, settle_seconds=args.settle_seconds)
    engine = QueryEngine(forum, args.data_dir.resolve())
    spec = _query_spec(args)
    options = {
        "max_pages": args.max_pages,
        "max_seconds": args.max_seconds,
        "include_images": args.include_images,
        "include_comments": args.include_comments,
        "screenshots": not args.no_screenshots,
    }
    result = engine.live(spec, **options) if source == "live" else engine.search(spec, source=source, **options)
    _print_query_result(result, args.json)
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            return doctor(args)
        if args.command == "sections":
            if args.json:
                print(json.dumps({"sections": list(SECTIONS)}, ensure_ascii=False))
            else:
                print("、".join(SECTIONS))
            return 0
        if args.command == "llm-test":
            return llm_test(args)
        if args.command == "browse":
            return browse(args)
        if args.command == "find":
            return run_query(args, source="live")
        if args.command == "search":
            return run_query(args, source=args.source)
        parser.error("未知命令")
    except (AdbError, ConfigError, LlmError, OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0
