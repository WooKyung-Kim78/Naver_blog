"""전체 생성 파이프라인.

  1. 상품 페이지 수집 (텍스트 + 이미지 URL)
  2. 상세 이미지 내려받기 / 품질평가 / 중복제거
  3. 상품 분석 -> ProductBrief
  4. 상세페이지를 눈으로 확인하고 집중 포인트 3안 제시 -> 사용자가 선택
  5. SEO 키워드 전략 -> SeoPlan
  6. 11단 구조 집필 -> Article
  7. 휴머나이징 (계측 -> 재작성)
  8. 품질 평가. 기준 미달이면 지적 사항만 수정
  9. 이미지 슬롯 배치
 10. HTML + 네이버 블록으로 렌더링

이미지를 분석보다 먼저 내려받는 이유는, 상품 상세페이지의 설명 대부분이 이미지 안에
글자로 박혀 있어서 그걸 봐야 집중 포인트를 제대로 고를 수 있기 때문이다.

진행 상황은 report 콜백으로, 선택은 choose 콜백으로 CLI 에 넘긴다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import config
from ai import analyst, critic, focus as focus_mod, humanizer, reviser, seo as seo_mod, writer
from ai.client import MyGenAssistClient
from core.models import (
    KIND_CTA,
    KIND_IMAGE,
    KIND_LINK,
    Article,
    Draft,
    FocusPoint,
    ImageAsset,
    Product,
    ProductBrief,
    SeoPlan,
)
from media.evaluator import ImageProcessor
from media.generator import ImageGenerator
from media.planner import ImagePlanner
from media.stock import StockImageFetcher
from render import html as html_render
from render import naver_blocks
from scrape import product as product_scraper

Reporter = Callable[[str], None]
Chooser = Callable[[list[FocusPoint]], FocusPoint]


@dataclass
class PipelineResult:
    product: Product
    brief: ProductBrief
    focus: FocusPoint | None
    focus_options: list[FocusPoint]
    seo: SeoPlan
    draft: Draft
    images: dict[str, ImageAsset]
    ops: list[naver_blocks.Op]
    run_dir: Path
    attempts: list[Draft]


class Pipeline:
    def __init__(self, *, report: Reporter | None = None, choose: Chooser | None = None):
        self.report = report or (lambda _: None)
        # 콜백이 없으면 첫 번째 안을 자동 선택한다 (비대화형 실행용).
        self.choose = choose or (lambda options: options[0])
        self.ai_cfg = config.load_ai_config()
        self.img_cfg = config.load_image_config()
        self.gen_cfg = config.load_imagegen_config()
        self.post_cfg = config.load_post_config()
        self.quality_cfg = config.load_quality_config()
        self.client = MyGenAssistClient(self.ai_cfg)
        self._processor = ImageProcessor(verify_ssl=self.ai_cfg.verify_ssl, proxies=self.ai_cfg.proxies)

    # ------------------------------------------------------------------ 실행

    def run(self, url: str, *, manual_desc: str = "", collect_images: bool = True) -> PipelineResult:
        product = self._collect_product(url, manual_desc, collect_images)

        # 이미지를 먼저 내려받아야 해서 제목이 정해지기 전에 폴더를 만든다.
        # 완성된 제목은 나중에 알게 되므로 그때 폴더 이름을 고쳐 단다.
        stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
        run_dir = config.OUTPUT_DIR / f"{stamp}_{_slug(product.title)}"
        available = self._prepare_images(product, run_dir)

        self.report("상품 분석 중 (특징·타깃·시나리오·장단점·FAQ)")
        brief = analyst.analyze(self.client, product)
        self.report(f"  카테고리: {brief.category} / 특징 {len(brief.features)}개 / FAQ {len(brief.faqs)}개")

        focus, options = self._pick_focus(product, brief, available)

        self.report("SEO 키워드 전략 수립 중 (웹 검색 포함)")
        seo = seo_mod.plan(self.client, product, brief, focus=focus)
        self.report(f"  메인 키워드: {seo.main_keyword} ({seo.search_type}, 경쟁도 {seo.competition})")

        draft, attempts = self._write_until_good(product, brief, seo, focus)

        images = self._place_images(product, brief, seo, draft.article, run_dir, available)
        run_dir = _rename_run_dir(run_dir, config.OUTPUT_DIR / f"{stamp}_{_slug(draft.article.title)}",
                                  draft.article, images)

        ops = naver_blocks.render(draft.article)
        self._save(run_dir, product, brief, focus, options, seo, draft, attempts)

        return PipelineResult(
            product, brief, focus, options, seo, draft, images, ops, run_dir, attempts
        )

    # -------------------------------------------------------------- 각 단계

    def _collect_product(self, url: str, manual_desc: str, collect_images: bool) -> Product:
        self.report(f"상품 페이지 수집 중: {url}")
        product = product_scraper.fetch(url, verify_ssl=self.ai_cfg.verify_ssl, proxies=self.ai_cfg.proxies)

        if manual_desc:
            product.body_text = f"{manual_desc}\n\n{product.body_text}"

        if collect_images:
            self.report("상세페이지 이미지 탐색 중 (스크롤하며 지연 로딩 유도)")
            from media import product_images

            thumbs, details = product_images.collect(url)
            seen = set(product.thumbnail_urls)
            product.thumbnail_urls += [a.url for a in thumbs if a.url not in seen]
            product.detail_image_urls = [a.url for a in details]
            self.report(f"  썸네일 {len(product.thumbnail_urls)}개 / 상세 이미지 {len(product.detail_image_urls)}개")

        self.report(f"  본문 {len(product.body_text):,}자 / 스펙 {len(product.specs)}항목")
        return product

    def _prepare_images(self, product: Product, run_dir: Path) -> list[ImageAsset]:
        """상품 이미지를 미리 내려받아 평가·중복제거까지 마친다.

        집중 포인트를 고를 때 AI 에게 상세 이미지를 보여줘야 해서 집필보다 먼저 돈다.
        """
        candidates = [ImageAsset(source="thumbnail", url=u) for u in product.thumbnail_urls[:6]]
        candidates += [ImageAsset(source="detail", url=u) for u in product.detail_image_urls[:12]]
        if not candidates:
            return []

        self.report(f"상품 이미지 {len(candidates)}개 내려받아 평가 중")
        available = self._processor.prepare(candidates, run_dir / "images" / "product", referer=product.url)

        by_source: dict[str, int] = {}
        for asset in available:
            by_source[asset.source] = by_source.get(asset.source, 0) + 1
        self.report(f"  사용 가능 {len(available)}개 (중복 제거 후) {by_source}")
        return available

    def _pick_focus(
        self, product: Product, brief: ProductBrief, available: list[ImageAsset]
    ) -> tuple[FocusPoint | None, list[FocusPoint]]:
        # 상세 이미지를 점수 순으로 몇 장만 보여준다. 비전 호출은 장당 비용이 크다.
        details = sorted(
            (a for a in available if a.source == "detail" and a.path),
            key=lambda a: a.score,
            reverse=True,
        )
        paths = [a.path for a in details[: focus_mod.MAX_VISION_IMAGES]]

        if paths:
            self.report(f"상세페이지 이미지 {len(paths)}장을 AI 가 직접 읽는 중")
        else:
            self.report("상세 이미지가 없어 본문 텍스트만으로 집중 포인트를 뽑습니다")

        try:
            options = focus_mod.propose(self.client, product, brief, paths)
        except Exception as exc:
            self.report(f"  집중 포인트 제안 실패({exc}). 포인트 없이 진행합니다.")
            return None, []

        chosen = self.choose(options)
        self.report(f"  선택된 집중 포인트: {chosen.title}")
        return chosen, options

    def _write_until_good(
        self, product: Product, brief: ProductBrief, seo: SeoPlan, focus: FocusPoint | None
    ) -> tuple[Draft, list[Draft]]:
        attempts: list[Draft] = []
        best: Draft | None = None
        article: Article | None = None

        for attempt in range(1, self.quality_cfg.max_attempts + 1):
            if article is None:
                self.report(f"원고 집필 중 (시도 {attempt}/{self.quality_cfg.max_attempts})")
                article = writer.write(
                    self.client, product, brief, seo,
                    self.post_cfg.persona, self.post_cfg.disclosure,
                    focus=focus,
                )
            else:
                # 백지에서 다시 쓰면 잘 쓴 부분까지 날아간다. 지적된 곳만 고친다.
                self.report(f"지적 사항 수정 중 (시도 {attempt}/{self.quality_cfg.max_attempts})")
                article = reviser.revise(
                    self.client, article, seo, best.quality, best.seo_score, self.post_cfg.persona,
                )

            report = humanizer.measure(article.body_text())
            if self.quality_cfg.humanize:
                self.report("  AI 문체 계측 후 자연스럽게 다듬는 중")
                article, report = humanizer.humanize(self.client, article, self.post_cfg.persona, seo)

            seo_score = seo_mod.score(article, seo)
            self.report("  품질 평가 중")
            quality = critic.evaluate(self.client, article, brief, seo, seo_score)

            draft = Draft(article=article, quality=quality, seo_score=seo_score,
                          humanness=report, attempt=attempt)
            attempts.append(draft)
            self.report(f"  총점 {quality.total}/100 (SEO {seo_score.total}, 본문 {article.char_count():,}자)")

            if best is None or quality.total > best.quality.total:
                best = draft
            else:
                # 수정이 오히려 나빠졌으면 그 결과를 버리고 최고 원고에서 다시 시도한다.
                self.report(f"  직전보다 낮아 이전 원고({best.quality.total}점)를 유지합니다.")
                article = best.article

            if quality.total >= self.quality_cfg.pass_mark:
                break
            if attempt < self.quality_cfg.max_attempts:
                self.report(f"  기준 {self.quality_cfg.pass_mark}점 미달. 지적 사항을 고쳐 다시 평가합니다.")

        assert best is not None
        return best, attempts

    def _place_images(
        self,
        product: Product,
        brief: ProductBrief,
        seo: SeoPlan,
        article: Article,
        run_dir: Path,
        available: list[ImageAsset],
    ) -> dict[str, ImageAsset]:
        slots = article.image_slots()
        if not slots:
            return {}

        self.report(f"이미지 배치 중 (슬롯 {len(slots)}개)")
        image_dir = run_dir / "images"
        processor = self._processor

        stock = None
        try:
            self.img_cfg.validate()
            stock = StockImageFetcher(self.img_cfg)
        except config.ConfigError:
            self.report("  스톡 이미지 키가 없어 건너뜁니다.")

        generator = ImageGenerator(self.gen_cfg)
        if not generator.enabled:
            self.report("  AI 이미지 생성은 비활성 상태입니다 (.env 의 IMAGE_GEN_* 미설정).")

        planner = ImagePlanner(stock=stock, generator=generator, processor=processor, image_dir=image_dir)
        placed = planner.assign(slots, available, brief, stock_query=_stock_query(brief, seo))

        for block in article.blocks:
            if block.kind in (KIND_IMAGE, KIND_CTA) and block.slot in placed:
                block.image = placed[block.slot]
            if block.kind in (KIND_CTA, KIND_LINK) and not block.href:
                block.href = product.url

        # 이미지를 못 채운 슬롯은 빈 자리로 남기지 않고 지운다.
        article.blocks = [
            b for b in article.blocks if not (b.kind == KIND_IMAGE and b.image is None)
        ]

        for slot, asset in placed.items():
            self.report(f"  {slot:<12} <- {asset.source} ({asset.width}x{asset.height}, 점수 {asset.score})")

        return placed

    def _save(
        self, run_dir: Path, product, brief, focus, options, seo, draft: Draft, attempts: list[Draft]
    ) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)

        (run_dir / "article.html").write_text(
            html_render.render(draft.article, seo), encoding="utf-8"
        )
        (run_dir / "naver.txt").write_text(
            "\n\n".join(f"[{op.kind}] {op.value}" for op in naver_blocks.render(draft.article)),
            encoding="utf-8",
        )
        (run_dir / "report.json").write_text(
            json.dumps(
                {
                    "product": {"url": product.url, "title": product.title, "price": product.price},
                    "brief": {
                        "category": brief.category,
                        "features": [f.__dict__ for f in brief.features],
                        "personas": [p.__dict__ for p in brief.personas],
                        "scenarios": [s.__dict__ for s in brief.scenarios],
                        "pros": brief.pros,
                        "cons": brief.cons,
                        "faqs": [f.__dict__ for f in brief.faqs],
                    },
                    "focus": focus.__dict__ if focus else None,
                    "focus_options": [f.__dict__ for f in options],
                    "seo_plan": seo.__dict__,
                    "best": draft.to_dict(),
                    "attempts": [d.to_dict() for d in attempts],
                    "humanness": draft.humanness.__dict__,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )


def _rename_run_dir(
    current: Path, target: Path, article: Article, images: dict[str, ImageAsset]
) -> Path:
    """완성된 제목으로 폴더 이름을 바꾸고, 이미 잡혀 있는 이미지 경로를 따라 옮긴다."""
    if current == target or not current.exists() or target.exists():
        return current

    current.rename(target)
    for asset in [*images.values(), *(b.image for b in article.blocks if b.image)]:
        if asset.path:
            try:
                asset.path = target / asset.path.relative_to(current)
            except ValueError:
                pass
    return target


def _stock_query(brief: ProductBrief, seo: SeoPlan) -> str:
    """스톡 사진 검색은 영어가 결과가 좋다. 카테고리 위주로 단순하게 만든다."""
    return brief.category or seo.main_keyword


def _slug(text: str) -> str:
    import re

    return re.sub(r"[^\w가-힣]+", "_", text).strip("_")[:40] or "post"
