"""상세페이지를 읽고 '이 글에서 밀어붙일 한 가지'를 세 가지로 좁혀 제안한다.

네이버 상품 상세페이지는 설명 대부분이 이미지 안에 글자로 박혀 있다. HTML 텍스트만
긁으면 핵심 소구점을 놓치기 때문에, 내려받은 상세 이미지를 모델에게 직접 보여준다.
이미지를 못 구하면 텍스트만으로 진행한다.
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
      "title": "이 글이 밀 한 가지를 한 문장으로",
      "angle": "글 전체를 어떤 각도로 끌고 갈지",
      "evidence": "상세페이지의 어떤 내용에서 나온 판단인지 구체적으로",
      "target": "이 각도가 꽂히는 독자",
      "why_now": "지금 이 각도가 먹히는 이유",
      "keywords": ["이 각도에서 밀 검색 키워드", "2~4개"],
      "risk": "이 각도의 약점이나 놓치게 되는 것"
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
) -> list[FocusPoint]:
    images = (detail_images or [])[:MAX_VISION_IMAGES]

    vision_note = (
        "\n첨부된 이미지는 이 상품의 상세페이지다. 이미지 안의 문구와 수치까지 읽고 판단해라."
        if images
        else ""
    )

    user = f"""아래 상품으로 네이버 블로그 리뷰를 쓰려 한다.
글 하나에 모든 장점을 다 담으면 아무것도 각인되지 않는다. 그래서 이 글에서 집중해서
밀어붙일 포인트를 {count}가지로 좁혀 제안해라.{vision_note}

{product.as_prompt_context(limit=4000)}

[분석된 특징]
{chr(10).join(f'- {f.name}: {f.detail}' for f in brief.features)}

[타깃 후보]
{chr(10).join(f'- {p.who} / {p.pain}' for p in brief.personas)}

요구사항.
- {count}개는 서로 확실히 달라야 한다. 같은 말을 바꿔 쓴 것이면 실패다.
  기능 중심 / 가격 대비 가치 / 특정 상황이나 사용자층 중심처럼 축 자체를 다르게 잡아라.
- evidence 는 반드시 상세페이지에 실제로 있는 내용이어야 한다. 지어내지 마라.
- risk 에는 이 각도를 골랐을 때 포기하게 되는 것을 솔직히 쓴다.

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
