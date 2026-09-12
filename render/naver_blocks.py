"""블록을 네이버 스마트에디터가 받아들일 수 있는 조작 목록으로 바꾼다.

스마트에디터는 임의 HTML 을 붙여넣을 수 없다. 실제로 쓸 수 있는 구성 요소는
텍스트, 이미지, 인용구, 구분선 정도이고 표와 아코디언은 자동화가 사실상 불가능하다.
그래서 표와 FAQ 는 유니코드 기호로 시각적 구조를 만들어 텍스트로 넣는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.models import (
    KIND_CALLOUT,
    KIND_CHECKLIST,
    KIND_CTA,
    KIND_DIVIDER,
    KIND_FAQ,
    KIND_HEADING,
    KIND_IMAGE,
    KIND_LINK,
    KIND_PARAGRAPH,
    KIND_QUOTE,
    KIND_TABLE,
    Article,
)

RULE = "─" * 22

CALLOUT_MARK = {
    "info": "ℹ️",
    "tip": "💡",
    "warn": "⚠️",
    "summary": "📌",
}
CALLOUT_TITLE = {
    "info": "안내",
    "tip": "팁",
    "warn": "체크포인트",
    "summary": "3줄 요약",
}


@dataclass
class Op:
    """에디터에 순서대로 적용할 조작. kind 는 text / image / quote / divider."""

    kind: str
    value: str = ""


def render(article: Article) -> list[Op]:
    ops: list[Op] = []

    def text(value: str) -> None:
        if value.strip():
            ops.append(Op("text", value.rstrip()))

    for block in article.blocks:
        kind = block.kind

        if kind == KIND_HEADING:
            mark = "■" if block.level <= 2 else "▸"
            text(f"\n{mark} {block.text}")

        elif kind == KIND_PARAGRAPH:
            text(block.text)

        elif kind == KIND_QUOTE:
            ops.append(Op("quote", block.text))

        elif kind == KIND_CALLOUT:
            mark = CALLOUT_MARK.get(block.style, "ℹ️")
            title = CALLOUT_TITLE.get(block.style, "안내")
            lines = [l for l in block.text.split("\n") if l.strip()]
            text("\n".join([RULE, f"{mark} {title}", *lines, RULE]))

        elif kind == KIND_CHECKLIST:
            text("\n".join(f"✅ {item}" for item in block.items))

        elif kind == KIND_TABLE:
            text(_table_as_text(block.headers, block.rows))

        elif kind == KIND_FAQ:
            for faq in block.qa:
                text(f"Q. {faq.question}\nA. {faq.answer}")

        elif kind == KIND_DIVIDER:
            ops.append(Op("divider"))

        elif kind == KIND_IMAGE:
            if block.image and block.image.path:
                ops.append(Op("image", str(block.image.path)))
                if block.image.credit:
                    text(f"({block.image.credit})")

        elif kind == KIND_LINK:
            # 네이버 에디터는 줄 단독으로 놓인 URL 을 자동으로 링크로 바꾼다.
            text("\n".join([f"👉 {block.text}", block.href]))

        elif kind == KIND_CTA:
            # 마지막 구매 안내는 썸네일을 먼저 보여주고 바로 아래에 링크를 붙인다.
            if block.image and block.image.path:
                ops.append(Op("image", str(block.image.path)))
            text("\n".join([RULE, f"👉 {block.text}", block.href, RULE]))

    if article.tags:
        text("\n" + " ".join(f"#{t}" for t in article.tags))

    return ops


def _table_as_text(headers: list[str], rows: list[list[str]]) -> str:
    """표 컴포넌트 자동화는 불안정해서, 항목별 블록 형태의 텍스트로 대신한다."""
    lines = [RULE]
    for row in rows:
        pairs = list(zip(headers, row))
        if pairs:
            lines.append(f"▪ {pairs[0][1]}")
            lines += [f"   {label}: {value}" for label, value in pairs[1:] if value]
            lines.append("")
    lines.append(RULE)
    return "\n".join(lines).replace("\n\n" + RULE, "\n" + RULE)
