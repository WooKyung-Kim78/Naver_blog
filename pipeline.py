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
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import config
from ai import analyst, critic, focus as focus_mod, humanizer, reviser, roundup as roundup_mod, seo as seo_mod, writer
from ai.client import MyGenAssistClient
from core.models import (
    KIND_CTA,
    KIND_IMAGE,
    KIND_LINK,
    SLOT_INFOGRAPHIC,
    Article,
    Draft,
    FocusChoice,
    FocusPoint,
    ImageAsset,
    Product,
    ProductBrief,
    RoundupBrief,
    SeoPlan,
)
from media import infographic
from media.evaluator import ImageProcessor
from media.generator import ImageGenerator
from media.planner import ImagePlanner
from media.stock import StockImageFetcher
from render import html as html_render
from render import naver_blocks
from core import shopping_connect
from core.article_io import save_article
from publish.review import SUGGESTIONS_FILE, SUGGESTIONS_TEMPLATE
from scrape import product as product_scraper

Reporter = Callable[[str], None]
Chooser = Callable[[list[FocusPoint]], FocusChoice]
#: 수집이 막혔을 때 브라우저 창을 띄우고 사용자가 직접 통과시키게 하는 콜백.
#: 주소를 받아 안내를 띄우고, 사용자가 준비될 때까지 막아 둔 뒤 진행 여부를 돌려준다.
Handoff = Callable[[str], bool]

#: 재제안을 무한정 돌면 토큰만 태운다. 이 횟수를 넘기면 포인트 없이 진행한다.
MAX_FOCUS_ROUNDS = 4

#: 네이버 차단은 대개 잠깐이다. 몇 번 쉬었다 다시 해보고 나서 포기한다.
SCRAPE_ROUNDS = 3
SCRAPE_COOLDOWN = 45

#: 한 글에 묶을 수 있는 상품 수. 다섯 개부터는 글이 쇼핑 목록이 된다.
MAX_PRODUCTS = 4


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
    products: list[Product] | None = None
    roundup: RoundupBrief | None = None


