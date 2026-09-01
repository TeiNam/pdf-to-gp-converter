"""코드네임 → 기타 프렛 보이싱.

이 곡(`나는반딧불`)에 실제로 쓰인 5개만 넣는다. 표에 없으면 None 을 돌려 호출자가
경고를 남기게 한다 — 추측으로 채우면 틀린 악보가 조용히 나온다.
string 1 = 고음 E, 6 = 저음 E. fret 0 = 개방현.
"""

import re
from dataclasses import dataclass

# 코드명 후보 판정. A~G 로 시작해야 하므로 'H'(해머온)·'2'/'3'(페이지 번호)는 걸러지고,
# 미등록 코드('Bm7')는 통과해 unknown_chord 경고 경로가 살아난다.
_CHORD_PATTERN = re.compile(r"^[A-G](?:[#b])?[A-Za-z0-9#b/+()-]*$")

# GP4/5 는 코드명을 22바이트 고정 필드에 쓴다 (pyguitarpro writeByteSizeString(…, 22)).
# 넘치면 조용히 잘려서 IR 과 .gp5 의 코드명이 달라진다 — 실측으로 30자가 22자로 잘렸다.
MAX_NAME_BYTES = 22

VOICINGS: dict[str, tuple[tuple[int, int], ...]] = {
    "Cadd9": ((5, 3), (4, 2), (3, 0), (2, 3), (1, 3)),
    "E7":    ((6, 0), (5, 2), (4, 0), (3, 1), (2, 0), (1, 0)),
    "Am":    ((5, 0), (4, 2), (3, 2), (2, 1), (1, 0)),
    "F":     ((4, 3), (3, 2), (2, 1), (1, 1)),
    "G":     ((6, 3), (5, 2), (4, 0), (3, 0), (2, 0), (1, 3)),
}


def looks_like_chord(token: str) -> bool:
    """코드명 형태인지. 미등록 코드도 True 여야 경고 경로가 살아난다."""
    return bool(_CHORD_PATTERN.match(token.strip()))


def name_fits(name: str, encoding: str = "cp949") -> bool:
    """GP5 의 22바이트 코드명 필드에 들어가는지."""
    return len(name.strip().encode(encoding, errors="replace")) <= MAX_NAME_BYTES


# 근음 이름 → 반음 값 (C=0). 이명동음은 같은 값으로 모인다.
_ROOTS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
SEMITONES = 12

# 코드 성질 → 근음 기준 반음 간격. 이름은 정확히 일치해야 한다 (접두어 매칭이
# 아니다) — 'm7b5' 가 'm7' 로 잘리면 감5도가 완전5도로 바뀐다.
_QUALITIES: dict[str, tuple[int, ...]] = {
    "": (0, 4, 7),
    "5": (0, 7),
    "6": (0, 4, 7, 9),
    "6/9": (0, 2, 4, 7, 9),
    "7": (0, 4, 7, 10),
    "7b5": (0, 4, 6, 10),
    "7#5": (0, 4, 8, 10),
    "7b9": (0, 1, 4, 7, 10),
    "7#9": (0, 3, 4, 7, 10),
    "7sus4": (0, 5, 7, 10),
    "9": (0, 2, 4, 7, 10),
    "11": (0, 2, 4, 5, 7, 10),
    "13": (0, 2, 4, 7, 9, 10),
    "add9": (0, 2, 4, 7),
    "aug": (0, 4, 8),
    "+": (0, 4, 8),
    "dim": (0, 3, 6),
    "dim7": (0, 3, 6, 9),
    "m": (0, 3, 7),
    "m6": (0, 3, 7, 9),
    "m7": (0, 3, 7, 10),
    "m7b5": (0, 3, 6, 10),
    "m9": (0, 2, 3, 7, 10),
    "madd9": (0, 2, 3, 7),
    "mmaj7": (0, 3, 7, 11),
    "maj7": (0, 4, 7, 11),
    "maj9": (0, 2, 4, 7, 11),
    "min": (0, 3, 7),
    "sus2": (0, 2, 7),
    "sus4": (0, 5, 7),
}


# 화음이라고 부를 최소 음 수. 이보다 적으면 근음 하나만 짚어도 어떤 코드든
# "부분집합" 이라 통과해버린다. 파워코드(5)처럼 성질 자체가 2음이면 그 수를 쓴다.
MIN_CHORD_TONES = 3

# 완전5도는 어느 코드에서든 생략한다 — 기타에서 가장 흔한 생략이다.
PERFECT_FIFTH = 7
# 5도 말고도 실제 연주에서 빼는 음. 확장 코드는 6줄에 다 담기지 않는다.
# 표준 C11 은 11도와 부딪치는 3도를 빼고, 표준 C13 은 9도를 뺀다.
_OPTIONAL_INTERVALS: dict[str, frozenset[int]] = {
    "11": frozenset({2, 4}),
    "13": frozenset({2}),
}


def _required_intervals(quality: str) -> frozenset[int]:
    """이름이 주장하는 음 중 반드시 울려야 하는 것.

    코드명이 7th·9th·6th 를 말하면 그 음이 없으면 다른 코드다 — B·D·F# 를
    `Bm7` 이라 부르면 7도(A)가 없으니 그냥 Bm 이다.
    """
    optional = _OPTIONAL_INTERVALS.get(quality, frozenset()) | {PERFECT_FIFTH}
    return frozenset(_QUALITIES[quality]) - optional


