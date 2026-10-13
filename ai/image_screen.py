"""상세 이미지 중 배송·반품 안내, 쿠폰·적립 혜택처럼 제품을 설명하지 않는 것을 걸러낸다.

상세페이지에는 배송·교환·반품 안내나 네이버쇼핑 쿠폰 배너가 이미지로 붙어 있는 경우가 많다.
긴 이미지는 evaluator 가 조각으로 자른 뒤에 검사하므로, 조각 단위로 걸러진다. 파일명은
대개 의미 없는 해시라 주소만으로는 못 거르므로, 내려받은 이미지를 AI 에게 직접 보여주고
제품 설명 이미지만 남긴다.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from ai.client import MyGenAssistClient
from core.models import ImageAsset

#: 비전 호출은 장당 시간이 걸린다. 본문에 실제로 들어가는 상세 이미지는 몇 장뿐이라
#: 점수 높은 순으로 이만큼만 확인한다.
MAX_SCREEN_IMAGES = 4

KIND_PRODUCT = "product"
KIND_POLICY = "policy"
KIND_OTHER = "other"

_POLICY_URL = re.compile(
    r"(delivery|shipping|refund|exchange|return|notice|coupon|baesong|banpum|gyohwan)", re.I
)

_SYSTEM = "너는 쇼핑몰 상세페이지 이미지를 분류하는 검수자다. 지시한 JSON 만 출력한다."

_USER = """첨부한 이미지 {count}장은 한 상품의 상세페이지에서 잘라낸 것이다. 순서대로 1번부터 {count}번이다.
각 이미지를 아래 셋 중 하나로 분류하라.

- product: 제품 자체를 설명한다. 외관, 색상, 구성품, 기능, 사양·스펙, 소재, 사이즈, 사용 방법, 착용·사용 장면.
- policy: 배송, 교환, 반품, 환불, A/S, 고객센터, 주문·결제, 구매 전 주의사항 같은 판매 정책 안내.
- other: 네이버쇼핑·스토어 쿠폰, 적립·멤버십 혜택, 이벤트·할인, 브랜드 소개, 다른 상품 광고, 인증서, 글자도 제품도 없는 여백.

배송·반품 안내나 쿠폰·혜택 문구가 이미지의 일부라도 크게 차지하면 product 가 아니다.

아래 JSON 형식으로만 출력한다.
{{"images": [{{"no": 1, "kind": "product"}}]}}"""


def looks_like_policy(url: str) -> bool:
    """파일 경로에 배송·반품 안내임이 드러나는 이미지."""
    return bool(_POLICY_URL.search(urlparse(url).path))


def can_see(client: MyGenAssistClient) -> bool:
    """responses 엔드포인트는 이미지를 보내지 않으므로 분류를 맡길 수 없다."""
    return client.cfg.provider == "gemini" or client.cfg.endpoint == "agent"


def classify(client: MyGenAssistClient, paths: list[Path]) -> list[str]:
    data = client.chat_json(
        _SYSTEM,
        _USER.format(count=len(paths)),
        temperature=0.0,
        max_tokens=4000,
        websearch=False,
        images=paths,
    )
    raw = data.get("images", []) if isinstance(data, dict) else data
    kinds = [KIND_OTHER] * len(paths)
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("no")) - 1
        except (TypeError, ValueError):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        if 0 <= index < len(paths) and kind in (KIND_PRODUCT, KIND_POLICY, KIND_OTHER):
            kinds[index] = kind
    return kinds


def screen(client: MyGenAssistClient, assets: list[ImageAsset]) -> tuple[list[ImageAsset], dict[str, int]]:
    """상세 이미지 중 제품 설명만 남긴다. 확인하지 못한 하위 점수 이미지도 버린다."""
    details = sorted(
        (a for a in assets if a.source == "detail" and a.path),
        key=lambda a: a.score,
        reverse=True,
    )[:MAX_SCREEN_IMAGES]
    if not details:
        return assets, {}

    kinds = classify(client, [a.path for a in details])
    counts: dict[str, int] = {}
    for kind in kinds:
        counts[kind] = counts.get(kind, 0) + 1

    kept = {id(a) for a, k in zip(details, kinds) if k == KIND_PRODUCT}
    return [a for a in assets if a.source != "detail" or id(a) in kept], counts
