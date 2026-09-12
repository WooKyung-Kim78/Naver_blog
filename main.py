"""네이버 블로그 자동 포스팅 CLI.

사용법:
    python main.py doctor                     설정과 API 연결 상태 점검
    python main.py post --url <상품페이지 URL>  포스팅 워크플로우 실행
    python main.py post --url <URL> --dry-run  발행 없이 원고와 이미지만 생성
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import config
from ai_client import AIError, MyGenAssistClient
from copywriter import BlogPost, Concept, propose_concepts, write_post
from images import StockImageFetcher
from product import fetch as fetch_product

console = Console()


def main() -> int:
    parser = argparse.ArgumentParser(description="네이버 블로그 자동 포스팅")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="설정과 API 연결 점검")

    post = sub.add_parser("post", help="상품 URL 로 포스팅")
    post.add_argument("--url", required=True, help="홍보할 상품 페이지 URL")
    post.add_argument("--desc", default="", help="상품 설명을 직접 입력(사이트가 크롤링을 막을 때)")
    post.add_argument("--concepts", type=int, default=5, help="제안받을 컨셉 개수")
    post.add_argument("--dry-run", action="store_true", help="네이버에 올리지 않고 원고만 생성")
    post.add_argument("--debug", action="store_true", help="브라우저 스크린샷 저장")

    args = parser.parse_args()

    try:
        if args.command == "doctor":
            return cmd_doctor()
        return cmd_post(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]중단되었습니다.[/yellow]")
        return 130
    except config.ConfigError as exc:
        console.print(f"\n[red]설정 오류:[/red] {exc}")
        return 1
    except (AIError, RuntimeError) as exc:
        console.print(f"\n[red]오류:[/red] {exc}")
        return 1


# --------------------------------------------------------------------- doctor


def cmd_doctor() -> int:
    if not (config.ROOT / ".env").exists():
        console.print("[red].env 파일이 없습니다.[/red] .env.example 을 복사해서 .env 로 만들고 값을 채우세요.")
        return 1

    table = Table("항목", "상태", title="설정 점검", title_justify="left")

    ai_cfg = config.load_ai_config()
    try:
        ai_cfg.validate()
        client = MyGenAssistClient(ai_cfg)
        account = client.ping()
        name = account.get("name") or account.get("email") or "확인됨"
        table.add_row("Bayer AI API", f"[green]연결 성공[/green] ({name})")
    except Exception as exc:
        table.add_row("Bayer AI API", f"[red]실패[/red] {str(exc)[:120]}")
        client = None

    if client:
        try:
            answer = client.chat("한 단어로만 답한다.", "테스트라고만 답해줘.", max_tokens=2000, websearch=False)
            table.add_row(f"모델 {ai_cfg.chat_model}", f"[green]응답 성공[/green] ({answer.strip()[:40]})")
        except Exception as exc:
            table.add_row(f"모델 {ai_cfg.chat_model}", f"[red]실패[/red] {str(exc)[:200]}")

    img_cfg = config.load_image_config()
    try:
        img_cfg.validate()
        results = StockImageFetcher(img_cfg).search("coffee", per_page=1)
        table.add_row(f"이미지 ({img_cfg.provider})", f"[green]검색 성공[/green] ({len(results)}건)")
    except Exception as exc:
        table.add_row(f"이미지 ({img_cfg.provider})", f"[red]실패[/red] {str(exc)[:120]}")

    naver_cfg = config.load_naver_config()
    try:
        naver_cfg.validate()
        table.add_row("네이버 설정", f"[green]입력됨[/green] (blog.naver.com/{naver_cfg.blog_id}, {naver_cfg.post_mode})")
    except config.ConfigError as exc:
        table.add_row("네이버 설정", f"[red]{exc}[/red]")

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
        table.add_row("Playwright", "[green]브라우저 준비됨[/green]")
    except Exception as exc:
        table.add_row("Playwright", f"[red]실패[/red] {str(exc)[:100]} → playwright install chromium")

    console.print(table)
    return 0


# ----------------------------------------------------------------------- post


def cmd_post(args) -> int:
    ai_cfg = config.load_ai_config()
    img_cfg = config.load_image_config()
    post_cfg = config.load_post_config()

    client = MyGenAssistClient(ai_cfg)

    # 1. 상품 정보 수집
    console.print(f"\n[bold cyan]1/5[/bold cyan] 상품 페이지 분석 중... {args.url}")
    product = fetch_product(args.url, verify_ssl=ai_cfg.verify_ssl, proxies=ai_cfg.proxies)

    if args.desc:
        product.body_text = f"{args.desc}\n\n{product.body_text}"
    console.print(f"  상품명: [white]{product.title or '(없음)'}[/white]")
    console.print(f"  본문 {len(product.body_text):,}자 / 이미지 {len(product.image_urls)}개 수집")

    if len(product.body_text) < 300:
        console.print(
            "\n[yellow]페이지에서 정보를 거의 읽지 못했습니다.[/yellow] "
            "쿠팡처럼 크롤링을 막는 사이트일 수 있습니다."
        )
        console.print("상품 설명을 직접 붙여넣어 주세요. 다 넣었으면 빈 줄에서 Enter 를 두 번 누르세요.")
        typed = _read_multiline()
        if not typed:
            console.print("[red]상품 정보가 없어 진행할 수 없습니다.[/red]")
            return 1
        product.body_text = typed
        product.title = product.title or typed.splitlines()[0][:80]

    # 2. 컨셉 제안 및 선택
    console.print(f"\n[bold cyan]2/5[/bold cyan] 트렌드 조사 후 홍보 컨셉 {args.concepts}개 생성 중... (1~2분 소요)")
    concepts = propose_concepts(client, product, count=args.concepts)
    concept = _choose_concept(concepts)

    # 3. 본문 작성
    console.print("\n[bold cyan]3/5[/bold cyan] 블로그 본문 작성 중...")
    post = write_post(client, product, concept, post_cfg)

    while True:
        console.print(Panel(post.preview(), title=f"[bold]{post.title}[/bold]", border_style="green"))
        choice = _ask("이 원고로 진행할까요? (y: 진행 / r: 다시 작성 / q: 종료)", ("y", "r", "q"))
        if choice == "y":
            break
        if choice == "q":
            return 0
        console.print("  다시 작성 중...")
        post = write_post(client, product, concept, post_cfg)

    # 4. 이미지 준비
    console.print("\n[bold cyan]4/5[/bold cyan] 이미지 검색 및 다운로드 중...")
    run_dir = config.OUTPUT_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{_slug(post.title)}"
    blocks = _build_blocks(post, img_cfg, run_dir)
    _save_run(run_dir, product, concept, post, blocks)
    console.print(f"  원고와 이미지 저장 위치: [white]{run_dir}[/white]")

    # 5. 네이버 발행
    if args.dry_run:
        console.print("\n[yellow]--dry-run 이므로 네이버에는 올리지 않았습니다.[/yellow]")
        return 0

    naver_cfg = config.load_naver_config()
    naver_cfg.validate()
    mode_label = "임시저장" if naver_cfg.post_mode == "draft" else "즉시 발행"
    if _ask(f"\n[bold cyan]5/5[/bold cyan] 네이버 블로그에 {mode_label} 할까요? (y/n)", ("y", "n")) != "y":
        console.print("중단했습니다. 원고는 저장되어 있습니다.")
        return 0

    from naver_blog import NaverBlogPublisher, PostBlock

    payload = [PostBlock(kind=k, value=v) for k, v in blocks]
    with NaverBlogPublisher(naver_cfg, debug=args.debug) as publisher:
        console.print("  네이버 로그인 중...")
        publisher.ensure_login()
        console.print("  글쓰기 화면에 내용 입력 중...")
        result = publisher.publish(post.title, payload, post.tags)

    console.print(f"\n[bold green]{result}[/bold green]")
    return 0


def _choose_concept(concepts: list[Concept]) -> Concept:
    for i, c in enumerate(concepts, 1):
        body = (
            f"[bold]{c.title}[/bold]\n\n"
            f"접근    : {c.angle}\n"
            f"후킹    : {c.hook}\n"
            f"타깃    : {c.target}\n"
            f"키워드  : {', '.join(c.keywords)}\n"
            f"트렌드  : {c.reason}"
        )
        console.print(Panel(body, title=f"[cyan]{i}번[/cyan]", border_style="cyan"))

    valid = tuple(str(i) for i in range(1, len(concepts) + 1))
    return concepts[int(_ask(f"어떤 컨셉으로 쓸까요? (1-{len(concepts)})", valid)) - 1]


def _read_multiline() -> str:
    lines: list[str] = []
    blanks = 0
    while blanks < 2:
        line = input()
        if line.strip():
            lines.append(line)
            blanks = 0
        else:
            blanks += 1
    return "\n".join(lines).strip()


def _ask(prompt: str, valid: tuple[str, ...]) -> str:
    while True:
        console.print(prompt)
        answer = input("> ").strip().lower()
        if answer in valid:
            return answer
        console.print(f"[yellow]{' / '.join(valid)} 중에서 입력해주세요.[/yellow]")


def _build_blocks(post: BlogPost, img_cfg, run_dir: Path) -> list[tuple[str, str]]:
    """본문 블록의 image_query 를 실제 다운로드된 이미지 경로로 바꾼다."""
    fetcher = StockImageFetcher(img_cfg)
    image_dir = run_dir / "images"
    resolved: list[tuple[str, str]] = []
    used = 0

    for kind, value in post.to_blocks():
        if kind != "image_query":
            resolved.append((kind, value))
            continue
        if used >= img_cfg.count:
            continue
        try:
            candidates = fetcher.search(value, per_page=1)
            if not candidates:
                console.print(f"  [yellow]'{value}' 검색 결과 없음, 건너뜁니다.[/yellow]")
                continue
            path = fetcher.download(candidates[0], image_dir, f"{used + 1}_{value}")
            resolved.append(("image", str(path)))
            used += 1
            console.print(f"  [green]OK[/green] {value} → {path.name}")
        except Exception as exc:
            console.print(f"  [yellow]'{value}' 다운로드 실패:[/yellow] {_explain(exc)}")

    return resolved


def _explain(exc: Exception) -> str:
    text = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in text or "SSLError" in type(exc).__name__:
        return (
            "사내망 SSL 검사 때문에 인증서 검증에 실패했습니다.\n"
            "     [white]pip install truststore[/white] 를 실행하면 해결됩니다."
        )
    if "ProxyError" in type(exc).__name__ or "Max retries" in text:
        return f"네트워크 연결 실패. 사내 프록시가 필요하면 .env 의 HTTPS_PROXY 를 채우세요.\n     {text[:200]}"
    return text[:300]


def _save_run(run_dir: Path, product, concept: Concept, post: BlogPost, blocks) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "post.txt").write_text(post.preview(), encoding="utf-8")
    (run_dir / "post.json").write_text(
        json.dumps(
            {
                "product_url": product.url,
                "product_title": product.title,
                "concept": concept.__dict__,
                "title": post.title,
                "intro": post.intro,
                "sections": [s.__dict__ for s in post.sections],
                "outro": post.outro,
                "tags": post.tags,
                "disclosure": post.disclosure,
                "blocks": blocks,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _slug(text: str) -> str:
    return re.sub(r"[^\w가-힣]+", "_", text).strip("_")[:40] or "post"


if __name__ == "__main__":
    sys.exit(main())
