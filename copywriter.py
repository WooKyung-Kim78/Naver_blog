"""AI 로 홍보 컨셉을 제안하고, 선택된 컨셉으로 블로그 본문을 작성한다."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ai_client import MyGenAssistClient
from config import PostConfig
from product import Product

_BASE_RULES = """너는 네이버 블로그 상위 노출 경험이 많은 한국인 콘텐츠 마케터다.
다음 원칙을 반드시 지킨다.

- 네이버 검색 알고리즘(C-Rank, D.I.A.+)은 실제 경험과 구체적인 정보를 높게 평가한다.
  추상적인 미사여구 대신 숫자, 상황, 비교, 사용 맥락을 담는다.
- 의료법/표시광고법 위반 표현(치료, 완치, 최고, 1위, 부작용 없음 등)은 절대 쓰지 않는다.
- "~하실 수 있으십니다" 같은 과잉 존대나 AI 특유의 상투어를 쓰지 않는다.
- 모든 출력은 한국어로 한다.
- 반드시 유효한 JSON 만 출력한다. 코드펜스나 설명 문장을 덧붙이지 않는다."""


@dataclass
class Concept:
    title: str
    angle: str
    hook: str
    target: str
    keywords: list[str] = field(default_factory=list)
    reason: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Concept":
        return cls(
            title=str(d.get("title", "")).strip(),
            angle=str(d.get("angle", "")).strip(),
            hook=str(d.get("hook", "")).strip(),
            target=str(d.get("target", "")).strip(),
            keywords=[str(k) for k in d.get("keywords", [])],
            reason=str(d.get("reason", "")).strip(),
        )


@dataclass
class Section:
    heading: str
    body: str
    image_query: str = ""


@dataclass
class BlogPost:
    title: str
    intro: str
    sections: list[Section]
    outro: str
    tags: list[str]
    disclosure: str

    def to_blocks(self) -> list[tuple[str, str]]:
        """에디터에 순서대로 입력할 (종류, 내용) 목록. 종류는 text 또는 image_query."""
        blocks: list[tuple[str, str]] = [("text", self.disclosure), ("text", self.intro)]
        for section in self.sections:
            if section.image_query:
                blocks.append(("image_query", section.image_query))
            if section.heading:
                blocks.append(("text", f"■ {section.heading}"))
            blocks.append(("text", section.body))
        blocks.append(("text", self.outro))
        return blocks

    def preview(self) -> str:
        lines = [f"제목: {self.title}", "", self.disclosure, "", self.intro, ""]
        for section in self.sections:
            if section.image_query:
                lines.append(f"[이미지: {section.image_query}]")
            lines += [f"■ {section.heading}", section.body, ""]
        lines += [self.outro, "", "태그: " + " ".join(f"#{t}" for t in self.tags)]
        return "\n".join(lines)


def propose_concepts(client: MyGenAssistClient, product: Product, count: int = 5) -> list[Concept]:
    user = f"""아래 상품을 네이버 블로그에서 홍보하려고 한다.
웹 검색으로 이 상품 카테고리의 2026년 현재 국내 소비 트렌드와 사람들이 실제로 검색하는
키워드를 먼저 조사한 뒤, 서로 확실히 다른 접근의 홍보 컨셉 {count}개를 제안해라.

{product.as_prompt_context()}

각 컨셉은 아래 JSON 형식으로 작성한다.

{{
  "concepts": [
    {{
      "title": "실제 블로그 제목안 (32자 이내, 검색 키워드 포함)",
      "angle": "이 글의 접근 방식을 한 문장으로",
      "hook": "첫 두 줄에 쓸 후킹 문장",
      "target": "이 글이 노리는 독자층",
      "keywords": ["네이버 검색 키워드", "3~6개"],
      "reason": "지금 이 컨셉이 먹히는 트렌드 근거"
    }}
  ]
}}"""

    data = client.chat_json(_BASE_RULES, user, temperature=0.9, max_tokens=8000)
    raw = data.get("concepts", data) if isinstance(data, dict) else data
    concepts = [Concept.from_dict(c) for c in raw if isinstance(c, dict)]
    if not concepts:
        raise RuntimeError("AI 가 컨셉을 만들지 못했습니다. 모델명이나 프롬프트를 확인하세요.")
    return concepts


def write_post(
    client: MyGenAssistClient,
    product: Product,
    concept: Concept,
    post_cfg: PostConfig,
    *,
    section_count: int = 4,
) -> BlogPost:
    user = f"""아래 상품과 확정된 홍보 컨셉으로 네이버 블로그 글을 완성해라.

{product.as_prompt_context(limit=4000)}

[확정 컨셉]
{json.dumps(concept.__dict__, ensure_ascii=False, indent=2)}

[글쓴이 페르소나]
{post_cfg.persona}

[작성 조건]
- 전체 분량 공백 포함 1500~2500자.
- 본문 소제목 {section_count}개. 각 소제목 아래 본문은 3~6문장.
- 컨셉의 keywords 를 본문에 자연스럽게 3~5회 녹인다. 억지로 반복하지 않는다.
- image_query 는 그 문단 분위기에 맞는 무료 스톡 사진을 찾기 위한 검색어다.
  반드시 영어로, 2~4단어로, 사람/사물/장면이 드러나게 쓴다. (예: "morning coffee desk")
- 마지막 문단에서 상품 페이지로 자연스럽게 유도하되 강매하지 않는다.
- tags 는 # 없이 단어만, 8~12개.

아래 JSON 형식으로만 출력한다.

{{
  "title": "최종 블로그 제목",
  "intro": "도입부 문단",
  "sections": [
    {{"heading": "소제목", "body": "본문 문단", "image_query": "english search words"}}
  ],
  "outro": "마무리 문단",
  "tags": ["태그1", "태그2"]
}}"""

    data = client.chat_json(_BASE_RULES, user, temperature=0.85, max_tokens=6000, websearch=False)
    if not isinstance(data, dict):
        raise RuntimeError("AI 응답 형식이 올바르지 않습니다.")

    sections = [
        Section(
            heading=str(s.get("heading", "")).strip(),
            body=str(s.get("body", "")).strip(),
            image_query=str(s.get("image_query", "")).strip(),
        )
        for s in data.get("sections", [])
        if isinstance(s, dict) and s.get("body")
    ]
    if not sections:
        raise RuntimeError("AI 가 본문을 만들지 못했습니다.")

    return BlogPost(
        title=str(data.get("title") or concept.title).strip(),
        intro=str(data.get("intro", "")).strip(),
        sections=sections,
        outro=str(data.get("outro", "")).strip(),
        tags=[str(t).lstrip("#").strip() for t in data.get("tags", []) if str(t).strip()],
        disclosure=post_cfg.disclosure,
    )
