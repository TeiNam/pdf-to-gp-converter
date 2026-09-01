"""AI 보정안을 검증해 IR 에 적용한다. 네트워크도 프롬프트도 모른다.

이 모듈이 파이프라인의 안전 장치다. AI 는 무엇이든 제안할 수 있지만, 여기를
통과하지 못한 제안은 IR 에 닿지 못하고 이유와 함께 기록된다.

핵심 불변식 — **AI 는 가사를 날조할 수 없다.** 한 마디의 가사 재배치를 적용하기
전에 그 마디 음절을 beat 순서로 이어붙인 문자열이 원본과 **완전히 같은지** 확인한다.
다르면 그 마디의 가사 보정을 통째로 버린다. 즉 AI 는 음절을 다른 beat 로 옮길
수만 있고, 글자를 더하거나 빼거나 바꾸거나 **순서를 뒤집을 수도** 없다.

AI 가 준 값은 조회에 쓰기 **전에** 타입을 검사한다. `measure: []` 는 dict 조회에서
TypeError 로 변환 전체를 죽이고, `measure: true` 는 `True == 1` 이라 1번 마디를
가리키는 멀쩡한 인덱스가 되어 조용히 잘못된 마디를 고친다.

원본 IR 은 건드리지 않는다 — 새 dict 를 돌려준다.
"""

import collections
import copy
from dataclasses import dataclass

from . import chords

# GP5 에 파라미터 없이 그대로 담을 수 있는 연주법만 받는다. 트릴·트레몰로·꾸밈음은
# 프렛·박자 인자가 필요해서 제외했다 — 인자를 AI 에 맡기면 검증할 근거가 없다.
TECHNIQUE_KINDS = frozenset({
    "hammer",           # 해머온·풀오프. GP 는 둘을 한 플래그로 다룬다
    "slide",            # 다음 음으로 시프트 슬라이드
    "slide_out_down", "slide_out_up",
    "slide_in_below", "slide_in_above",
    "bend",             # 반음 벤드 (build 에서 기본 곡선을 만든다)
    "vibrato",
    "harmonic",         # 내추럴 하모닉스
    "palm_mute",
    "let_ring",
    "staccato",
    "dead",             # 뮤트 노트 'x'
    "accent",
    "heavy_accent",
    "ghost",
})

OPS = ("technique", "lyric", "chord", "voicing")

GUITAR_STRINGS = 6
MAX_FRET = 24
UNUSED_STRING = -1
# 사람 손이 짚을 수 없는 보이싱을 거른다. 하이코드 바레가 4프렛을 쓰므로 여유를 둔다
MAX_VOICING_SPAN = 6


@dataclass(frozen=True)
class Outcome:
    """적용 결과. `rejected` 항목은 {"correction": …, "reason": …} 다."""

    applied: tuple[dict, ...] = ()
    rejected: tuple[dict, ...] = ()
    # 새로 받은 보이싱으로 음을 채워 넣은 beat 수 (코드에서 음을 만드는 슬래시 beat)
    realized: int = 0


