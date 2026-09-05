# CLI reference

Run commands from:

`C:\Users\Administrator\Documents\Codex\2026-09-05\w\outputs\wepeiyang-agent`

Entrypoint:

`python -m wepeiyang_agent`

## Environment

- `doctor`: verify ADB, emulator, and TianWaiTian installation.
- `sections --json`: list supported forum sections.

## Live conditional browsing

```powershell
python -m wepeiyang_agent find --section 湖底 --min-likes 11 --count 3 --max-pages 30 --max-seconds 300 --json
```

Useful options:

- `--query TEXT`: title/body substring; separate alternatives with `|`.
- `--section`: 精华、湖底、校务、学习、Bugs、交往、美食、社团.
- `--min-likes N`
- `--count N`
- `--since 7d`, `--since 12h`, or `--since YYYY-MM-DD`
- `--exclude-pinned`
- `--only-images`
- `--include-images`: open matching posts and save visible image regions.
- `--include-comments`: open matching posts and collect visible comment pages.
- `--max-pages N`, `--max-seconds N`
- `--no-screenshots`
- `--json`

## Search

```powershell
python -m wepeiyang_agent search --query "国创赛|国创|创新大赛|大创" --section 全部 --source hybrid --count 5 --max-pages 20 --max-seconds 300 --json
```

Sources:

- `local`: existing `data/index.json` only.
- `live`: browse the app and filter fresh pages.
- `hybrid`: local first, then live for missing results. Prefer this for ordinary keyword requests.

## Output

JSON contains:

- `posts`: normalized post objects with text, metrics, section, image paths, and comments.
- `source`: local, live, or hybrid.
- `pages_scanned`
- `run_dir`
- `stopped_reason`: target_count, max_pages, max_seconds, since_boundary, or local_exhausted.

