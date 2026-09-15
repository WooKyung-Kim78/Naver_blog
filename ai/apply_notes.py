"""사람이 적은 수정 제안으로 원고를 다시 쓴다.

이미지 경로와 구매 링크 주소는 바꾸지 않는다. 대가성 문구도 고정이다.
"""

from __future__ import annotations

from dataclasses import replace

from ai.client import MyGenAssistClient
from ai.prompts import WRITER_RULES
from core.models import (
    KIND_CALLOUT,
    KIND_CHECKLIST,
    KIND_CTA,
    KIND_FAQ,
    KIND_HEADING,
    KIND_IMAGE,
    KIND_LINK,
    KIND_TABLE,
    Article,
    FAQ,
    SeoPlan,
)

_LOCKED = {KIND_IMAGE}


def apply_notes(
    client: MyGenAssistClient,
    article: Article,
    notes: str,
    *,
    persona: str,
    disclosure: str,
    seo: SeoPlan | None = None,
) -> Article:
    numbered = _outline(article)
    seo_hint = ""
    if seo and seo.main_keyword:
        seo_hint = (
            f"\n[SEO]\n메인 키워드: {seo.main_keyword}\n"
            f"보조 키워드: {', '.join(seo.sub_keywords)}\n"
            "키워드는 지우지 말고, 제목과 도입·소제목에 자연스럽게 남긴다."
        )

    user = f"""아래는 이미 만들어 둔 블로그 원고와, 사람이 미리보기를 보고 적은 수정 제안이다.
제안사항을 모두 반영해 글을 다시 써라. 제안과 상관없는 부분은 유지해도 된다.

[글쓴이 페르소나]
{persona}
{seo_hint}

[수정 제안]
{notes.strip()}

[현재 원고]
제목: {article.title}
태그: {', '.join(article.tags)}

{numbered}

규칙.
- 제안사항을 빠짐없이 반영한다. 글을 다시 쓰는 것이지, 제안 문장을 그대로 본문에 붙여 넣지 않는다.
- index 를 바꾸거나 블록을 빼지 않는다. 없는 번호를 만들지 않는다.
- kind 가 image 인 블록은 출력하지 않는다. 사진은 그대로 둔다.
- kind 가 link 또는 cta 이면 text 만 고친다. 주소는 넣지 않는다.
- style 이 info 인 안내 문구는 출력하지 않는다. 대가성 문구는 프로그램이 고정한다.
- 상품 페이지에 없는 사실을 지어내지 않는다.
- 의료·광고 과장(최고, 1위, 완치 등)을 쓰지 않는다.

아래 JSON 만 출력한다.

{{
  "title": "새 제목 또는 기존 제목",
  "tags": ["태그"],
  "blocks": [
    {{"index": 2, "text": "고친 문단 또는 제목"}},
    {{"index": 8, "items": ["체크 항목"]}},
    {{"index": 9, "headers": ["열"], "rows": [["값"]]}},
    {{"index": 10, "qa": [{{"question": "Q", "answer": "A"}}]}}
  ]
}}"""

    data = client.chat_json(WRITER_RULES, user, temperature=0.7, max_tokens=14000, websearch=False)
    if not isinstance(data, dict):
        return article
    return _merge(article, data, disclosure)


def notes_are_empty(text: str) -> bool:
    useful = []
    for row in (text or "").splitlines():
        stripped = row.strip()
        if not stripped or stripped in {"-", "*", "·"}:
            continue
        if stripped.startswith("#"):
            continue
        if "완성 원고가 아닙니다" in stripped or "제안만 적" in stripped:
            continue
        useful.append(stripped)
    return len("".join(useful)) < 8


def _outline(article: Article) -> str:
    lines = []
    for i, block in enumerate(article.blocks):
        if block.kind == KIND_IMAGE:
            lines.append(f"[{i}] image (고정. 출력하지 말 것)")
            continue
        if block.kind == KIND_CALLOUT and block.style == "info":
            lines.append(f"[{i}] disclosure (고정. 출력하지 말 것)")
            continue
        if block.kind == KIND_LINK:
            lines.append(f"[{i}] link text={block.text} href=고정")
        elif block.kind == KIND_CTA:
            lines.append(f"[{i}] cta text={block.text} href=고정")
        elif block.kind == KIND_HEADING:
            lines.append(f"[{i}] heading h{block.level} {block.text}")
        elif block.kind == KIND_CHECKLIST:
            lines.append(f"[{i}] checklist " + " / ".join(block.items))
        elif block.kind == KIND_TABLE:
            lines.append(f"[{i}] table headers={block.headers} rows={block.rows}")
        elif block.kind == KIND_FAQ:
            qa = " | ".join(f"Q.{f.question} A.{f.answer}" for f in block.qa)
            lines.append(f"[{i}] faq {qa}")
        else:
            lines.append(f"[{i}] {block.kind} {block.text}")
    return "\n".join(lines)


def _merge(article: Article, data: dict, disclosure: str) -> Article:
    edits = {}
    for item in data.get("blocks") or []:
        if not isinstance(item, dict):
            continue
        try:
            edits[int(item.get("index"))] = item
        except (TypeError, ValueError):
            continue

    blocks = []
    for i, block in enumerate(article.blocks):
        if block.kind in _LOCKED:
            blocks.append(block)
            continue
        if block.kind == KIND_CALLOUT and block.style == "info":
            blocks.append(replace(block, text=disclosure or block.text))
            continue
        edit = edits.get(i)
        if not edit:
            blocks.append(block)
            continue
        if block.kind == KIND_CHECKLIST and edit.get("items"):
            blocks.append(replace(block, items=[str(x).strip() for x in edit["items"] if str(x).strip()]))
        elif block.kind == KIND_TABLE and (edit.get("rows") or edit.get("headers")):
            headers = [str(h) for h in (edit.get("headers") or block.headers)]
            rows = [list(map(str, row)) for row in (edit.get("rows") or block.rows)]
            blocks.append(replace(block, headers=headers, rows=rows))
        elif block.kind == KIND_FAQ and edit.get("qa"):
            qa = [
                FAQ(question=str(f.get("question") or ""), answer=str(f.get("answer") or ""))
                for f in edit["qa"]
                if isinstance(f, dict)
            ]
            blocks.append(replace(block, qa=qa or block.qa))
        elif block.kind in (KIND_LINK, KIND_CTA):
            text = str(edit.get("text") or block.text).strip()
            blocks.append(replace(block, text=text or block.text))
        else:
            text = str(edit.get("text") or block.text).strip()
            blocks.append(replace(block, text=text or block.text))

    title = str(data.get("title") or article.title).strip() or article.title
    tags = [str(t).lstrip("#").replace(" ", "") for t in (data.get("tags") or article.tags) if str(t).strip()]
    return Article(title=title, blocks=blocks, tags=tags or article.tags, disclosure=disclosure or article.disclosure)
