"""코드네임 → 기타 프렛 보이싱.

이 곡(`나는반딧불`)에 실제로 쓰인 5개만 넣는다. 표에 없으면 None 을 돌려 호출자가
경고를 남기게 한다 — 추측으로 채우면 틀린 악보가 조용히 나온다.
string 1 = 고음 E, 6 = 저음 E. fret 0 = 개방현.
"""

import re

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

# 코드 성질 → 근음 기준 반음 간격. 긴 이름이 먼저 매치되도록 정렬해서 쓴다
# ('m7b5' 가 'm7' 로 잘리면 안 된다).
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
_QUALITY_ORDER = tuple(sorted(_QUALITIES, key=len, reverse=True))


def pitch_classes(name: str) -> frozenset[int] | None:
    """코드명이 쓰는 음(반음 값 집합). 성질을 모르면 None.

    None 은 "틀렸다" 가 아니라 "판정할 수 없다" 다 — 호출자는 검증을 건너뛴다.
    표에 없는 성질을 틀렸다고 처리하면 멀쩡한 코드를 거부한다.
    """
    text = name.strip()
    if not text or text[0] not in _ROOTS:
        return None
    root = _ROOTS[text[0]]
    rest = text[1:]
    if rest[:1] == "#":
        root, rest = root + 1, rest[1:]
    elif rest[:1] == "b":
        root, rest = root - 1, rest[1:]
    # 분수 코드의 베이스는 근음 위 어딘가의 코드 톤이거나 별개 음이다.
    # 성질 판정에는 쓰지 않되, 그 음을 허용 집합에 넣어준다.
    quality, _, bass = rest.partition("/")
    intervals = next((_QUALITIES[q] for q in _QUALITY_ORDER if quality == q), None)
    if intervals is None:
        return None
    classes = {(root + step) % SEMITONES for step in intervals}
    if bass:
        bass_class = pitch_classes(bass)
        if bass_class is None:
            return None
        classes |= bass_class
    return frozenset(classes)


def voicing_pitch_classes(voicing, tuning) -> frozenset[int]:
    """(줄, 프렛) 보이싱이 실제로 내는 음의 반음 값 집합. string 1 = tuning[0]."""
    return frozenset((tuning[string - 1] + fret) % SEMITONES
                     for string, fret in voicing
                     if 1 <= string <= len(tuning))


def voicing_matches(name: str, voicing, tuning) -> bool | None:
    """보이싱이 코드명의 음만 내는지. 판정할 수 없으면 None.

    코드 톤을 빼먹은 보이싱(기타에서 흔하다)은 통과시키고, **코드에 없는 음을
    내는** 보이싱만 걸러낸다. AI 가 이름과 무관한 프렛을 줘도 검증할 수 없으면
    악보가 조용히 틀리기 때문이다 — 실측 예: Bm7 에 개방현 6개(EADGBE).
    """
    expected = pitch_classes(name)
    if expected is None:
        return None
    return voicing_pitch_classes(voicing, tuning) <= expected


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
