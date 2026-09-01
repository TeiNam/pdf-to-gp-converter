"""SMuFL 사설 영역 코드포인트 → 사람이 읽는 분류명.

Finale 는 음악 기호를 유니코드 사설 영역(U+E000~U+F8FF)에 넣는다. 코드포인트만
보면 뜻을 알 수 없어서, AI 보정에 넘길 때 분류명을 붙여준다. 로컬 소형 모델은
`U+E4E5` 를 못 읽지만 "쉼표" 는 읽는다.

범위는 SMuFL 1.4 표준 구획이다. 추측이 아니라 표준이므로 다른 Finale/Sibelius
악보에도 그대로 통한다.
"""

BARLINE = (0xE030, 0xE03F)
REPEAT = (0xE040, 0xE04F)          # segno·coda 포함
CLEF = (0xE050, 0xE07F)
NOTEHEAD = (0xE0A0, 0xE0FF)
SLASH = (0xE100, 0xE10F)
INDIVIDUAL_NOTE = (0xE1D0, 0xE1EF)  # 붙임점 등
STEM = (0xE210, 0xE21F)
TREMOLO = (0xE220, 0xE23F)
FLAG = (0xE240, 0xE25F)
ACCIDENTAL = (0xE260, 0xE26F)
TIMESIG_DIGIT = (0xE080, 0xE089)
TIME_SIGNATURE = (0xE080, 0xE09F)
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
    (TIME_SIGNATURE, "박자표"),
    (BARLINE, "마디선"),
    (REPEAT, "반복·진행기호"),
    (CLEF, "음자리표"),
    (NOTEHEAD, "음표머리"),
    (SLASH, "슬래시음표"),
    (INDIVIDUAL_NOTE, "붙임점·개별음표"),
    (STEM, "기둥"),
    (TREMOLO, "트레몰로"),
    (FLAG, "꼬리·깃발"),
    (ACCIDENTAL, "임시표"),
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


# 구획 이름만으로는 부족한 글리프의 표준 SMuFL 이름. 범위 라벨은 "아티큘레이션"
# 까지만 말해주는데, GP5 로 옮기려면 악센트인지 스타카토인지를 알아야 한다.
# 실측: 이 악보의 U+E4A1 은 타브 아래 '>' 로 그려지는 악센트다 (렌더해서 확인했다).
NAMES: dict[int, str] = {
    0xE047: "segno",
    0xE048: "coda",
    0xE050: "gClef",
    0xE0A2: "noteheadWhole",
    0xE0A3: "noteheadHalf",
    0xE0A4: "noteheadBlack",
    0xE0A9: "noteheadXBlack",           # 뮤트 노트 'x'
    0xE100: "noteheadSlashHorizontalEnds",
    0xE1E7: "augmentationDot",          # 점음표의 점 — 리듬에 영향
    0xE240: "flag8thUp",
    0xE241: "flag8thDown",
    0xE242: "flag16thUp",
    0xE243: "flag16thDown",
    0xE260: "accidentalFlat",
    0xE261: "accidentalNatural",
    0xE262: "accidentalSharp",
    0xE4A0: "articAccentAbove",
    0xE4A1: "articAccentBelow",
    0xE4A2: "articStaccatoAbove",
    0xE4A3: "articStaccatoBelow",
    0xE4A4: "articTenutoAbove",
    0xE4A5: "articTenutoBelow",
    0xE4A6: "articStaccatissimoAbove",
    0xE4A7: "articStaccatissimoBelow",
    0xE4AC: "articMarcatoAbove",
    0xE4AD: "articMarcatoBelow",
    0xE4C0: "fermataAbove",
    0xE4C1: "fermataBelow",
    0xE4E3: "restWhole",
    0xE4E4: "restHalf",
    0xE4E5: "restQuarter",
    0xE4E6: "rest8th",
    0xE4E7: "rest16th",
    0xE4E8: "rest32nd",
    0xE610: "stringsDownBow",           # 타브에서는 다운스트로크
    0xE612: "stringsUpBow",             # 타브에서는 업스트로크
    0xE614: "stringsHarmonic",
}


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


def name(char: str) -> str | None:
    """표준 SMuFL 글리프 이름. 표에 없으면 None — 그때는 코드포인트로 판단한다."""
    return NAMES.get(ord(char))


def codepoint(char: str) -> str:
    return f"U+{ord(char):04X}"
