from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .adb import AdbClient, AdbError, find_adb
from .agent import BrowseAgent, PACKAGE
from .config import ConfigError, load_config
from .llm import LlmController, LlmError


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
        if args.command == "llm-test":
            return llm_test(args)
        if args.command == "browse":
            return browse(args)
        parser.error("未知命令")
    except (AdbError, ConfigError, LlmError, OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0
