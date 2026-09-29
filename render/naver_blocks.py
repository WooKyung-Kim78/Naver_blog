"""블록을 네이버 스마트에디터가 받아들일 수 있는 조작 목록으로 바꾼다.

스마트에디터는 임의 HTML 을 붙여넣을 수 없다. 실제로 쓸 수 있는 구성 요소는
텍스트, 이미지, 인용구, 구분선, 쇼핑 커넥트 상품 카드 정도이고 표는 자동화가 사실상
불가능하다. 그래서 표는 유니코드 기호로 시각적 구조를 만들어 텍스트로 넣는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.models import (
    KIND_CALLOUT,
    KIND_CHECKLIST,
    KIND_CTA,
    KIND_DIVIDER,
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
    "summary": "총평",
}


@dataclass
class Op:
    """에디터에 순서대로 적용할 조작.

    kind 는 text / image / quote / divider / product.

    product 는 툴바의 '쇼핑커넥트' 버튼으로 넣는 상품 카드다. 에디터가 제휴 링크를
    직접 발급하므로 우리가 넘기는 건 어느 상품인지 찾을 검색어뿐이다. 상품을 못
    찾았을 때를 대비해 지금까지 쓰던 텍스트 링크를 value 에 함께 들려 보낸다.
    """

    kind: str
    value: str = ""
    query: str = ""  # product 전용. 에디터에서 상품을 찾을 검색어


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

        elif kind == KIND_DIVIDER:
            ops.append(Op("divider"))

        elif kind == KIND_IMAGE:
            if block.image and block.image.path:
                ops.append(Op("image", str(block.image.path)))
                if block.image.credit:
                    text(f"({block.image.credit})")

        elif kind == KIND_LINK:
            ops.append(_buy_link(block, "\n".join([f"👉 {block.text}", block.href])))

        elif kind == KIND_CTA:
            # 마지막 구매 안내는 썸네일을 먼저 보여주고 바로 아래에 링크를 붙인다.
            if block.image and block.image.path:
                ops.append(Op("image", str(block.image.path)))
            ops.append(_buy_link(block, "\n".join([RULE, f"👉 {block.text}", block.href, RULE])))

    if article.tags:
        text("\n" + " ".join(f"#{t}" for t in article.tags))

    return ops


def _buy_link(block, fallback: str) -> Op:
    """상품 카드로 넣을 수 있으면 카드로, 아니면 예전처럼 텍스트 링크로.

    카드에 상품명이 없으면 에디터에서 찾을 방법이 없으므로 텍스트 링크로 둔다.
    네이버 에디터는 줄 단독으로 놓인 URL 을 자동으로 링크로 바꿔 준다.
    """
    if block.card and block.card.title:
        return Op("product", fallback, query=block.card.title)
    return Op("text", fallback)


def dump(ops: list[Op]) -> str:
    """naver.txt 로 남길 사람이 읽는 형태.

    product 는 첫 줄이 검색어이고 나머지가 실패했을 때 쓸 텍스트다.
    """
    chunks = []
    for op in ops:
        head = f"[{op.kind}] {op.query}\n{op.value}" if op.kind == "product" else f"[{op.kind}] {op.value}"
        chunks.append(head.rstrip())
    return "\n\n".join(chunks)


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
