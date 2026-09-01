"""코드네임 → 기타 프렛 보이싱.

이 곡(`나는반딧불`)에 실제로 쓰인 5개만 넣는다. 표에 없으면 None 을 돌려 호출자가
경고를 남기게 한다 — 추측으로 채우면 틀린 악보가 조용히 나온다.
string 1 = 고음 E, 6 = 저음 E. fret 0 = 개방현.
"""

import re

# 코드명 후보 판정. A~G 로 시작해야 하므로 'H'(해머온)·'2'/'3'(페이지 번호)는 걸러지고,
# 미등록 코드('Bm7')는 통과해 unknown_chord 경고 경로가 살아난다.
_CHORD_PATTERN = re.compile(r"^[A-G](?:[#b])?[A-Za-z0-9#b/+()-]*$")

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
