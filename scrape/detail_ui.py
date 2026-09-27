"""스마트스토어 상세설명 UI 조작.

상세정보가 탭 뒤에 있거나, 긴 설명은 '상세정보 펼쳐보기' 뒤에 접혀 있다.
이미지·본문 수집 전에 이 버튼들을 눌러 전체 내용을 노출한다.
"""

from __future__ import annotations

import re

# 네이버 공식 문구는 '상세정보 펼쳐보기'. '상품정보 펼쳐 보기' 변형도 있다.
# 버튼이 아니라 div/span 인 경우가 많아 텍스트로 찾는다. '접기' 는 제외.
_EXPAND_LABEL = re.compile(
    r"(상품|상세)\s*정보\s*펼쳐\s*보기|펼쳐\s*보기|더\s*보기|원본\s*보기"
)
_COLLAPSE_LABEL = re.compile(r"접기")


def open_detail_tab(page) -> None:
    """스마트스토어는 상세정보가 탭 뒤에 숨어 있는 경우가 있다."""
    for name in ("상세정보", "상품정보", "상세설명"):
        try:
            tab = page.get_by_role("tab", name=name).first
            if tab.is_visible(timeout=1500):
                tab.click()
                page.wait_for_timeout(1500)
                return
        except Exception:
            continue


def expand_collapsed_detail(page, *, max_clicks: int = 10) -> bool:
    """접힌 상세설명 버튼을 보이는 만큼 모두 누른다. 하나라도 눌렀으면 True.

    상세 영역이 여러 덩어리로 나뉘어 펼쳐보기 가 여러 개일 수 있고,
    하나를 펼친 뒤에 또 다른 버튼이 나타나기도 한다. 클릭할 때마다 DOM 이
    바뀌므로 매번 현재 보이는 첫 버튼을 다시 찾는다.
    """
    clicked = 0
    for _ in range(max_clicks):
        btn = _find_expand_control(page)
        if btn is None:
            break
        try:
            btn.scroll_into_view_if_needed(timeout=2000)
            btn.click(timeout=3000)
            clicked += 1
            page.wait_for_timeout(1500)
        except Exception:
            break
    return clicked > 0


def _find_expand_control(page):
    """role=button 우선, 없으면 텍스트 노드(div/span)까지 본다."""
    candidates = (
        page.get_by_role("button", name=_EXPAND_LABEL),
        page.locator("button, a, [role='button'], span, div").filter(has_text=_EXPAND_LABEL),
    )
    for locator in candidates:
        try:
            count = min(locator.count(), 8)
        except Exception:
            continue
        for i in range(count):
            try:
                btn = locator.nth(i)
                if not btn.is_visible(timeout=300):
                    continue
                label = (btn.inner_text(timeout=500) or "").strip()
                # 본문 덩어리를 잘못 잡지 않도록 짧은 라벨만 클릭
                if len(label) > 40 or not _EXPAND_LABEL.search(label):
                    continue
                if _COLLAPSE_LABEL.search(label):
                    continue
                return btn
            except Exception:
                continue
    return None
