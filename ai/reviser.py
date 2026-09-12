"""기준 미달 원고를 고친다.

처음부터 다시 쓰면 잘 쓴 부분까지 사라져 점수가 오히려 떨어지는 일이 잦다.
그래서 백지 재집필 대신, 평가에서 지적된 지점과 빠진 키워드만 집어서 고쳐 쓴다.
"""

from __future__ import annotations

from dataclasses import replace

from ai.client import MyGenAssistClient
from ai.prompts import WRITER_RULES
from core.models import (
    KIND_CALLOUT,
    KIND_CTA,
    KIND_PARAGRAPH,
    KIND_QUOTE,
    Article,
    QualityScore,
    SeoPlan,
    SeoScore,
)

_EDITABLE = (KIND_PARAGRAPH, KIND_QUOTE, KIND_CTA, KIND_CALLOUT)


def missing_keywords(article: Article, seo: SeoPlan) -> list[str]:
    body = article.body_text()
    return [k for k in seo.sub_keywords if k and k not in body]


def revise(
    client: MyGenAssistClient,
    article: Article,
    seo: SeoPlan,
    quality: QualityScore,
    seo_score: SeoScore,
    persona: str,
) -> Article:
    targets = [
        (i, b)
        for i, b in enumerate(article.blocks)
        if b.kind in _EDITABLE and b.text and b.style != "info"
    ]
    if not targets:
        return article

    missing = missing_keywords(article, seo)
    numbered = "\n".join(f"[{i}] {b.text}" for i, b in targets)

    keyword_task = ""
    if missing:
        keyword_task = f"""
[본문에 빠진 보조 키워드]
{', '.join(missing)}

이 키워드들을 의미가 맞는 문단에 자연스럽게 녹여라. 한 문단에 몰아넣지 말고 흩어서 넣는다.
문맥에 안 맞으면 억지로 넣지 말고 건너뛴다."""

    density_task = ""
    main_density = seo_score.density.get(seo.main_keyword, 0.0)
    if main_density < 1.0:
        keyword_task += f"""

메인 키워드 '{seo.main_keyword}' 가 본문에 {main_density}% 밖에 없다.
2~3군데 더 자연스럽게 넣어라. 같은 문장에 반복하지 않는다."""

    user = f"""아래 블로그 원고가 품질 기준에 미달했다. 지적된 부분만 고쳐라.

[글쓴이 페르소나]
{persona}

[평가 총점] {quality.total}/100

[고쳐야 할 점]
{chr(10).join(f'- {f}' for f in quality.feedback)}
{chr(10).join(f'- {n}' for n in seo_score.notes)}
{keyword_task}{density_task}

[원고 문단들]
{numbered}

규칙.
- 고칠 필요가 있는 문단만 골라서 돌려준다. 멀쩡한 문단은 아예 포함하지 않는다.
- 문단 번호를 바꾸지 않는다. 문단을 합치거나 나누지 않는다.
- 내용을 더 구체적으로 만들되, 상품 페이지에 없는 사실을 지어내지 않는다.
- 분량이 부족하다는 지적이 있으면 해당 문단을 2~3문장 늘린다.

아래 JSON 형식으로만 출력한다. 키는 문단 번호다.

{{"2": "고친 문단", "7": "고친 문단"}}"""

    data = client.chat_json(WRITER_RULES, user, temperature=0.8, max_tokens=12000, websearch=False)
    if not isinstance(data, dict):
        return article

    blocks = list(article.blocks)
    for key, value in data.items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            continue
        text = str(value or "").strip()
        if text and 0 <= index < len(blocks) and blocks[index].kind in _EDITABLE:
            blocks[index] = replace(blocks[index], text=text)

    return Article(title=article.title, blocks=blocks, tags=article.tags, disclosure=article.disclosure)