def _index_of(value, label: str) -> tuple[int | None, str | None]:
    """AI 가 준 값을 인덱스로 받는다.

    `bool` 은 `int` 의 하위형이라 따로 막는다 — `True` 를 그냥 두면 `True == 1`
    이라 1번 마디를 가리키는 멀쩡한 인덱스가 되어버린다.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None, f"{label} 이 정수가 아니다: {value!r}"
    return value, None


def _beat_at(measures: dict, correction: dict) -> tuple[dict | None, str | None]:
    """보정이 가리키는 beat. 못 찾으면 이유를 함께 돌려준다."""
    measure_index, reason = _index_of(correction.get("measure"), "measure")
    if reason:
        return None, reason
    measure = measures.get(measure_index)
    if measure is None:
        return None, f"마디 {measure_index} 가 없다"
    beat_index, reason = _index_of(correction.get("beat"), "beat")
    if reason:
        return None, reason
    if not 0 <= beat_index < len(measure["beats"]):
        return None, (f"마디 {measure['index']} 의 beat 는 "
                      f"0..{len(measure['beats']) - 1} 인데 {beat_index} 를 가리켰다")
    return measure["beats"][beat_index], None


def _apply_technique(measures: dict, correction: dict) -> str | None:
    """연주법을 beat 에 붙인다.

    `string` 을 생략하면 그 beat 의 **모든 음**에 걸린다. 악보 위·아래에 그려지는
    아티큘레이션(악센트·마르카토·스타카토)은 한 줄이 아니라 그 박 전체에 붙는
    표기다 — 줄을 요구하면 모델이 근거 없이 하나를 고르게 되고, 스트럼 화음
    6개 음 중 1개만 악센트가 되어 원본과 다른 악보가 나온다.
    """
    beat, reason = _beat_at(measures, correction)
    if reason:
        return reason
    kind = correction.get("kind")
    if not isinstance(kind, str) or kind not in TECHNIQUE_KINDS:
        return f"연주법 {kind!r} 을 GP5 로 옮길 수 없다"
    if not beat["notes"]:
        return "이 beat 에는 음이 없어 연주법을 붙일 곳이 없다"

    string = correction.get("string")
    if string is not None:
        string, reason = _index_of(string, "string")
        if reason:
            return reason
        if string not in {note["string"] for note in beat["notes"]}:
            return (f"이 beat 의 줄은 {sorted(n['string'] for n in beat['notes'])} "
                    f"인데 {string} 을 가리켰다")

    existing = beat.setdefault("techniques", [])
    where = f"{string}번줄" if string is not None else "beat 전체"
    if any(t["string"] == string and t["kind"] == kind for t in existing):
        return f"{where}에 {kind} 가 이미 있다"
    existing.append({"string": string, "kind": kind})
    return None


def _apply_chord(ir: dict, measures: dict, correction: dict,
                 claimed: set) -> str | None:
    """beat 의 코드명을 바꾼다.

    그 beat 의 음이 코드에서 만들어진 것(`from_chord`)이면 새 코드의 보이싱으로
    다시 만든다. 이름만 갈면 다이어그램은 새 코드인데 소리는 이전 코드가 난다.
    """
    beat, reason = _beat_at(measures, correction)
    if reason:
        return reason
    name = correction.get("name")
    if not isinstance(name, str) or not chords.looks_like_chord(name):
        return f"코드명 형태가 아니다: {name!r}"
    name = name.strip()
    key = (correction.get("measure"), correction.get("beat"))
    if key in claimed:
        return f"이 beat 에 이미 다른 코드 보정을 적용했다 — {name} 은 버린다"
    if beat.get("chord") == name:
        return f"이미 {name} 다"
    if beat.get("from_chord"):
        voicing = chords.voicing_in(ir, name)
        if voicing is None:
            return f"{name} 의 보이싱을 몰라 이 beat 의 음을 다시 만들 수 없다"
        beat["notes"] = [{"string": string, "fret": fret}
                         for string, fret in voicing]
    beat["chord"] = name
    claimed.add(key)
    return None


def _validate_frets(frets) -> str | None:
    if not isinstance(frets, list) or len(frets) != GUITAR_STRINGS:
        return f"frets 는 1번줄부터 {GUITAR_STRINGS}개 배열이어야 한다: {frets!r}"
    if any(not isinstance(f, int) or isinstance(f, bool) for f in frets):
        return f"frets 에 정수가 아닌 값이 있다: {frets!r}"
    if any(f != UNUSED_STRING and not 0 <= f <= MAX_FRET for f in frets):
        return f"프렛은 {UNUSED_STRING}(미사용) 또는 0..{MAX_FRET} 여야 한다: {frets!r}"
    used = [f for f in frets if f != UNUSED_STRING]
    if not used:
        return "전부 미사용이다"
    fretted = [f for f in used if f > 0]
    if fretted and max(fretted) - min(fretted) > MAX_VOICING_SPAN:
        return f"{max(fretted) - min(fretted)}프렛에 걸쳐 있어 짚을 수 없다: {frets!r}"
    return None


def _realize_chord_beats(ir: dict) -> int:
    """코드에서 음을 만드는 beat 중 비어 있던 것을 새로 알게 된 보이싱으로 채운다.

    추출 단계에서 보이싱을 모르는 코드는 음이 하나도 없는 beat 로 남는다 —
    GP 에서 무음이다. AI 가 보이싱을 알려줬으면 그때 소리가 나야 하는데,
    다이어그램만 그리고 넘어가면 `voicing` 보정이 아무 일도 하지 않는 셈이다.
    """
    filled = 0
    for measure in ir["measures"]:
        for beat in measure["beats"]:
            if beat["notes"] or not beat.get("from_chord") or not beat.get("chord"):
                continue
            voicing = chords.voicing_in(ir, beat["chord"])
            if voicing is None:
                continue
            beat["notes"] = [{"string": string, "fret": fret}
                             for string, fret in voicing]
            filled += 1
    return filled


def _apply_voicing(ir: dict, correction: dict) -> str | None:
    """모르는 코드의 보이싱을 채운다. 검증된 기존 보이싱은 덮지 않는다."""
    name = correction.get("name")
    if not isinstance(name, str) or not chords.looks_like_chord(name):
        return f"코드명 형태가 아니다: {name!r}"
    name = name.strip()
    if chords.voicing_for(name) is not None:
        return f"{name} 은 손으로 검증한 보이싱이 이미 있다"
    reason = _validate_frets(correction.get("frets"))
    if reason:
        return reason
    voicing = ir.setdefault("ai_voicings", {})
    if name in voicing:
        return f"{name} 보이싱을 이미 받았다"
    voicing[name] = [
        [string, fret]
        for string, fret in enumerate(correction["frets"], start=1)
        if fret != UNUSED_STRING
    ]
    return None


def _lyric_text(measure: dict) -> str:
    """마디의 가사를 beat 순서로 이어붙인 문자열."""
    return "".join(beat.get("lyric") or "" for beat in measure["beats"])


def _apply_lyric_group(measure: dict, group: list[dict]) -> str | None:
    """한 마디의 가사를 통째로 재배치한다. 글자열이 바뀌면 전부 버린다.

    다중집합이 아니라 **순서까지 포함한 문자열**로 비교한다. 다중집합만 보면
    '가나' 를 '나가' 로 뒤집어도 통과한다 — 가사는 순서가 뜻이다.

    부분 적용은 하지 않는다 — 절반만 옮겨진 가사는 원본보다 나쁘다.
    """
    placement: dict[int, str] = {}
    for correction in group:
        beat_index, reason = _index_of(correction.get("beat"), "beat")
        if reason:
            return reason
        if not 0 <= beat_index < len(measure["beats"]):
            return (f"beat 는 0..{len(measure['beats']) - 1} 인데 "
                    f"{beat_index} 를 가리켰다")
        text = correction.get("text")
        if text is not None and not isinstance(text, str):
            return f"text 가 문자열이 아니다: {text!r}"
        placement[beat_index] = placement.get(beat_index, "") + (text or "")

    before = _lyric_text(measure)
    proposed = "".join(placement.get(position, "")
                       for position in range(len(measure["beats"])))
    if proposed != before:
        return (f"음절이 바뀌었다 — 원본 {before!r} → 제안 {proposed!r}. "
                f"가사 재배치는 그 마디의 음절을 순서 그대로 다시 나열해야 한다")

    for beat_index, beat in enumerate(measure["beats"]):
        beat["lyric"] = placement.get(beat_index) or None
    return None


def _reject(correction: dict, reason: str) -> dict:
    return {"correction": correction, "reason": reason}


def _lyric_key(correction: dict) -> object:
    """가사 보정을 마디별로 묶는 키. 해시 못 하는 값은 문자열로 눕힌다."""
    measure = correction.get("measure")
    return measure if isinstance(measure, (int, str, type(None))) else repr(measure)


def apply_corrections(ir: dict, proposals: list[dict]) -> tuple[dict, Outcome]:
    """보정안을 검증해 새 IR 을 만든다. 원본은 그대로 둔다.

    `voicing` 을 먼저 처리한다 — 코드명 보정이 새 보이싱으로 음을 다시 만들 수
    있어야 하고, 순서가 뒤면 같은 배치 안에서도 실패한다.

    가사 보정은 마디 단위로 모아 원자적으로 적용한다. 나머지는 건별이다.
    """
    result = copy.deepcopy(ir)
    measures = {measure["index"]: measure for measure in result["measures"]}
    applied: list[dict] = []
    rejected: list[dict] = []
    chord_claims: set = set()

    lyric_groups: dict[object, list[dict]] = collections.defaultdict(list)
    ordered = sorted(
        proposals,
        key=lambda c: 0 if isinstance(c, dict) and c.get("op") == "voicing" else 1)
    for correction in ordered:
        if not isinstance(correction, dict):
            rejected.append(_reject({"raw": correction}, "객체가 아니다"))
            continue
        op = correction.get("op")
        if op == "lyric":
            lyric_groups[_lyric_key(correction)].append(correction)
            continue
        if op == "technique":
            reason = _apply_technique(measures, correction)
        elif op == "chord":
            reason = _apply_chord(result, measures, correction, chord_claims)
        elif op == "voicing":
            reason = _apply_voicing(result, correction)
        else:
            reason = f"op {op!r} 를 모른다. {' | '.join(OPS)} 중 하나여야 한다"
        if reason:
            rejected.append(_reject(correction, reason))
        else:
            applied.append(correction)

    for group in lyric_groups.values():
        measure_index, reason = _index_of(group[0].get("measure"), "measure")
        measure = None if reason else measures.get(measure_index)
        if measure is None:
            reason = reason or f"마디 {measure_index} 가 없다"
        else:
            # 원본을 복사해 시험 적용한다 — 실패 시 마디가 반쯤 바뀌면 안 된다
            trial = copy.deepcopy(measure)
            reason = _apply_lyric_group(trial, group)
            if reason is None:
                measure["beats"] = trial["beats"]
        if reason:
            rejected.extend(_reject(correction, reason) for correction in group)
        else:
            applied.extend(group)

    realized = _realize_chord_beats(result)
    return result, Outcome(tuple(applied), tuple(rejected), realized)
