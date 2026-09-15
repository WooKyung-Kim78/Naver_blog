"""네이버 상품 리뷰 블로그 생성기 CLI.

사용법:
    python main.py doctor                      설정과 API 연결 상태 점검
    python main.py post --url <상품 URL>        생성 후 네이버에 업로드
    python main.py post --url <URL> --dry-run   업로드 없이 원고와 이미지만 생성
    python main.py upload                      이미 만든 결과물을 네이버에 임시저장/발행
    python main.py upload --from output/폴더    특정 결과물만 다시 올리기
    python main.py upload --md 제안.md          수정 제안 마크다운으로 article 을 다시 쓴 뒤 올리기
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import config
from ai.client import AIError, MyGenAssistClient
from core.models import KIND_CTA, KIND_HEADING, KIND_IMAGE, KIND_LINK, FocusChoice, FocusPoint
from pipeline import MAX_PRODUCTS, Pipeline, PipelineResult
from scrape.product import ProductUnavailable
from publish.naver_blog import NaverBlogError

console = Console()


def main() -> int:
    parser = argparse.ArgumentParser(description="네이버 상품 리뷰 블로그 생성기")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="설정과 API 연결 점검")

    post = sub.add_parser("post", help="상품 URL 로 리뷰 생성")
    post.add_argument(
        "--url", action="append", required=True, dest="urls",
        help="본문에 넣을 구매 링크. 여러 번 주면 묶어 한 글로 쓴다 (최대 4개)",
    )
    post.add_argument(
        "--page-url", action="append", default=[], dest="page_urls",
        help="내용을 긁어올 실제 상품 페이지. --url 과 같은 순서로. 생략하면 물어본다",
    )
    post.add_argument("--desc", default="", help="상품 설명 직접 입력(사이트가 크롤링을 막을 때)")
    post.add_argument("--dry-run", action="store_true", help="네이버에 올리지 않고 원고만 생성")
    post.add_argument("--no-images", action="store_true", help="상세페이지 이미지 수집 건너뛰기(빠름)")
    post.add_argument("--open", action="store_true", help="완성된 HTML 미리보기를 브라우저로 열기")
    post.add_argument("--md", dest="md_path", default="", help="수정 제안 마크다운. 있으면 그 제안으로 article 을 다시 쓴 뒤 올린다")
    post.add_argument("--debug", action="store_true", help="브라우저 스크린샷 저장")
    post.add_argument("--auto-focus", action="store_true", help="집중 포인트를 묻지 않고 1안으로 진행")

    upload = sub.add_parser("upload", help="이미 만든 결과물을 네이버에 올리기 (원고 생성 생략)")
    upload.add_argument(
        "--from", dest="run_dir", default="",
        help="결과 폴더. 생략하면 output/ 에서 가장 최근 것을 쓴다",
    )
    upload.add_argument("--md", dest="md_path", default="", help="수정 제안 마크다운. 있으면 그 제안으로 article 을 다시 쓴 뒤 올린다")
    upload.add_argument("--debug", action="store_true", help="브라우저 스크린샷 저장")

    args = parser.parse_args()

    try:
        if args.command == "doctor":
            return cmd_doctor()
        if args.command == "upload":
            return cmd_upload(args)
        return cmd_post(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]중단되었습니다.[/yellow]")
        return 130
    except config.ConfigError as exc:
        console.print(f"\n[red]설정 오류:[/red] {exc}")
        return 1
    except FileNotFoundError as exc:
        console.print(f"\n[red]결과물을 찾지 못했습니다:[/red] {exc}")
        return 1
    except NaverBlogError as exc:
        console.print()
        console.print(Panel(str(exc), title="네이버 업로드 실패", title_align="left", border_style="red"))
        return 1
    except ProductUnavailable as exc:
        console.print()
        console.print(Panel(str(exc), title="상품 정보를 가져오지 못했습니다",
                            title_align="left", border_style="red"))
        return 1
    except (AIError, RuntimeError) as exc:
        console.print(f"\n[red]오류:[/red] {explain(exc)}")
        return 1


# --------------------------------------------------------------------- doctor


def cmd_doctor() -> int:
    if not (config.ROOT / ".env").exists():
        console.print("[red].env 파일이 없습니다.[/red] .env.example 을 복사해 .env 로 만들고 값을 채우세요.")
        return 1

    table = Table("항목", "상태", title="설정 점검", title_justify="left")

    ai_cfg = config.load_ai_config()
    client = None
    try:
        ai_cfg.validate()
        client = MyGenAssistClient(ai_cfg)
        account = client.ping()
        table.add_row("Bayer AI API", f"[green]연결 성공[/green] ({account.get('email') or '확인됨'})")
    except Exception as exc:
        table.add_row("Bayer AI API", f"[red]실패[/red] {str(exc)[:140]}")

    if client:
        try:
            answer = client.chat("한 단어로만 답한다.", "테스트라고만 답해줘.", max_tokens=2000, websearch=False)
            table.add_row(f"모델 {ai_cfg.chat_model}", f"[green]응답 성공[/green] ({answer.strip()[:40]})")
        except Exception as exc:
            table.add_row(f"모델 {ai_cfg.chat_model}", f"[red]실패[/red] {str(exc)[:200]}")

    img_cfg = config.load_image_config()
    try:
        img_cfg.validate()
        from media.stock import StockImageFetcher

        found = StockImageFetcher(img_cfg).search("coffee", per_page=1)
        table.add_row(f"스톡 이미지 ({img_cfg.provider})", f"[green]검색 성공[/green] ({len(found)}건)")
    except Exception as exc:
        table.add_row(f"스톡 이미지 ({img_cfg.provider})", f"[red]실패[/red] {str(exc)[:140]}")

    gen_cfg = config.load_imagegen_config()
    table.add_row(
        "AI 이미지 생성",
        f"[green]사용 가능[/green] ({gen_cfg.model})"
        if gen_cfg.enabled
        else "[yellow]비활성[/yellow] Bayer API 미지원. 외부 키를 넣으면 켜집니다.",
    )

    q = config.load_quality_config()
    table.add_row("품질 기준", f"{q.pass_mark}점 이상, 최대 {q.max_attempts}회 재생성, 휴머나이징 {'ON' if q.humanize else 'OFF'}")

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
    pipeline = Pipeline(
        report=lambda msg: console.print(f"  {msg}" if msg.startswith(" ") else f"[cyan]›[/cyan] {msg}"),
        choose=(lambda options: FocusChoice(point=options[0])) if args.auto_focus else _choose_focus,
    )

    urls = args.urls
    if len(urls) > MAX_PRODUCTS:
        console.print(f"[red]한 글에 묶을 상품은 {MAX_PRODUCTS}개까지입니다.[/red] 지금은 {len(urls)}개입니다.")
        return 1

    if len(urls) == 1:
        page_url = _match_page_url(urls[0], args.page_urls) or _ask_page_url(urls[0])
        result = pipeline.run(
            urls[0], page_url=page_url, manual_desc=args.desc, collect_images=not args.no_images
        )
    else:
        if args.desc:
            console.print("[yellow]여러 상품을 묶을 때는 --desc 를 쓰지 않습니다. 상품마다 페이지를 읽습니다.[/yellow]")
        items = []
        for i, buy in enumerate(urls, 1):
            page = ""
            if len(args.page_urls) == len(urls):
                page = args.page_urls[i - 1]
            else:
                page = _ask_page_url(buy, index=i, total=len(urls))
            items.append((buy, page, ""))
        result = pipeline.run_roundup(items, collect_images=not args.no_images)

    _show_report(result)
    console.print(f"\n결과 저장 위치: [white]{result.run_dir}[/white]")
    console.print("  article.html  미리보기 / suggestions.md  수정 제안 / naver.txt  업로드될 내용")
    console.print(f"  업로드만 다시 하려면: [white]python main.py upload --from {result.run_dir}[/white]")

    if args.dry_run:
        if args.open:
            webbrowser.open((result.run_dir / "article.html").as_uri())
        console.print("\n[yellow]--dry-run 이므로 네이버에는 올리지 않았습니다.[/yellow]")
        console.print(f"  고친 뒤 올리려면: python main.py upload --from {result.run_dir}")
        return 0

    from publish.naver_blog import PostBlock

    payload = [PostBlock(kind=op.kind, value=op.value) for op in result.ops]
    return _upload_to_naver(
        result.draft.article.title,
        result.draft.article.tags,
        payload,
        run_dir=result.run_dir,
        debug=args.debug,
        md_path=args.md_path,
    )


def cmd_upload(args) -> int:
    from publish.payload import latest_run, load_run

    run_dir = Path(args.run_dir) if args.run_dir else latest_run()
    if args.run_dir and not run_dir.exists():
        run_dir = config.ROOT / args.run_dir
    title, tags, payload = load_run(run_dir)
    console.print(f"올릴 결과물: [white]{run_dir}[/white]")
    console.print(f"  제목: {title or '(제목 없음)'}")
    console.print(f"  블록: {len(payload)}개")
    return _upload_to_naver(
        title, tags, payload, run_dir=run_dir, debug=args.debug, md_path=args.md_path,
    )


def _upload_to_naver(title: str, tags: list[str], payload, *, run_dir, debug: bool, md_path: str = "") -> int:
    from publish.naver_blog import NaverBlogPublisher

    reviewed = _review_before_upload(run_dir, title, tags, payload, md_path=md_path)
    if reviewed is None:
        console.print(f"중단했습니다. 다시 올리려면: python main.py upload --from {run_dir}")
        return 0
    title, tags, payload = reviewed

    naver_cfg = config.load_naver_config()
    naver_cfg.validate()
    mode = "임시저장" if naver_cfg.post_mode == "draft" else "즉시 발행"
    if _ask(f"\n이 내용으로 네이버 블로그에 {mode} 할까요? (y/n)", ("y", "n")) != "y":
        console.print(f"중단했습니다. 다시 올리려면: python main.py upload --from {run_dir}")
        return 0

    try:
        with NaverBlogPublisher(naver_cfg, debug=debug) as publisher:
            console.print("[cyan]›[/cyan] 네이버 로그인 중")
            publisher.ensure_login()
            console.print("[cyan]›[/cyan] 로그인 확인됨. 에디터에 내용 입력 중")
            message = publisher.publish(title, payload, tags)
    except NaverBlogError:
        console.print(f"\n[yellow]원고는 그대로 있습니다.[/yellow] 다시 올리려면:")
        console.print(f"  python main.py upload --from {run_dir}")
        raise

    console.print(f"\n[bold green]{message}[/bold green]")
    return 0


def _review_before_upload(run_dir, title: str, tags: list[str], payload, *, md_path: str):
    """HTML 을 보여 주고, 제안 마크다운으로 article 을 다시 만든 뒤 올린다."""
    from publish.review import ensure_suggestions, rebuild_from_notes

    notes_file = ensure_suggestions(run_dir)
    html_path = run_dir / "article.html"
    webbrowser.open(html_path.as_uri())

    if md_path:
        chosen = _resolve_md(md_path)
        title, tags, payload = _rebuild_article(run_dir, chosen, title, tags, payload)
        webbrowser.open(html_path.as_uri())
        return title, tags, payload

    stamp = notes_file.stat().st_mtime
    console.print()
    console.print(Panel(
        f"[bold]HTML 미리보기[/bold]  {html_path}\n"
        f"[bold]수정 제안[/bold]  {notes_file}\n\n"
        "브라우저에서 글을 확인한 뒤, 고칠 점만 suggestions.md 에 적으세요.\n"
        "Copilot 과 확인한 제안이어도 됩니다. 완성 원고를 넣을 필요는 없습니다.\n"
        "저장하면 그 제안으로 article 을 다시 작성합니다.\n\n"
        "[white]엔터[/white]  이 폴더의 suggestions.md 로 다시 작성\n"
        "[white]경로[/white]  다른 제안 md 로 다시 작성\n"
        "[white]s[/white]     제안 없이 지금 article 로 진행\n"
        "[white]n[/white]     업로드 취소",
        title="업로드 전 검토", title_align="left", border_style="cyan",
    ))

    while True:
        try:
            answer = input("> ").strip()
        except EOFError:
            return title, tags, payload
        if answer.lower() == "n":
            return None
        if answer.lower() == "s":
            return title, tags, payload
        if not answer:
            if notes_file.stat().st_mtime <= stamp:
                console.print("[yellow]suggestions.md 에 제안이 아직 없습니다. 지금 article 로 진행합니다.[/yellow]")
                return title, tags, payload
            chosen = notes_file
        else:
            chosen = _resolve_md(answer)
            if chosen is None:
                console.print("[yellow]그 경로에 md 파일이 없습니다. 다시 넣거나 s / n 을 입력하세요.[/yellow]")
                continue
        title, tags, payload = _rebuild_article(run_dir, chosen, title, tags, payload)
        webbrowser.open(html_path.as_uri())
        return title, tags, payload


def _rebuild_article(run_dir, notes_path: Path, title: str, tags: list[str], payload):
    from publish.review import rebuild_from_notes

    title, tags, payload = rebuild_from_notes(
        run_dir,
        notes_path,
        title,
        tags,
        payload,
        report=lambda msg: console.print(f"[cyan]›[/cyan] {msg}"),
    )
    console.print(Panel(
        f"[white]{notes_path}[/white] 제안으로 article 을 다시 만들었습니다.\n"
        f"제목: {title or '(제목 없음)'}  ·  블록 {len(payload)}개\n"
        "미리보기를 다시 열었습니다. 맞으면 다음에서 업로드를 진행하세요.",
        title="원고 재작성", title_align="left", border_style="green",
    ))
    return title, tags, payload


def _resolve_md(value: str) -> Path | None:
    chosen = Path(value.strip().strip('"'))
    if not chosen.exists():
        chosen = config.ROOT / chosen
    return chosen if chosen.exists() else None


def _match_page_url(buy_url: str, page_urls: list[str]) -> str:
    """--page-url 이 하나이고 상품도 하나일 때만 그대로 쓴다."""
    if len(page_urls) == 1:
        return page_urls[0]
    return ""


def _ask_page_url(buy_url: str, *, index: int = 0, total: int = 0) -> str:
    """내용을 긁어올 실제 상품 페이지를 물어본다.

    브랜드 커넥트 제휴 링크는 중간 페이지를 거쳐서 상품 내용이 제대로 안 잡히는
    경우가 있다. 판매 페이지 주소를 직접 받으면 그 문제가 사라진다.
    """
    title = "실제 상품 페이지"
    if total:
        title = f"실제 상품 페이지 ({index}/{total})"
    console.print()
    console.print(Panel(
        f"[dim]구매 링크[/dim]  {buy_url}\n"
        "이 링크는 블로그 본문의 구매 링크로 그대로 들어갑니다. (제휴 추적 유지)\n\n"
        "내용과 이미지를 긁어올 [bold]실제 상품 판매 페이지[/bold] 주소를 알려주세요.\n"
        "[dim]예: https://brand.naver.com/finevu/products/13030260544[/dim]\n"
        "[dim]모르면 그냥 엔터. 구매 링크를 따라가서 긁습니다.[/dim]",
        title=title, title_align="left", border_style="cyan",
    ))

    while True:
        try:
            answer = input("> ").strip()
        except EOFError:  # 파이프로 실행한 경우
            return buy_url
        if not answer:
            return buy_url
        if answer.startswith(("http://", "https://")):
            return answer
        console.print("[yellow]http 로 시작하는 주소를 넣어주세요. 그냥 쓰려면 엔터.[/yellow]")


def _choose_focus(options: list[FocusPoint]) -> FocusChoice:
    """상세페이지에서 뽑은 상품의 포인트를 보여주고 하나를 고르게 한다."""
    console.print()
    console.print(Panel(
        "상세페이지를 확인해 이 글에서 집중할 만한 점을 정리했습니다.\n"
        "하나를 고르면 글 전체가 그 점을 중심으로 쓰입니다.",
        title="집중 포인트 선택", border_style="cyan",
    ))

    for i, point in enumerate(options, 1):
        body = [
            f"[white]{point.angle}[/white]",
            "",
            f"[dim]상세페이지 근거[/dim] {point.evidence}",
            f"[dim]도움 되는 사람[/dim]  {point.target}",
            f"[dim]중요한 이유[/dim]    {point.why_now}",
        ]
        if point.keywords:
            body.append(f"[dim]키워드[/dim]         {', '.join(point.keywords)}")
        if point.risk:
            body.append(f"[yellow]놓치는 것[/yellow]      {point.risk}")
        console.print(Panel("\n".join(body), title=f"[bold]{i}. {point.title}[/bold]",
                            title_align="left", border_style="white"))

    numbers = tuple(str(i) for i in range(1, len(options) + 1))
    answer = _ask(
        f"어느 점에 집중할까요? ({'/'.join(numbers)}"
        " · r=다시 제안받기 · s=포인트 없이 진행)",
        (*numbers, "r", "s"),
    )

    if answer == "s":
        return FocusChoice()
    if answer == "r":
        console.print("[dim]원하는 방향이 있으면 알려주세요. 없으면 그냥 엔터.[/dim]")
        console.print("[dim]예: 휴대성이나 무게 쪽으로, 초보자용 기능 위주로[/dim]")
        return FocusChoice(retry=True, hint=input("> ").strip())
    return FocusChoice(point=options[int(answer) - 1])


def _show_report(result: PipelineResult) -> None:
    article, draft, seo = result.draft.article, result.draft, result.seo

    quality = Table("항목", "점수", "만점", title="품질 점수", title_justify="left")
    for name, got, full in draft.quality.as_rows():
        color = "green" if got >= full * 0.8 else "yellow" if got >= full * 0.6 else "red"
        quality.add_row(name, f"[{color}]{got}[/{color}]", str(full))
    quality.add_row("[bold]총점[/bold]", f"[bold]{draft.quality.total}[/bold]", "100")

    seo_table = Table("항목", "값", title="SEO 분석", title_justify="left")
    seo_table.add_row("메인 키워드", seo.main_keyword)
    seo_table.add_row("보조 키워드", ", ".join(seo.sub_keywords))
    seo_table.add_row("예상 검색의도", seo.search_intent)
    seo_table.add_row("검색 타입", seo.search_type)
    seo_table.add_row("경쟁도", seo.competition)
    seo_table.add_row("키워드 밀도", ", ".join(f"{k} {v}%" for k, v in draft.seo_score.density.items()))
    seo_table.add_row("SEO 점수", f"{draft.seo_score.total}/100")
    seo_table.add_row("본문 분량", f"{article.char_count():,}자")

    structure = Table("구성", "내용", title="글 구조", title_justify="left")
    for block in article.blocks:
        if block.kind == KIND_HEADING:
            structure.add_row(f"{'  ' * (block.level - 2)}H{block.level}", block.text)
        elif block.kind == KIND_IMAGE and block.image:
            structure.add_row("  이미지", f"{block.slot} ← {block.image.source}")
        elif block.kind == KIND_LINK:
            structure.add_row("  [green]구매링크[/green]", block.text)
        elif block.kind == KIND_CTA:
            thumb = f"썸네일({block.image.source})" if block.image else "[yellow]썸네일 없음[/yellow]"
            structure.add_row("  [green]구매링크[/green]", f"{thumb} + {block.text[:40]}")

    console.print()
    console.print(Panel(f"[bold]{article.title}[/bold]", border_style="green"))
    if result.products and len(result.products) > 1:
        names = "\n".join(f"· {p.title or p.url}" for p in result.products)
        theme = result.roundup.theme if result.roundup else ""
        console.print(Panel(
            (f"[cyan]{theme}[/cyan]\n" if theme else "") + names,
            title="묶어 소개한 상품", title_align="left", border_style="cyan",
        ))
    if result.focus:
        console.print(Panel(f"{result.focus.title}\n[dim]{result.focus.angle}[/dim]",
                            title="집중 포인트", title_align="left", border_style="cyan"))
    console.print(quality)
    console.print(seo_table)
    console.print(structure)

    if draft.quality.feedback:
        console.print(Panel("\n".join(f"· {f}" for f in draft.quality.feedback),
                            title="남은 개선 여지", border_style="yellow"))


def _ask(prompt: str, valid: tuple[str, ...]) -> str:
    while True:
        console.print(prompt)
        answer = input("> ").strip().lower()
        if answer in valid:
            return answer
        console.print(f"[yellow]{' / '.join(valid)} 중에서 입력해주세요.[/yellow]")


def explain(exc: Exception) -> str:
    text = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return "사내망 SSL 검사 문제입니다. pip install truststore 를 실행하세요."
    if "Max retries" in text:
        return f"네트워크 연결 실패. 사내 프록시가 필요하면 .env 의 HTTPS_PROXY 를 채우세요.\n{text[:200]}"
    return text[:500]


if __name__ == "__main__":
    sys.exit(main())
