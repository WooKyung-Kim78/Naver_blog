"""섹션 본문을 '섹션/위치' 키로 펼치고, 고친 문장을 같은 키로 되돌려 넣는다.

키 형식
  h       소제목          p0, p1  문단
  i0      항목            x0      추가 항목(비추천 대상)
  r0.1    표 0행 1열       s0.1    장면 0의 본문 (s0.0 은 장면 소제목)
  c       마무리 문장(총평, 구매 안내)
"""

from __future__ import annotations

import copy
import re

from core.models import SectionDraft

#: 지워도 되는 단위. 소제목·표·마무리 문장은 비우면 구조가 깨진다.
_DELETABLE = re.compile(r"^(p|i|x)\d+$|^s\d+\.1$")


def iter_units(sections: dict[str, SectionDraft], *, prose_only: bool = False):
    for sid, draft in sections.items():
        if not prose_only and draft.heading:
            yield f"{sid}/h", draft.heading
        for i, text in enumerate(draft.paragraphs):
            yield f"{sid}/p{i}", text
        if not prose_only:
            for i, text in enumerate(draft.items):
                yield f"{sid}/i{i}", text
            for i, text in enumerate(draft.extra_items):
                yield f"{sid}/x{i}", text
            for i, row in enumerate(draft.rows):
                for j, text in enumerate(row):
                    yield f"{sid}/r{i}.{j}", text
        for i, scene in enumerate(draft.scenes):
            if not prose_only and scene:
                yield f"{sid}/s{i}.0", scene[0]
            if len(scene) > 1:
                yield f"{sid}/s{i}.1", scene[1]
        if draft.closing:
            yield f"{sid}/c", draft.closing


def numbered(sections: dict[str, SectionDraft], *, prose_only: bool = False) -> str:
    return "\n".join(f"[{key}] {text}" for key, text in iter_units(sections, prose_only=prose_only))


def apply_changes(sections: dict[str, SectionDraft], changes: dict[str, str]) -> dict[str, SectionDraft]:
    """모르는 키는 버린다. 빈 문자열은 지워도 되는 단위만 지운다."""
    result = copy.deepcopy(sections)
    known = {key for key, _ in iter_units(sections)}

    per_section: dict[str, dict[str, str]] = {}
    for key, text in changes.items():
        if key not in known or not isinstance(text, str):
            continue
        text = text.strip()
        sid, unit = key.split("/", 1)
        if not text and not _DELETABLE.match(unit):
            continue
        per_section.setdefault(sid, {})[unit] = text

    for sid, edits in per_section.items():
        draft = result[sid]
        if "h" in edits:
            draft.heading = edits["h"]
        if "c" in edits:
            draft.closing = edits["c"]
        draft.paragraphs = _rebuild(draft.paragraphs, "p", edits, split=True)
        draft.items = _rebuild(draft.items, "i", edits)
        draft.extra_items = _rebuild(draft.extra_items, "x", edits)
        for i, row in enumerate(draft.rows):
            for j in range(len(row)):
                if f"r{i}.{j}" in edits:
                    row[j] = edits[f"r{i}.{j}"]
        scenes = []
        for i, scene in enumerate(draft.scenes):
            scene = list(scene)
            for j in range(len(scene)):
                if f"s{i}.{j}" in edits:
                    scene[j] = edits[f"s{i}.{j}"]
            if len(scene) < 2 or scene[1]:
                scenes.append(scene)
        draft.scenes = scenes
    return result


def _rebuild(values: list[str], prefix: str, edits: dict[str, str], *, split: bool = False) -> list[str]:
    out: list[str] = []
    for i, text in enumerate(values):
        new = edits.get(f"{prefix}{i}", text)
        if not new:
            continue
        if split:
            out += [part.strip() for part in new.split("\n") if part.strip()]
        else:
            out.append(new)
    return out
