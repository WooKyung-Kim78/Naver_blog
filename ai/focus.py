"""상세페이지를 읽고 '이 글에서 밀어붙일 상품의 한 가지'를 세 가지로 좁혀 제안한다.

네이버 상품 상세페이지는 설명 대부분이 이미지 안에 글자로 박혀 있다. HTML 텍스트만
긁으면 핵심 소구점을 놓치기 때문에, 내려받은 상세 이미지를 모델에게 직접 보여준다.
이미지를 못 구하면 텍스트만으로 진행한다.

제안은 '마케팅 각도'가 아니라 '상품이 실제로 가진 기능·사양·구성'이어야 한다.
페이지 텍스트에는 적립·배송 안내나 다른 추천 상품이 섞여 들어오기 때문에, 근거를
상세페이지에서 그대로 인용하게 해서 상품 밖으로 새는 것을 막는다.
"""

from __future__ import annotations

from pathlib import Path

from ai.client import MyGenAssistClient
from ai.prompts import BASE_RULES
from core.models import FocusPoint, ProductBrief, Product

#: 비전 호출은 이미지 한 장당 수십 초가 걸린다. 상세페이지 앞부분에 핵심이 몰려 있으므로
#: 점수 높은 몇 장만 보낸다.
MAX_VISION_IMAGES = 4

_SCHEMA = """{
  "focus_points": [
    {
      "title": "집중할 상품의 기능·사양·구성. 그 이름을 그대로 넣어 한 줄로",
      "angle": "그 기능을 글에서 어떻게 풀어갈지",
      "evidence": "상세페이지나 스펙에 적힌 문구/수치를 그대로 옮긴다. 옮길 게 없으면 이 포인트를 만들지 마라",
      "target": "그 기능이 실제로 도움이 되는 사람",
      "why_now": "그 기능이 구매 결정에서 중요한 이유",
      "keywords": ["그 기능과 관련해 사람들이 검색할 말", "2~4개"],
      "risk": "이 기능만 앞세웠을 때 놓치게 되는 것"
    }
  ]
}"""


def propose(
    client: MyGenAssistClient,
    product: Product,
    brief: ProductBrief,
    detail_images: list[Path] | None = None,
    *,
    count: int = 3,
    avoid: list[str] | None = None,
    hint: str = "",
) -> list[FocusPoint]:
    images = (detail_images or [])[:MAX_VISION_IMAGES]

    vision_note = (
        "\n첨부된 이미지는 이 상품의 상세페이지다. 이미지 안의 문구와 수치까지 읽어라."
        if images
        else ""
    )

    retry_note = ""
    if avoid:
        retry_note += (
            "\n[이미 제안했다가 거절당한 것들. 다시 내지 마라]\n"
            + "\n".join(f"- {t}" for t in avoid)
            + "\n"
        )
    if hint:
        retry_note += f"\n[사용자가 원하는 방향]\n{hint}\n"

    user = f"""아래 상품으로 네이버 블로그 리뷰를 쓰려 한다.
장점을 다 나열한 글은 아무것도 각인되지 않는다. 이 상품이 가진 것 중에서 글 하나가
집중해서 밀 만한 것을 {count}가지 골라라.{vision_note}

{product.as_prompt_context(limit=4000)}

[분석된 특징]
{chr(10).join(f'- {f.name}: {f.detail}' for f in brief.features) or '- (없음)'}

[타깃 후보]
{chr(10).join(f'- {p.who} / {p.pain}' for p in brief.personas) or '- (없음)'}
{retry_note}
요구사항.
- title 은 반드시 이 상품이 실제로 가진 기능·사양·구성·성능을 가리켜야 한다.
  그 이름을 문장 안에 그대로 넣어라. 예: "에이밍 모드", "슬로프 보정", "외부 LCD".
- 마케팅 전략을 쓰지 마라. "가성비를 어필한다", "한 줄 메시지로 각인시킨다",
  "초기 구매 장벽을 낮춘다" 같은 것은 상품의 포인트가 아니라 글쓰기 방법이다. 금지다.
- evidence 는 상세페이지·스펙·리뷰에 실제로 적힌 문구나 수치를 그대로 옮긴다.
  옮길 근거가 없으면 그 포인트는 만들지 마라. 지어내면 실패다.
- 페이지에는 적립·할인·배송·멤버십 안내와 다른 추천 상품이 섞여 있다.
  그런 것은 이 상품의 포인트가 아니다. 절대 쓰지 마라.
- {count}개는 서로 다른 기능이어야 한다. 같은 기능을 말만 바꾼 것이면 실패다.

아래 JSON 형식으로만 출력한다.

{_SCHEMA}"""

    data = client.chat_json(
        BASE_RULES,
        user,
        temperature=0.85,
        max_tokens=10000,
        websearch=False,
        images=images or None,
    )

    raw = data.get("focus_points", data) if isinstance(data, dict) else data
    points = [
        FocusPoint(
            title=_s(p.get("title")),
            angle=_s(p.get("angle")),
            evidence=_s(p.get("evidence")),
            target=_s(p.get("target")),
            why_now=_s(p.get("why_now")),
            keywords=[str(k).strip() for k in (p.get("keywords") or []) if str(k).strip()],
            risk=_s(p.get("risk")),
        )
        for p in (raw or [])
        if isinstance(p, dict) and _s(p.get("title"))
    ]

    if not points:
        raise RuntimeError("AI 가 집중 포인트를 만들지 못했습니다.")
    return points


def _s(value: object) -> str:
    return str(value or "").strip()
