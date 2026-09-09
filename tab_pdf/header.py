"""악보 머리글(크레디트·조성·리듬·카포)과 튜닝 표기 해석."""

import re

from . import chords

STANDARD_TUNING = (64, 59, 55, 50, 45, 40)      # 1=고음 E … 6=저음 E

# 악보 머리글의 한국어 항목 라벨 → IR 필드. 글리프 스트림에 공백이 없어 라벨을
# 접두어로 떼어낸다 ('노래중식이' → 노래 + 중식이).
HEADER_LABELS = {
    "노래": "artist",
    "작사": "words",
    "작곡": "music",
    "편곡": "arranger",
    "연주": "performer",
}
KEY_LABEL = "Key:"
# 머리글의 카포 표기 — 글리프 스트림에 공백이 없어 'Capo3'·'카포:3' 형태다
CAPO_PATTERN = re.compile(r"(?i)(?:capo|카포)\D{0,3}(\d{1,2})")
# 튜닝 문자열의 음 이름 하나 (Eb·F# 지원)
TUNING_NOTE = re.compile(r"[A-Ga-g][#b♯♭]?")
# 조성 표기가 붙는 리듬·연주 지시 줄을 알아보는 낱말. 라벨이 없는 자유 문구라
# 내용으로 판정한다 — 'Slow 16Beat', '16 Beat', 'Shuffle' 따위.
RHYTHM_WORDS = ("beat", "shuffle", "swing", "slow", "waltz", "ballad", "bounce")


def parse_tuning(text: str) -> list[int]:
    """'DADGBE'·'Eb Ab Db Gb Bb Eb' 같은 저음→고음 표기를 MIDI 목록으로.

    옥타브 표기가 없으므로 각 줄을 표준 튜닝의 그 줄에서 가장 가까운
    옥타브로 푼다 — Drop-D·반음 내림 같은 실제 대체 튜닝을 전부 덮는다.
    돌려주는 순서는 IR 관례대로 1번줄(고음)부터다.
    """
    names = TUNING_NOTE.findall(text)
    if len(names) != len(STANDARD_TUNING):
        raise ValueError(
            f"튜닝은 저음→고음 6개 음 이름이어야 합니다 (예: DADGBE): {text!r}")
    result = []
    for name, standard in zip(reversed(names), STANDARD_TUNING):
        pitch_class = chords._note_value(name.upper()[0] + name[1:])
        if pitch_class is None:
            raise ValueError(f"음 이름을 해석할 수 없습니다: {name!r}")
        candidates = range(pitch_class, 128, chords.SEMITONES)
        result.append(min(candidates, key=lambda value: abs(value - standard)))
    return result


def header_lines(geo, system) -> list[str]:
    """첫 시스템 위쪽 머리글을 baseline 별 한 줄씩 돌려준다. 위→아래 순서."""
    above = sorted((g for g in geo.glyphs if g.y < system.melody_ys[0]),
                   key=lambda g: (round(g.y, 1), g.x))
    lines, current, previous_y = [], [], None
    for glyph in above:
        y = round(glyph.y, 1)
        if previous_y is not None and y != previous_y:
            lines.append("".join(current))
            current = []
        current.append(glyph.char)
        previous_y = y
    if current:
        lines.append("".join(current))
    return lines


def header_fields(geo, system) -> dict[str, str]:
    """머리글에서 크레디트·조성·카포·리듬 표기를 뽑는다.

    조판이 항목을 라벨+값으로 붙여 쓰므로 (`작사정중식`) 라벨을 접두어로 떼어낸다.
    라벨이 없는 리듬 지시(`Slow 16Beat`)는 낱말로 알아본다.
    """
    fields: dict[str, str] = {}
    for line in header_lines(geo, system):
        text = line.strip()
        if not text:
            continue
        for label, key in HEADER_LABELS.items():
            if text.startswith(label) and len(text) > len(label):
                fields.setdefault(key, text[len(label):].strip())
                break
        else:
            capo = CAPO_PATTERN.search(text)
            if capo is not None:
                fields.setdefault("capo", capo.group(1))
            if text.startswith(KEY_LABEL):
                fields.setdefault("key", text[len(KEY_LABEL):].strip())
            elif any(word in text.lower() for word in RHYTHM_WORDS):
                fields.setdefault("rhythm", text)
    return fields