class Pipeline:
    def __init__(
        self,
        *,
        report: Reporter | None = None,
        choose: Chooser | None = None,
        handoff: Handoff | None = None,
        handoff_on_block: bool = False,
    ):
        self.report = report or (lambda _: None)
        # 콜백이 없으면 첫 번째 안을 자동 선택한다 (비대화형 실행용).
        self.choose = choose or (lambda options: FocusChoice(point=options[0]))
        # 없으면 수집이 막혔을 때 그냥 포기한다 (비대화형 실행용).
        self.handoff = handoff
        # 수집이 막힌 단계에서도 곧바로 창을 띄울지(--browser). 꺼져 있으면 쉬었다
        # 다시 긁어보고 포기한다. 상세 이미지가 한 장도 없을 때는 이 값과 무관하게
        # 창을 띄운다.
        self.handoff_on_block = handoff_on_block
        self.ai_cfg = config.load_ai_config()
        self.img_cfg = config.load_image_config()
        self.gen_cfg = config.load_imagegen_config()
        self.post_cfg = config.load_post_config()
        self.quality_cfg = config.load_quality_config()
        self.client = MyGenAssistClient(self.ai_cfg)
        self._processor = ImageProcessor(verify_ssl=self.ai_cfg.verify_ssl, proxies=self.ai_cfg.proxies)

    # ------------------------------------------------------------------ 실행

    def run(
        self,
        url: str,
        *,
        page_url: str = "",
        manual_desc: str = "",
        collect_images: bool = True,
    ) -> PipelineResult:
        """url 은 본문에 넣을 구매 링크, page_url 은 내용을 긁어올 실제 상품 페이지다.

        브랜드 커넥트 제휴 링크는 중간 페이지를 거치느라 상품 내용이 안 잡히는 경우가
        있어서 둘을 나눠 받는다. page_url 을 비우면 구매 링크를 그대로 긁는다.
        """
        product = self._collect_product(url, page_url or url, manual_desc, collect_images)

        # 이미지를 먼저 내려받아야 해서 제목이 정해지기 전에 폴더를 만든다.
        # 완성된 제목은 나중에 알게 되므로 그때 폴더 이름을 고쳐 단다.
        stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
        run_dir = config.OUTPUT_DIR / f"{stamp}_{_slug(product.title)}"
        available = self._prepare_images(product, run_dir)

        self.report("상품 분석 중 (특징·타깃·시나리오·장단점·추천대상)")
        brief = analyst.analyze(self.client, product)
        self.report(
            f"  카테고리: {brief.category} / 특징 {len(brief.features)}개 / "
            f"추천대상 {len(brief.recommended_for)}개"
        )

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
            product, brief, focus, options, seo, draft, images, ops, run_dir, attempts,
            products=[product],
        )

    def run_roundup(
        self,
        items: list[tuple[str, str, str]],
        *,
        collect_images: bool = True,
    ) -> PipelineResult:
        """여러 상품을 모아 공통점을 찾고 한 글로 쓴다.

        items 는 (구매 링크, 판매 페이지, 수동 설명) 튜플. 개수는 2~4.
        """
        if not 2 <= len(items) <= MAX_PRODUCTS:
            raise RuntimeError(f"묶어 쓸 상품은 2~{MAX_PRODUCTS}개입니다. 지금은 {len(items)}개입니다.")

        products: list[Product] = []
        for i, (buy_url, page_url, desc) in enumerate(items, 1):
            self.report(f"[{i}/{len(items)}] 상품 수집")
            products.append(self._collect_product(buy_url, page_url or buy_url, desc, collect_images))

        stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
        run_dir = config.OUTPUT_DIR / f"{stamp}_{_slug(products[0].title)}_외{len(products)-1}"

        available: list[ImageAsset] = []
        for i, product in enumerate(products):
            batch = self._prepare_images(product, run_dir, owner=str(i))
            available.extend(batch)

        briefs: list[ProductBrief] = []
        for i, product in enumerate(products):
            self.report(f"[{i+1}/{len(products)}] 상품 분석: {product.title or product.url}")
            briefs.append(analyst.analyze(self.client, product))

        self.report("조합의 공통점과 같이 쓰는 이유를 정리하는 중")
        combo = roundup_mod.analyze(self.client, products, briefs)
        self.report(f"  테마: {combo.theme or '(없음)'} / 공통점 {len(combo.commonalities)}개")

        focus, options = self._pick_roundup_focus(products, briefs, combo, available)

        self.report("SEO 키워드 전략 수립 중 (웹 검색 포함)")
        lead_brief = briefs[0]
        if combo.theme:
            lead_brief.category = combo.theme
            lead_brief.one_liner = combo.one_liner or lead_brief.one_liner
        seo = seo_mod.plan(
            self.client,
            products[0],
            lead_brief,
            focus=focus,
            extra_titles=[p.title for p in products[1:]],
            theme=combo.theme,
        )
        self.report(f"  메인 키워드: {seo.main_keyword} ({seo.search_type}, 경쟁도 {seo.competition})")

        draft, attempts = self._write_roundup_until_good(products, briefs, combo, seo, focus)

        images = self._place_roundup_images(
            products, lead_brief, combo, seo, draft.article, run_dir, available
        )
        run_dir = _rename_run_dir(
            run_dir, config.OUTPUT_DIR / f"{stamp}_{_slug(draft.article.title)}",
            draft.article, images,
        )

        ops = naver_blocks.render(draft.article)
        self._save(run_dir, products[0], lead_brief, focus, options, seo, draft, attempts,
                   products=products, combo=combo)

        return PipelineResult(
            products[0], lead_brief, focus, options, seo, draft, images, ops, run_dir, attempts,
            products=products, roundup=combo,
        )

    # -------------------------------------------------------------- 각 단계

    def _collect_product(
        self, buy_url: str, page_url: str, manual_desc: str, collect_images: bool
    ) -> Product:
        product, page_url, by_hand = self._scrape_with_fallback(buy_url, page_url, collect_images)

        # 긁는 주소와 본문에 넣을 구매 링크는 다를 수 있다. 링크는 제휴 추적이 붙은
        # 쪽을 써야 하므로 여기서 갈아 끼운다.
        product.page_url = page_url
        product.url = buy_url

        if buy_url != page_url:
            self.report(f"  본문 구매 링크: {buy_url}")
        self.report(f"  상품명: {product.title or '(못 찾음)'}")

        # 수동 브라우저로 받아온 경우 그 창에서 이미지까지 이미 긁었다. 다시 열면
        # 사용자가 통과시킨 화면을 잃고 또 막힌다.
        if collect_images and not product.detail_image_urls:
            self.report("상세페이지 이미지 탐색 중 (스크롤하며 지연 로딩 유도)")
            from media import product_images

            thumbs, details = product_images.collect(page_url)
            seen = set(product.thumbnail_urls)
            product.thumbnail_urls += [a.url for a in thumbs if a.url not in seen]
            product.detail_image_urls = [a.url for a in details]
            self.report(f"  썸네일 {len(product.thumbnail_urls)}개 / 상세 이미지 {len(product.detail_image_urls)}개")

        if collect_images and not product.detail_image_urls and not by_hand:
            self._collect_images_by_hand(product)

        if manual_desc:
            product.body_text = f"{manual_desc}\n\n{product.body_text}"

        self.report(f"  본문 {len(product.body_text):,}자 / 스펙 {len(product.specs)}항목")
        if not manual_desc:
            _require_product_signal(product)
        return product

    def _collect_images_by_hand(self, product: Product) -> None:
        """상세 이미지를 한 장도 못 건졌으면 막히지 않았어도 창을 띄운다.

        상세설명 이미지는 끝까지 스크롤하고 '펼쳐보기'를 눌러야 지연 로딩이 도는데,
        headless 브라우저는 그 전에 로그인·캡차로 돌려보내지거나 빈 화면을 받는다.
        상세 이미지가 없으면 상품을 눈으로 확인하지 못한 채 글을 쓰게 되므로
        (집중 포인트도 본문 텍스트만으로 뽑힌다) 여기서는 사람 손을 빌린다.
        """
        page_url = product.page_url or product.url

        if not self.handoff:
            self.report("  상세 이미지가 0개입니다. 창을 넘겨줄 사람이 없어 그냥 진행합니다.")
            return

        from scrape import manual

        self.report("상세 이미지가 0개입니다. 브라우저 창에서 직접 수집합니다.")
        try:
            collected = manual.fetch(
                page_url,
                wait=lambda: self.handoff(page_url),
                collect_images=True,
                report=self.report,
            )
        except Exception as exc:  # noqa: BLE001 - 이미 본문과 썸네일은 확보한 상태다
            # 사용자가 그만뒀거나 창을 띄우지 못했어도 원고는 쓸 수 있다.
            self.report(f"  브라우저 수집을 건너뜁니다: {exc}")
            return

        product_scraper.merge(product, collected)
        product.detail_image_urls = collected.detail_image_urls
        product.resolved_url = collected.resolved_url or product.resolved_url
        self.report(
            f"  썸네일 {len(product.thumbnail_urls)}개 / "
            f"상세 이미지 {len(product.detail_image_urls)}개"
        )

    def _scrape_with_fallback(
        self, buy_url: str, page_url: str, collect_images: bool = True
    ) -> tuple[Product, str, bool]:
        """판매 페이지가 막히면 구매 링크로, 그래도 안 되면 좀 쉬었다 다시 시도한다.

        네이버는 smartstore 직접 주소로 들어오는 자동 접근을 로그인 페이지로 돌려보낸다.
        같은 상품이라도 제휴 링크로 들어가면 통과하는 경우가 많아서 그쪽으로 갈아탄다.
        연달아 긁으면 아예 차단당하는데, 이때는 '상품이 없다'는 얼굴로 오기도 해서
        기다렸다 다시 해보기 전에는 진짜 삭제와 구분할 수 없다.

        마지막 값은 창을 띄워 사람이 통과시킨 화면에서 읽었는지 여부다. 그 경우
        상세 이미지까지 그 창에서 이미 긁었으므로 다시 띄우지 않는다.
        """
        urls = [page_url] + ([buy_url] if buy_url != page_url else [])
        problem = ""

        # 브라우저로 넘길 수 있으면 오래 기다리지 않는다. 사용자가 앞에 앉아 있는데
        # 몇 분씩 재시도를 지켜보게 할 이유가 없다.
        rounds = 1 if self.handoff_on_block else SCRAPE_ROUNDS

        for attempt in range(1, rounds + 1):
            if attempt > 1:
                self.report(f"{SCRAPE_COOLDOWN}초 기다렸다 다시 시도합니다 ({attempt}/{rounds})")
                time.sleep(SCRAPE_COOLDOWN)

            for index, url in enumerate(urls):
                self.report(f"상품 페이지 수집 중: {url}")
                product = product_scraper.fetch(
                    url, verify_ssl=self.ai_cfg.verify_ssl, proxies=self.ai_cfg.proxies
                )
                if product.resolved_url and product.resolved_url != url:
                    self.report(f"  실제 주소: {product.resolved_url}")

                problem = product_scraper.diagnose(product)
                if not problem:
                    return product, url, False

                self.report(f"  [막힘] {problem}")
                if index + 1 < len(urls):
                    self.report("  구매 링크로 다시 시도합니다.")

            # 주소를 다 시도해 보고도 막혔으면 창을 띄워 사용자에게 넘긴다.
            if self.handoff_on_block:
                collected = self._scrape_by_hand(page_url, collect_images=collect_images)
                if collected is not None:
                    return collected, collected.resolved_url or page_url, True

        raise product_scraper.ProductUnavailable(
            f"상품 페이지를 읽지 못했습니다. {problem}.\n"
            f"  시도한 주소: {', '.join(urls)}\n"
            "  네이버가 짧은 시간에 반복 접속을 막습니다. 몇 분 뒤에 다시 실행해 보세요.\n"
            + (
                ""
                if self.handoff_on_block
                else "  --browser 를 붙이면 브라우저 창을 띄워 직접 로그인·인증을 끝낸 뒤\n"
                     "  그 화면에서 상품 정보를 읽어옵니다.\n"
            )
            + "  계속 막히면 --desc 로 상품 설명을 직접 넣어 진행할 수 있습니다."
        )

    def _scrape_by_hand(self, page_url: str, *, collect_images: bool = True) -> Product | None:
        """브라우저 창을 띄워 사용자가 직접 통과시킨 화면에서 읽는다.

        여기서 실패해도 예외로 끝내지 않는다. 호출한 쪽이 원래의 '막혔다' 안내를
        띄우는 편이 사용자에게 더 쓸모 있다. 다만 사용자가 그만두겠다고 한 경우만은
        자동 재시도로 더 붙잡아 두지 않는다.
        """
        from scrape import manual

        self.report("브라우저 창을 띄웁니다. 직접 상품 페이지를 열어주세요.")
        try:
            product = manual.fetch(
                page_url,
                wait=lambda: self.handoff(page_url),
                collect_images=collect_images,
                report=self.report,
            )
        except manual.ManualCancelled as exc:
            raise product_scraper.ProductUnavailable(
                f"{exc}\n"
                f"  주소: {page_url}\n"
                "  --desc 로 상품 설명을 직접 넣어 진행할 수도 있습니다."
            ) from None
        except manual.ManualUnavailable as exc:
            self.report(f"  [수동 수집 실패] {exc}")
            return None

        problem = product_scraper.diagnose(product)
        if problem:
            self.report(f"  [막힘] 창에서 읽은 화면도 쓸 수 없습니다: {problem}")
            return None
        return product

    def _prepare_images(
        self, product: Product, run_dir: Path, *, owner: str = ""
    ) -> list[ImageAsset]:
        """상품 이미지를 미리 내려받아 평가·중복제거까지 마친다.

        집중 포인트를 고를 때 AI 에게 상세 이미지를 보여줘야 해서 집필보다 먼저 돈다.
        """
        candidates = [
            ImageAsset(source="thumbnail", url=u, owner=owner) for u in product.thumbnail_urls[:6]
        ]
        candidates += [
            ImageAsset(source="detail", url=u, owner=owner) for u in product.detail_image_urls[:12]
        ]
        if not candidates:
            return []

        self.report(f"상품 이미지 {len(candidates)}개 내려받아 평가 중")
        # 네이버 이미지 CDN 은 Referer 를 본다. 구매 링크가 아니라 이미지를 찾은
        # 페이지 주소를 보내야 한다.
        referer = product.resolved_url or product.page_url or product.url
        dest = run_dir / "images" / ("product" + (f"_{owner}" if owner else ""))
        available = self._processor.prepare(candidates, dest, referer=referer)

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

        rejected: list[str] = []
        hint = ""
        for round_no in range(1, MAX_FOCUS_ROUNDS + 1):
            if round_no > 1:
                self.report(f"집중 포인트를 다시 뽑는 중 ({round_no}/{MAX_FOCUS_ROUNDS})")
            try:
                options = focus_mod.propose(
                    self.client, product, brief, paths, avoid=rejected, hint=hint
                )
            except Exception as exc:
                self.report(f"  집중 포인트 제안 실패({exc}). 포인트 없이 진행합니다.")
                return None, []

            choice = self.choose(options)
            if not choice.retry:
                if choice.point:
                    self.report(f"  선택된 집중 포인트: {choice.point.title}")
                else:
                    self.report("  집중 포인트 없이 진행합니다.")
                return choice.point, options

            rejected += [o.title for o in options]
            hint = choice.hint or hint

        self.report("  재제안 횟수를 다 썼습니다. 집중 포인트 없이 진행합니다.")
        return None, []

    def _pick_roundup_focus(
        self,
        products: list[Product],
        briefs: list[ProductBrief],
        combo: RoundupBrief,
        available: list[ImageAsset],
    ) -> tuple[FocusPoint | None, list[FocusPoint]]:
        # 상품마다 점수 높은 상세 이미지를 하나씩만 보여 토큰을 아낀다.
        paths: list[Path] = []
        owners = {a.owner for a in available}
        for owner in sorted(owners, key=lambda x: int(x) if str(x).isdigit() else 99):
            details = sorted(
                (a for a in available if a.owner == owner and a.source == "detail" and a.path),
                key=lambda a: a.score,
                reverse=True,
            )
            if details:
                paths.append(details[0].path)
            if len(paths) >= roundup_mod.MAX_VISION_IMAGES:
                break

        if paths:
            self.report(f"상품 이미지 {len(paths)}장을 AI 가 직접 읽는 중")
        else:
            self.report("상세 이미지가 없어 텍스트만으로 조합의 축을 뽑습니다")

        rejected: list[str] = []
        hint = ""
        for round_no in range(1, MAX_FOCUS_ROUNDS + 1):
            if round_no > 1:
                self.report(f"조합의 축을 다시 뽑는 중 ({round_no}/{MAX_FOCUS_ROUNDS})")
            try:
                options = roundup_mod.propose_themes(
                    self.client, products, briefs, combo, paths, avoid=rejected, hint=hint
                )
            except Exception as exc:
                self.report(f"  집중 포인트 제안 실패({exc}). 테마만으로 진행합니다.")
                return None, []

            choice = self.choose(options)
            if not choice.retry:
                if choice.point:
                    self.report(f"  선택된 공통점: {choice.point.title}")
                else:
                    self.report("  집중 포인트 없이 진행합니다.")
                return choice.point, options
            rejected += [o.title for o in options]
            hint = choice.hint or hint

        self.report("  재제안 횟수를 다 썼습니다. 테마만으로 진행합니다.")
        return None, []

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

    def _write_roundup_until_good(
        self,
        products: list[Product],
        briefs: list[ProductBrief],
        combo: RoundupBrief,
        seo: SeoPlan,
        focus: FocusPoint | None,
    ) -> tuple[Draft, list[Draft]]:
        attempts: list[Draft] = []
        best: Draft | None = None
        article: Article | None = None
        lead = briefs[0]

        for attempt in range(1, self.quality_cfg.max_attempts + 1):
            if article is None:
                self.report(f"묶음 원고 집필 중 (시도 {attempt}/{self.quality_cfg.max_attempts})")
                article = roundup_mod.write(
                    self.client, products, briefs, combo, seo,
                    self.post_cfg.persona, self.post_cfg.disclosure, focus=focus,
                )
            else:
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
            quality = critic.evaluate(self.client, article, lead, seo, seo_score)
            draft = Draft(article=article, quality=quality, seo_score=seo_score,
                          humanness=report, attempt=attempt)
            attempts.append(draft)
            self.report(f"  총점 {quality.total}/100 (SEO {seo_score.total}, 본문 {article.char_count():,}자)")

            if best is None or quality.total > best.quality.total:
                best = draft
            else:
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
        placed = planner.assign(
            [s for s in slots if s != SLOT_INFOGRAPHIC], available, brief, stock_query=_stock_query(brief, seo)
        )
        self._draw_infographic(infographic.from_brief(product, brief, article), image_dir, placed)

        for block in article.blocks:
            if block.kind in (KIND_IMAGE, KIND_CTA) and block.slot in placed:
                block.image = placed[block.slot]
            if block.kind in (KIND_CTA, KIND_LINK) and not block.href:
                block.href = product.url
        shopping_connect.attach(article, [product], self.post_cfg.creator_space_id)

        # 이미지를 못 채운 슬롯은 빈 자리로 남기지 않고 지운다.
        article.blocks = [
            b for b in article.blocks if not (b.kind == KIND_IMAGE and b.image is None)
        ]

        for slot, asset in placed.items():
            self.report(f"  {slot:<12} <- {asset.source} ({asset.width}x{asset.height}, 점수 {asset.score})")

        return placed

    def _draw_infographic(
        self, data: infographic.InfographicData, image_dir: Path, placed: dict[str, ImageAsset]
    ) -> None:
        """글 전체 요약 그림을 그려 인포그래픽 슬롯에 꽂는다.

        이 자리만큼은 사진을 고르게 두지 않는다. '한눈에 보기' 아래에 상세페이지
        사진이 들어가면 제목과 그림이 따로 놀기 때문이다. 못 그리면 슬롯을 비우고,
        비면 뒤에서 그 이미지 블록 자체가 지워진다.
        """
        try:
            asset = infographic.render(data, image_dir)
        except Exception as exc:  # noqa: BLE001 - 그림 하나 때문에 원고를 잃을 수는 없다
            self.report(f"  인포그래픽을 그리지 못했습니다: {exc}")
            return
        if not asset:
            self.report("  인포그래픽에 넣을 내용이 없어 건너뜁니다.")
            return
        asset.slot = SLOT_INFOGRAPHIC
        placed[SLOT_INFOGRAPHIC] = asset

    def _place_roundup_images(
        self,
        products: list[Product],
        brief: ProductBrief,
        combo: RoundupBrief,
        seo: SeoPlan,
        article: Article,
        run_dir: Path,
        available: list[ImageAsset],
    ) -> dict[str, ImageAsset]:
        """상품별 슬롯(item0, cta1 ...)에는 그 상품 이미지를 우선 넣는다."""
        slots = article.image_slots()
        if not slots:
            return {}

        self.report(f"이미지 배치 중 (슬롯 {len(slots)}개, 상품 {len(products)}개)")
        image_dir = run_dir / "images"

        stock = None
        try:
            self.img_cfg.validate()
            stock = StockImageFetcher(self.img_cfg)
        except config.ConfigError:
            self.report("  스톡 이미지 키가 없어 건너뜁니다.")

        planner = ImagePlanner(
            stock=stock, generator=ImageGenerator(self.gen_cfg),
            processor=self._processor, image_dir=image_dir,
        )

        used: set[int] = set()
        placed: dict[str, ImageAsset] = {}

        def take(slot: str, pool: list[ImageAsset], prefer: tuple[str, ...]) -> ImageAsset | None:
            for source in prefer:
                candidates = [
                    a for a in pool
                    if a.source == source and a.path and (a.phash is None or a.phash not in used)
                ]
                if candidates:
                    return max(candidates, key=lambda a: a.score)
            return None

        shared = [s for s in slots if not s.startswith(("item", "cta")) and s != SLOT_INFOGRAPHIC]
        owned = [s for s in slots if s.startswith(("item", "cta"))]

        for slot in owned:
            owner = slot[4:] if slot.startswith("item") else slot[3:]
            pool = [a for a in available if a.owner == owner]
            asset = take(slot, pool, ("thumbnail", "detail"))
            if asset:
                asset.slot = slot
                if asset.phash is not None:
                    used.add(asset.phash)
                placed[slot] = asset

        leftovers = [a for a in available if a.phash is None or a.phash not in used]
        shared_placed = planner.assign(shared, leftovers, brief, stock_query=_stock_query(brief, seo))
        placed.update(shared_placed)
        self._draw_infographic(infographic.from_roundup(combo, article), image_dir, placed)

        for block in article.blocks:
            if block.kind in (KIND_IMAGE, KIND_CTA) and block.slot in placed:
                block.image = placed[block.slot]
        shopping_connect.attach(article, products, self.post_cfg.creator_space_id)

        article.blocks = [
            b for b in article.blocks if not (b.kind == KIND_IMAGE and b.image is None)
        ]

        for slot, asset in placed.items():
            self.report(f"  {slot:<12} <- {asset.source} ({asset.width}x{asset.height}, 점수 {asset.score})")
        return placed

    def _save(
        self,
        run_dir: Path,
        product,
        brief,
        focus,
        options,
        seo,
        draft: Draft,
        attempts: list[Draft],
        *,
        products: list[Product] | None = None,
        combo: RoundupBrief | None = None,
    ) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)

        (run_dir / "article.html").write_text(
            html_render.render(draft.article, seo), encoding="utf-8"
        )
        save_article(run_dir, draft.article)
        suggestions = run_dir / SUGGESTIONS_FILE
        if not suggestions.exists():
            suggestions.write_text(SUGGESTIONS_TEMPLATE, encoding="utf-8")
        ops = naver_blocks.render(draft.article)
        (run_dir / "naver.txt").write_text(naver_blocks.dump(ops), encoding="utf-8")
        # 업로드만 다시 할 때 원고 생성을 건너뛰기 위한 묶음.
        (run_dir / "upload.json").write_text(
            json.dumps(
                {
                    "title": draft.article.title,
                    "tags": draft.article.tags,
                    "ops": [
                        {"kind": op.kind, "value": op.value, **({"query": op.query} if op.query else {})}
                        for op in ops
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (run_dir / "report.json").write_text(
            json.dumps(
                {
                    "product": {
                        "buy_url": product.url,
                        "page_url": product.page_url,
                        "resolved_url": product.resolved_url,
                        "title": product.title,
                        "price": product.price,
                    },
                    "products": [
                        {"buy_url": p.url, "page_url": p.page_url, "title": p.title, "price": p.price}
                        for p in (products or [product])
                    ],
                    "roundup": dict(combo.__dict__) if combo else None,
                    "brief": {
                        "category": brief.category,
                        "features": [f.__dict__ for f in brief.features],
                        "personas": [p.__dict__ for p in brief.personas],
                        "scenarios": [s.__dict__ for s in brief.scenarios],
                        "pros": brief.pros,
                        "cons": brief.cons,
                        "recommended_for": brief.recommended_for,
                        "not_recommended_for": brief.not_recommended_for,
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


#: 상품 페이지라면 이 중 최소 몇 개는 잡혀야 한다. 죄다 비어 있는데도 진행하면
#: 메뉴와 푸터만 읽고 있지도 않은 상품의 글을 지어내게 된다.
MIN_PRODUCT_SIGNALS = 2


def _require_product_signal(product: Product) -> None:
    signals = {
        "가격": bool(product.price),
        "상품명": bool(product.title) and len(product.title) >= 6,
        "상품 이미지": (len(product.thumbnail_urls) + len(product.detail_image_urls)) >= 1,
        "설명": len(product.body_text) >= 150,
    }
    if sum(signals.values()) >= MIN_PRODUCT_SIGNALS:
        return

    found = ", ".join(k for k, v in signals.items() if v) or "없음"
    missing = ", ".join(k for k, v in signals.items() if not v)
    where = product.resolved_url or product.page_url or product.url
    raise product_scraper.ProductUnavailable(
        "상품 정보를 충분히 읽지 못했습니다. 이대로 진행하면 페이지의 메뉴와 안내문만 보고 "
        "실제와 다른 글을 지어냅니다.\n"
        f"  들어간 주소: {where}\n"
        f"  상품명: {product.title or '(못 찾음)'}\n"
        f"  확보한 것: {found}\n"
        f"  못 찾은 것: {missing}\n"
        "  상품명은 읽혔는데 나머지가 비었다면 네이버가 접속을 제한하는 중일 가능성이 큽니다. "
        "몇 분 뒤에 다시 실행해 보세요.\n"
        "  계속 막히면 --desc 로 상품 설명을 직접 넣어 진행할 수 있습니다."
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
