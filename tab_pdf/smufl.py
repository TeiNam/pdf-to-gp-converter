"""SMuFL 사설 영역 코드포인트 → 사람이 읽는 분류명.

Finale 는 음악 기호를 유니코드 사설 영역(U+E000~U+F8FF)에 넣는다. 코드포인트만
보면 뜻을 알 수 없어서, AI 보정에 넘길 때 분류명을 붙여준다. 로컬 소형 모델은
`U+E4E5` 를 못 읽지만 "쉼표" 는 읽는다.

범위는 SMuFL 1.4 표준 구획이다. 추측이 아니라 표준이므로 다른 Finale/Sibelius
악보에도 그대로 통한다.
"""

NOTEHEAD = (0xE0A0, 0xE0FF)
SLASH = (0xE100, 0xE10F)
TREMOLO = (0xE220, 0xE23F)
FLAG = (0xE240, 0xE25F)
TIMESIG_DIGIT = (0xE080, 0xE089)
ARTICULATION = (0xE4A0, 0xE4BF)
HOLD_PAUSE = (0xE4C0, 0xE4DF)
REST = (0xE4E0, 0xE4FF)
OCTAVE = (0xE510, 0xE51F)
DYNAMIC = (0xE520, 0xE54F)
ORNAMENT = (0xE560, 0xE58F)
STRING_TECHNIQUE = (0xE610, 0xE62F)
PLUCK = (0xE630, 0xE63F)
KEYBOARD = (0xE650, 0xE67F)
GUITAR = (0xE830, 0xE85F)
ANALYTICS = (0xE860, 0xE88F)
ARROW = (0xEB60, 0xEB8F)
PRIVATE_USE = (0xE000, 0xF8FF)

# 분류명은 IR·프롬프트에 그대로 실린다. 순서가 곧 우선순위다 (범위가 겹치지 않아
# 실제로는 무관하지만, 새 범위를 넣을 때 앞쪽이 이긴다는 규칙을 남긴다).
LABELS: tuple[tuple[tuple[int, int], str], ...] = (
    (TIMESIG_DIGIT, "박자표숫자"),
    (NOTEHEAD, "음표머리"),
    (SLASH, "슬래시음표"),
    (TREMOLO, "트레몰로"),
    (FLAG, "꼬리·깃발"),
    (ARTICULATION, "아티큘레이션"),
    (HOLD_PAUSE, "늘임표·숨표"),
    (REST, "쉼표"),
    (OCTAVE, "옥타브기호"),
    (DYNAMIC, "다이내믹"),
    (ORNAMENT, "장식음"),
    (STRING_TECHNIQUE, "현주법"),
    (PLUCK, "피킹·플럭"),
    (KEYBOARD, "건반기호"),
    (GUITAR, "기타주법"),
    (ANALYTICS, "분석기호"),
    (ARROW, "화살표"),
)


def in_range(char: str, bounds: tuple[int, int]) -> bool:
    low, high = bounds
    return low <= ord(char) <= high


def label(char: str) -> str | None:
    """음악 기호면 분류명, 평범한 글자면 None."""
    if not in_range(char, PRIVATE_USE):
        return None
    for bounds, name in LABELS:
        if in_range(char, bounds):
            return name
    return "미분류기호"
