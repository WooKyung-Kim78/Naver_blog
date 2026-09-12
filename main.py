"""네이버 상품 리뷰 블로그 생성기 CLI.

사용법:
    python main.py doctor                      설정과 API 연결 상태 점검
    python main.py post --url <상품 URL>        생성 후 네이버에 업로드
    python main.py post --url <URL> --dry-run   업로드 없이 원고와 이미지만 생성
"""

from __future__ import annotations

import argparse
import sys
import webbrowser

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import config
from ai.client import AIError, MyGenAssistClient
from core.models import KIND_HEADING, KIND_IMAGE
from pipeline import Pipeline, PipelineResult

console = Console()


def main() -> int:
    parser = argparse.ArgumentParser(description="네이버 상품 리뷰 블로그 생성기")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="설정과 API 연결 점검")

    post = sub.add_parser("post", help="상품 URL 로 리뷰 생성")
    post.add_argument("--url", required=True, help="홍보할 상품 페이지 URL")
    post.add_argument("--desc", default="", help="상품 설명 직접 입력(사이트가 크롤링을 막을 때)")
    post.add_argument("--dry-run", action="store_true", help="네이버에 올리지 않고 원고만 생성")
    post.add_argument("--no-images", action="store_true", help="상세페이지 이미지 수집 건너뛰기(빠름)")
    post.add_argument("--open", action="store_true", help="완성된 HTML 미리보기를 브라우저로 열기")
    post.add_argument("--debug", action="store_true", help="브라우저 스크린샷 저장")

    args = parser.parse_args()

    try:
        return cmd_doctor() if args.command == "doctor" else cmd_post(args)
    except KeyboardInterrupt:
        console.print("\n[yellow]중단되었습니다.[/yellow]")
        return 130
    except config.ConfigError as exc:
        console.print(f"\n[red]설정 오류:[/red] {exc}")
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
    pipeline = Pipeline(report=lambda msg: console.print(f"  {msg}" if msg.startswith(" ") else f"[cyan]›[/cyan] {msg}"))

    desc = args.desc
    result = pipeline.run(args.url, manual_desc=desc, collect_images=not args.no_images)

    _show_report(result)
    console.print(f"\n결과 저장 위치: [white]{result.run_dir}[/white]")
    console.print("  article.html  미리보기 / naver.txt  업로드될 내용 / report.json  분석 데이터")

    if args.open:
        webbrowser.open((result.run_dir / "article.html").as_uri())

    if args.dry_run:
        console.print("\n[yellow]--dry-run 이므로 네이버에는 올리지 않았습니다.[/yellow]")
        return 0

    naver_cfg = config.load_naver_config()
    naver_cfg.validate()
    mode = "임시저장" if naver_cfg.post_mode == "draft" else "즉시 발행"
    if _ask(f"\n네이버 블로그에 {mode} 할까요? (y/n)", ("y", "n")) != "y":
        console.print("중단했습니다. 원고는 저장되어 있습니다.")
        return 0

    from publish.naver_blog import NaverBlogPublisher, PostBlock

    payload = [PostBlock(kind=op.kind, value=op.value) for op in result.ops]
    with NaverBlogPublisher(naver_cfg, debug=args.debug) as publisher:
        console.print("[cyan]›[/cyan] 네이버 로그인 중")
        publisher.ensure_login()
        console.print("[cyan]›[/cyan] 에디터에 내용 입력 중")
        message = publisher.publish(result.draft.article.title, payload, result.draft.article.tags)

    console.print(f"\n[bold green]{message}[/bold green]")
    return 0


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

    console.print()
    console.print(Panel(f"[bold]{article.title}[/bold]", border_style="green"))
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
