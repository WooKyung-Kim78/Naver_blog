"""이미 만들어 둔 결과물에서 네이버 업로드용 묶음을 읽는다."""

from __future__ import annotations

import json
import re
from pathlib import Path

from config import OUTPUT_DIR
from publish.naver_blog import PostBlock


def latest_run() -> Path:
    runs = [
        p
        for p in OUTPUT_DIR.iterdir()
        if p.is_dir() and (
            (p / "upload.json").exists()
            or (p / "naver.txt").exists()
            or (p / "article.json").exists()
        )
    ]
    if not runs:
        raise FileNotFoundError(
            f"{OUTPUT_DIR} 에 올릴 결과물이 없습니다. 먼저 python main.py post --dry-run 으로 원고를 만드세요."
        )
    return max(runs, key=lambda p: p.stat().st_mtime)


def load_run(run_dir: Path) -> tuple[str, list[str], list[PostBlock]]:
    """(제목, 태그, 에디터 조작 목록)을 돌려준다."""
    run_dir = Path(run_dir)
    if not run_dir.exists():
        raise FileNotFoundError(f"결과 폴더가 없습니다: {run_dir}")

    upload = run_dir / "upload.json"
    if upload.exists():
        data = json.loads(upload.read_text(encoding="utf-8"))
        ops = [PostBlock(kind=str(o.get("kind") or "text"), value=str(o.get("value") or "")) for o in data.get("ops") or []]
        return str(data.get("title") or ""), list(data.get("tags") or []), ops

    report = run_dir / "report.json"
    title, tags = "", []
    if report.exists():
        data = json.loads(report.read_text(encoding="utf-8"))
        best = data.get("best") or {}
        title = str(best.get("title") or "")
        tags = list(best.get("tags") or [])

    naver = run_dir / "naver.txt"
    if not naver.exists():
        raise FileNotFoundError(f"{run_dir} 에 upload.json 또는 naver.txt 가 없습니다.")
    return title, tags, _parse_naver_txt(naver.read_text(encoding="utf-8"))


def _parse_naver_txt(text: str) -> list[PostBlock]:
    """예전 결과물처럼 upload.json 이 없을 때 naver.txt 를 읽는다."""
    ops: list[PostBlock] = []
    for chunk in re.split(r"\n(?=\[(?:text|image|quote|divider)\])", text.strip()):
        match = re.match(r"\[(\w+)\]\s?(.*)", chunk, re.S)
        if not match:
            continue
        kind, value = match.group(1), match.group(2).strip()
        ops.append(PostBlock(kind=kind, value=value))
    return ops
