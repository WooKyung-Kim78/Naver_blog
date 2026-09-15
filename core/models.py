"""파이프라인 전체가 주고받는 데이터 모델.

이 파일이 모듈 간 계약이다. 분석 -> SEO -> 집필 -> 휴머나이징 -> 평가 -> 이미지 배치 ->
렌더링 순서로 흐르며, 각 단계는 아래 모델만 보고 동작한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------- 상품


@dataclass
class Product:
    """상품 페이지에서 긁어온 원재료."""

    #: 블로그 본문에 넣을 구매 링크. 제휴 추적이 붙어 있으므로 절대 다른 주소로
    #: 바꾸지 않는다. 수익이 여기에 달려 있다.
    url: str
    #: 실제로 크롤링한 상품 페이지. 비어 있으면 url 과 같다.
    page_url: str = ""
    #: page_url 이 리다이렉트를 거쳐 최종 도착한 주소. 진단용이다.
    resolved_url: str = ""
    title: str = ""
    description: str = ""
    price: str = ""
    brand: str = ""
    site_name: str = ""
    body_text: str = ""
    thumbnail_urls: list[str] = field(default_factory=list)
    detail_image_urls: list[str] = field(default_factory=list)
    specs: dict[str, str] = field(default_factory=dict)

    @property
    def image_urls(self) -> list[str]:
        return self.thumbnail_urls + self.detail_image_urls

    def as_prompt_context(self, limit: int = 6000) -> str:
        parts = [f"[상품 페이지 URL] {self.page_url or self.url}"]
        for label, value in (
            ("상품명", self.title),
            ("브랜드", self.brand),
            ("판매처", self.site_name),
            ("가격", self.price),
            ("요약", self.description),
        ):
            if value:
                parts.append(f"[{label}] {value}")
        if self.specs:
            spec_lines = "\n".join(f"  - {k}: {v}" for k, v in list(self.specs.items())[:40])
            parts.append(f"[스펙표]\n{spec_lines}")
        if self.body_text:
            parts.append(f"[페이지 본문]\n{self.body_text[:limit]}")
        return "\n".join(parts)


# ----------------------------------------------------------------- 상품 분석


@dataclass
class Feature:
    name: str
    detail: str
    benefit: str


@dataclass
class Persona:
    who: str
    pain: str
    why_fits: str


@dataclass
class Scenario:
    when: str
    situation: str
    outcome: str


@dataclass
class Concern:
    worry: str
    answer: str


@dataclass
class FAQ:
    question: str
    answer: str


@dataclass
class FocusPoint:
    """상세페이지를 읽고 뽑은 '이 글에서 밀어붙일 한 가지'. 사용자가 셋 중 하나를 고른다."""

    title: str = ""
    angle: str = ""
    evidence: str = ""  # 상세페이지의 어떤 근거에서 나왔는지
    target: str = ""
    why_now: str = ""
    keywords: list[str] = field(default_factory=list)
    risk: str = ""  # 이 각도로 갔을 때의 약점

    def as_prompt(self) -> str:
        return (
            f"[집중 포인트] {self.title}\n"
            f"[접근] {self.angle}\n"
            f"[근거] {self.evidence}\n"
            f"[타깃] {self.target}\n"
            f"[밀어야 하는 이유] {self.why_now}"
        )


@dataclass
class FocusChoice:
    """집중 포인트 선택 결과. 마음에 드는 안이 없으면 retry 로 다시 받는다."""

    point: FocusPoint | None = None
    retry: bool = False
    hint: str = ""  # 다시 받을 때 사용자가 알려준 방향


@dataclass
class RoundupBrief:
    """여러 상품을 한 글로 묶을 때, 공통점과 조합 이유를 정리한 설계도."""

    theme: str = ""
    one_liner: str = ""
    commonalities: list[str] = field(default_factory=list)
    differences: list[str] = field(default_factory=list)
    how_together: str = ""  # 같이 쓰면 어떻게 쓰는지
    recommended_for: list[str] = field(default_factory=list)
    not_recommended_for: list[str] = field(default_factory=list)
    faqs: list[FAQ] = field(default_factory=list)


@dataclass
class ProductBrief:
    """AI 가 상품을 읽고 정리한 집필 설계도."""

    category: str = ""
    one_liner: str = ""
    features: list[Feature] = field(default_factory=list)
    personas: list[Persona] = field(default_factory=list)
    scenarios: list[Scenario] = field(default_factory=list)
    concerns: list[Concern] = field(default_factory=list)
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    faqs: list[FAQ] = field(default_factory=list)
    recommended_for: list[str] = field(default_factory=list)
    not_recommended_for: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------- SEO


@dataclass
class SeoPlan:
    main_keyword: str = ""
    sub_keywords: list[str] = field(default_factory=list)
    lsi_keywords: list[str] = field(default_factory=list)
    search_intent: str = ""
    search_type: str = ""  # 정보형 / 구매형 / 비교형 / 브랜드형
    competition: str = ""  # 낮음 / 중간 / 높음
    h1: str = ""
    h2s: list[str] = field(default_factory=list)
    h3s: dict[str, list[str]] = field(default_factory=dict)
    title_candidates: list[str] = field(default_factory=list)
    meta_description: str = ""


@dataclass
class SeoScore:
    total: int = 0
    density: dict[str, float] = field(default_factory=dict)
    main_count: int = 0
    title_has_main: bool = False
    intro_has_main: bool = False
    heading_coverage: float = 0.0
    char_count: int = 0
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------- 이미지


#: 콘텐츠 위치별 이미지 슬롯. planner 가 이 이름으로 이미지를 배정한다.
SLOT_HERO = "hero"  # 도입부 대표 이미지
SLOT_PRODUCT = "product"  # 상품 소개
SLOT_FEATURE = "feature"  # 주요 특징
SLOT_LIFESTYLE = "lifestyle"  # 실사용 시나리오
SLOT_INFOGRAPHIC = "infographic"  # FAQ / 정보 정리
SLOT_CTA = "cta"  # 총평 및 CTA

#: 이미지 출처. 숫자가 작을수록 우선순위가 높다.
SOURCE_PRIORITY = {"detail": 1, "thumbnail": 2, "stock": 3, "ai": 4}


@dataclass
class ImageAsset:
    source: str  # detail / thumbnail / stock / ai
    url: str = ""
    path: Path | None = None
    width: int = 0
    height: int = 0
    phash: int | None = None
    score: float = 0.0
    caption: str = ""
    credit: str = ""
    slot: str = ""
    prompt: str = ""
    owner: str = ""  # 묶어 쓸 때 어느 상품 이미지인지. "0", "1"...

    @property
    def priority(self) -> int:
        return SOURCE_PRIORITY.get(self.source, 9)


# ------------------------------------------------------------------- 콘텐츠


#: 렌더러가 이해하는 블록 종류.
KIND_HEADING = "heading"
KIND_PARAGRAPH = "paragraph"
KIND_QUOTE = "quote"  # 인용 박스
KIND_CALLOUT = "callout"  # 요약 / 강조 박스
KIND_CHECKLIST = "checklist"
KIND_TABLE = "table"
KIND_FAQ = "faq"
KIND_DIVIDER = "divider"
KIND_IMAGE = "image"
KIND_LINK = "link"  # 본문 중간에 끼워 넣는 구매 링크
KIND_CTA = "cta"  # 글 끝의 썸네일 + 구매 링크


@dataclass
class Block:
    """출력 형식에 중립적인 콘텐츠 조각.

    HTML 렌더러와 네이버 에디터 렌더러가 같은 블록 목록을 서로 다르게 그린다.
    """

    kind: str
    text: str = ""
    level: int = 2  # heading 전용
    style: str = "info"  # callout 전용: info / tip / warn / summary
    items: list[str] = field(default_factory=list)  # checklist 전용
    headers: list[str] = field(default_factory=list)  # table 전용
    rows: list[list[str]] = field(default_factory=list)  # table 전용
    qa: list[FAQ] = field(default_factory=list)  # faq 전용
    image: ImageAsset | None = None  # image / cta 전용
    slot: str = ""  # 이미지 자리표시자
    href: str = ""  # link / cta 전용

    @property
    def plain_text(self) -> str:
        """글자수/키워드 밀도 계산에 쓰는 순수 텍스트."""
        if self.kind == KIND_CHECKLIST:
            return " ".join(self.items)
        if self.kind == KIND_TABLE:
            return " ".join(self.headers + [c for row in self.rows for c in row])
        if self.kind == KIND_FAQ:
            return " ".join(f"{f.question} {f.answer}" for f in self.qa)
        if self.kind in (KIND_IMAGE, KIND_DIVIDER):
            return self.image.caption if self.image else ""
        return self.text


@dataclass
class Article:
    title: str = ""
    blocks: list[Block] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    disclosure: str = ""

    def body_text(self) -> str:
        return "\n".join(b.plain_text for b in self.blocks if b.plain_text)

    def char_count(self) -> int:
        return len(self.body_text().replace(" ", ""))

    def image_slots(self) -> list[str]:
        """이미지를 채워야 할 슬롯. 마지막 CTA 도 썸네일을 달아야 해서 포함한다."""
        return [b.slot for b in self.blocks if b.kind in (KIND_IMAGE, KIND_CTA) and b.slot]


# ------------------------------------------------------------------- 품질


@dataclass
class QualityScore:
    seo: int = 0
    readability: int = 0
    humanness: int = 0
    information: int = 0
    conversion: int = 0
    feedback: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.seo + self.readability + self.humanness + self.information + self.conversion

    def as_rows(self) -> list[tuple[str, int, int]]:
        return [
            ("SEO", self.seo, 20),
            ("가독성", self.readability, 20),
            ("인간다움", self.humanness, 20),
            ("정보밀도", self.information, 20),
            ("구매전환 가능성", self.conversion, 20),
        ]


@dataclass
class HumannessReport:
    """휴머나이저가 참고하는 계측 결과. LLM 에게 고칠 지점을 짚어주는 용도."""

    sentence_count: int = 0
    mean_length: float = 0.0
    stdev_length: float = 0.0
    variation: float = 0.0  # 변동계수. 낮을수록 기계적이다.
    ending_variety: float = 0.0  # 문장 종결 어미의 다양성 0~1
    repeated_phrases: list[str] = field(default_factory=list)
    cliches: list[str] = field(default_factory=list)
    long_sentences: list[str] = field(default_factory=list)

    @property
    def needs_work(self) -> bool:
        return bool(
            self.variation < 0.45
            or self.ending_variety < 0.35
            or self.repeated_phrases
            or self.cliches
            or self.long_sentences
        )

    def as_instructions(self) -> str:
        lines: list[str] = []
        if self.variation < 0.45:
            lines.append(
                f"- 문장 길이가 {self.mean_length:.0f}자 근처로 지나치게 균일하다(변동계수 {self.variation:.2f}). "
                "10자 내외의 짧은 문장과 60자 이상의 긴 문장을 섞어 리듬을 만들어라."
            )
        if self.ending_variety < 0.35:
            lines.append("- 문장 종결 어미가 단조롭다. 평서문, 의문문, 감탄, 말줄임을 섞어라.")
        if self.repeated_phrases:
            lines.append(f"- 다음 표현이 반복된다. 다른 말로 바꿔라: {', '.join(self.repeated_phrases[:8])}")
        if self.cliches:
            lines.append(f"- 다음 AI 상투어를 삭제하거나 구어체로 바꿔라: {', '.join(self.cliches[:8])}")
        if self.long_sentences:
            lines.append(f"- 다음 문장은 너무 길어 읽기 어렵다. 둘로 쪼개라: \"{self.long_sentences[0][:60]}...\"")
        return "\n".join(lines)


@dataclass
class Draft:
    """한 번의 생성 시도 결과 묶음."""

    article: Article
    quality: QualityScore = field(default_factory=QualityScore)
    seo_score: SeoScore = field(default_factory=SeoScore)
    humanness: HumannessReport = field(default_factory=HumannessReport)
    attempt: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "title": self.article.title,
            "char_count": self.article.char_count(),
            "quality_total": self.quality.total,
            "quality": self.quality.__dict__,
            "seo_score": self.seo_score.__dict__,
            "tags": self.article.tags,
        }