@dataclass(frozen=True)
class ChordSpec:
    """코드명을 반음 값으로 푼 결과."""

    root: int
    classes: frozenset[int]
    required: frozenset[int]        # 없으면 다른 코드가 되는 음
    bass: int | None = None         # 분수 코드의 베이스 (`G/B` 의 B)


def _note_value(text: str) -> int | None:
    """음 이름 하나를 반음 값으로. 코드명이 아니라 단음만 받는다."""
    text = text.strip()
    if not text or text[0] not in _ROOTS:
        return None
    value, rest = _ROOTS[text[0]], text[1:]
    if rest in ("#", "♯"):
        return (value + 1) % SEMITONES
    if rest in ("b", "♭"):
        return (value - 1) % SEMITONES
    return value % SEMITONES if not rest else None


def parse(name: str) -> ChordSpec | None:
    """코드명을 푼다. 성질을 모르면 None — 판정할 수 없다는 뜻이다."""
    text = name.strip()
    if not text or text[0] not in _ROOTS:
        return None
    root, rest = _ROOTS[text[0]], text[1:]
    if rest[:1] in ("#", "♯"):
        root, rest = root + 1, rest[1:]
    elif rest[:1] in ("b", "♭"):
        root, rest = root - 1, rest[1:]
    root %= SEMITONES

    # 성질 이름에 '/' 가 들어가는 것이 있다 ('6/9'). 통째로 먼저 맞춰본 뒤에
    # 분수 코드로 쪼갠다 — 무조건 쪼개면 '6/9' 항목이 영영 안 쓰인다.
    quality, bass = rest, None
    if quality not in _QUALITIES and "/" in rest:
        quality, _, bass_name = rest.rpartition("/")
        bass = _note_value(bass_name)
        if bass is None:
            return None
    intervals = _QUALITIES.get(quality)
    if intervals is None:
        return None
    classes = {(root + step) % SEMITONES for step in intervals}
    required = {(root + step) % SEMITONES for step in _required_intervals(quality)}
    if bass is not None:
        classes.add(bass)
        required.add(bass)
    return ChordSpec(root=root, classes=frozenset(classes),
                     required=frozenset(required), bass=bass)


def pitch_classes(name: str) -> frozenset[int] | None:
    """코드명이 쓰는 음(반음 값 집합). 성질을 모르면 None."""
    spec = parse(name)
    return None if spec is None else spec.classes


def voicing_pitch_classes(voicing, tuning) -> frozenset[int]:
    """(줄, 프렛) 보이싱이 실제로 내는 음의 반음 값 집합. string 1 = tuning[0]."""
    return frozenset((tuning[string - 1] + fret) % SEMITONES
                     for string, fret in voicing
                     if 1 <= string <= len(tuning))


def lowest_pitch_class(voicing, tuning) -> int | None:
    pitches = [tuning[string - 1] + fret for string, fret in voicing
               if 1 <= string <= len(tuning)]
    return min(pitches) % SEMITONES if pitches else None


def voicing_matches(name: str, voicing, tuning) -> bool | None:
    """보이싱이 코드명대로 소리 나는지. 성질을 모르면 None (판정 불가).

    네 가지를 본다:
    1. 코드에 없는 음을 내지 않는다 — 실측 예: Bm7 에 개방현 6개(EADGBE)
    2. 이름이 주장하는 음이 다 있다 — B·D·F# 는 Bm7 이 아니라 Bm 이다
    3. 화음이라 부를 만큼 음이 있다 — 부분집합만 보면 B 한 음이 Bm7 로 통과한다
    4. 분수 코드면 지정된 베이스가 최저음이다 (`C/E` 의 최저음은 E)

    5도 생략은 통과시킨다 — 기타에서 가장 흔한 생략이다. 확장 코드(11·13)에서
    실제로 빼는 음도 `_OPTIONAL_INTERVALS` 에 적어 통과시킨다.
    """
    spec = parse(name)
    if spec is None:
        return None
    played = voicing_pitch_classes(voicing, tuning)
    if not played <= spec.classes or not spec.required <= played:
        return False
    if len(played) < min(MIN_CHORD_TONES, len(spec.classes)):
        return False
    if spec.bass is not None and lowest_pitch_class(voicing, tuning) != spec.bass:
        return False
    return True


def voicing_for(name: str | None) -> tuple[tuple[int, int], ...] | None:
    """코드명의 보이싱. 모르는 코드는 None."""
    if name is None:
        return None
    return VOICINGS.get(name.strip())


def voicing_in(ir: dict, name: str | None) -> tuple[tuple[int, int], ...] | None:
    """손으로 검증한 표가 우선, 없으면 IR 에 실린 AI 보이싱.

    검증된 표를 AI 가 덮지 못하게 하는 우선순위가 여기 한 곳에만 있어야 한다 —
    build 와 corrections 가 각자 판단하면 언젠가 어긋난다.
    """
    verified = voicing_for(name)
    if verified is not None:
        return verified
    if name is None:
        return None
    proposed = ir.get("ai_voicings", {}).get(name.strip())
    if proposed is None:
        return None
    return tuple((string, fret) for string, fret in proposed)
