"""AI 이미지 생성용 프롬프트 템플릿.

슬롯(어떤 그림이 필요한가) x 카테고리(어떤 톤이어야 하는가) 조합으로 프롬프트를 만든다.
상품명을 그대로 넣으면 생성 모델이 엉뚱한 로고나 글자를 그려 넣으므로, 상품명 대신
생김새 묘사(descriptor)를 넣는 것이 핵심이다.
"""

from __future__ import annotations

from core.models import (
    SLOT_CTA,
    SLOT_FEATURE,
    SLOT_HERO,
    SLOT_INFOGRAPHIC,
    SLOT_LIFESTYLE,
    SLOT_PRODUCT,
)

#: 슬롯별 기본 구도. {subject} 에 상품 묘사가, {scene} 에 상황 묘사가 들어간다.
SLOT_TEMPLATES: dict[str, str] = {
    SLOT_HERO: (
        "A clean hero shot of {subject}, {scene}, centered composition, "
        "soft natural window light, shallow depth of field, 50mm lens, "
        "commercial product photography, high realism"
    ),
    SLOT_PRODUCT: (
        "A detailed product photograph of {subject} on a minimal surface, {scene}, "
        "studio softbox lighting, crisp focus on texture and material, 85mm macro lens, "
        "catalog photography, high realism"
    ),
    SLOT_FEATURE: (
        "A close-up detail shot of {subject} highlighting its key part, {scene}, "
        "directional light revealing surface detail, 100mm macro lens, "
        "technical product photography, high realism"
    ),
    SLOT_LIFESTYLE: (
        "A candid lifestyle photograph of a person using {subject} in real life, {scene}, "
        "natural daylight, authentic unposed moment, 35mm lens, "
        "editorial lifestyle photography, high realism"
    ),
    SLOT_INFOGRAPHIC: (
        "A flat lay overhead composition of {subject} with related everyday objects, {scene}, "
        "even soft lighting, organized minimal arrangement on a neutral background, "
        "top-down knolling photography, high realism"
    ),
    SLOT_CTA: (
        "An aspirational closing shot of {subject} in a tidy inviting setting, {scene}, "
        "warm golden hour light, generous negative space on one side for text, 50mm lens, "
        "commercial photography, high realism"
    ),
}

#: 카테고리별 분위기 보정. 없는 카테고리는 DEFAULT_STYLE 을 쓴다.
CATEGORY_STYLES: dict[str, str] = {
    "전자기기": "modern minimalist interior, cool neutral tones, matte surfaces, tech aesthetic",
    "가전": "bright clean kitchen or living space, white and wood tones, airy feel",
    "뷰티": "soft pastel palette, dewy skin tones, marble and glass surfaces, spa-like calm",
    "패션": "urban street or bright studio, fashionable styling, confident mood",
    "식품": "warm inviting kitchen table, natural wood, fresh ingredients nearby, appetizing",
    "리빙": "cozy scandinavian home interior, linen textures, plants, warm afternoon light",
    "스포츠": "outdoor natural setting, dynamic energy, sunlight and open space",
    "레저": "outdoor natural setting, dynamic energy, sunlight and open space",
    "육아": "soft safe nursery tones, gentle light, warm family atmosphere",
    "반려동물": "homey living room, playful warm mood, pet-friendly setting",
}

DEFAULT_STYLE = "clean contemporary setting, balanced neutral palette, natural light"

#: 생성 모델이 흔히 저지르는 실수를 막는 공통 금지 조건.
NEGATIVE = (
    "no text, no letters, no logos, no watermarks, no brand names, "
    "no distorted hands, no extra fingers, not a collage"
)


def build(slot: str, subject: str, category: str, scene: str = "") -> str:
    """슬롯과 카테고리에 맞는 최종 프롬프트를 만든다."""
    template = SLOT_TEMPLATES.get(slot, SLOT_TEMPLATES[SLOT_HERO])
    style = _style_for(category)
    body = template.format(subject=subject or "the product", scene=scene or style)
    return f"{body}, {style}, {NEGATIVE}"


def _style_for(category: str) -> str:
    for key, style in CATEGORY_STYLES.items():
        if key in category:
            return style
    return DEFAULT_STYLE
