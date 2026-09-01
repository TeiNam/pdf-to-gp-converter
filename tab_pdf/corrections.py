"""AI 보정안을 검증해 IR 에 적용한다. 네트워크도 프롬프트도 모른다.

이 모듈이 파이프라인의 안전 장치다. AI 는 무엇이든 제안할 수 있지만, 여기를
통과하지 못한 제안은 IR 에 닿지 못하고 이유와 함께 기록된다.

핵심 불변식 — **AI 는 가사를 날조할 수 없다.** 한 마디의 가사 재배치를 적용하기
전에 그 마디 음절의 다중집합(multiset)이 원본과 같은지 확인한다. 다르면 그
마디의 가사 보정을 통째로 버린다. 즉 AI 는 음절을 다른 beat 로 옮길 수만 있고,
글자를 더하거나 빼거나 바꿀 수는 없다.

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

    @property
    def counts(self) -> dict[str, int]:
        return {"applied": len(self.applied), "rejected": len(self.rejected)}

    def by_op(self) -> dict[str, int]:
        return dict(collections.Counter(c.get("op", "?") for c in self.applied))


def _beat_at(measures: dict, correction: dict) -> tuple[dict | None, str | None]:
    """보정이 가리키는 beat. 못 찾으면 이유를 함께 돌려준다."""
    measure = measures.get(correction.get("measure"))
    if measure is None:
        return None, f"마디 {correction.get('measure')!r} 가 없다"
    beat_index = correction.get("beat")
    if not isinstance(beat_index, int) or isinstance(beat_index, bool):
        return None, f"beat 이 정수가 아니다: {beat_index!r}"
    if not 0 <= beat_index < len(measure["beats"]):
        return None, (f"마디 {measure['index']} 의 beat 는 "
                      f"0..{len(measure['beats']) - 1} 인데 {beat_index} 를 가리켰다")
    return measure["beats"][beat_index], None


def _apply_technique(measures: dict, correction: dict) -> str | None:
    beat, reason = _beat_at(measures, correction)
    if reason:
        return reason
    kind = correction.get("kind")
    if kind not in TECHNIQUE_KINDS:
        return f"연주법 {kind!r} 을 GP5 로 옮길 수 없다"
    string = correction.get("string")
    if string not in {note["string"] for note in beat["notes"]}:
        return (f"이 beat 의 줄은 "
                f"{sorted(n['string'] for n in beat['notes'])} 인데 {string!r} 을 가리켰다")
    existing = beat.setdefault("techniques", [])
    if any(t["string"] == string and t["kind"] == kind for t in existing):
        return f"{string}번줄에 {kind} 가 이미 있다"
    existing.append({"string": string, "kind": kind})
    return None


def _apply_chord(measures: dict, correction: dict) -> str | None:
    beat, reason = _beat_at(measures, correction)
    if reason:
        return reason
    name = correction.get("name")
    if not isinstance(name, str) or not chords.looks_like_chord(name):
        return f"코드명 형태가 아니다: {name!r}"
    if beat.get("chord") == name.strip():
        return f"이미 {name} 다"
    beat["chord"] = name.strip()
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


def _syllable_counter(measure: dict) -> collections.Counter:
    return collections.Counter(
        "".join(beat.get("lyric") or "" for beat in measure["beats"]))


def _apply_lyric_group(measure: dict, group: list[dict]) -> str | None:
    """한 마디의 가사를 통째로 재배치한다. 다중집합이 바뀌면 전부 버린다.

    부분 적용은 하지 않는다 — 절반만 옮겨진 가사는 원본보다 나쁘다.
    """
    placement: dict[int, str] = {}
    for correction in group:
        beat_index = correction.get("beat")
        if not isinstance(beat_index, int) or isinstance(beat_index, bool):
            return f"beat 이 정수가 아니다: {beat_index!r}"
        if not 0 <= beat_index < len(measure["beats"]):
            return (f"beat 는 0..{len(measure['beats']) - 1} 인데 "
                    f"{beat_index} 를 가리켰다")
        text = correction.get("text")
        if text is not None and not isinstance(text, str):
            return f"text 가 문자열이 아니다: {text!r}"
        placement[beat_index] = (placement.get(beat_index, "") + (text or ""))

    before = _syllable_counter(measure)
    proposed = collections.Counter("".join(placement.values()))
    if proposed != before:
        missing = "".join((before - proposed).elements())
        added = "".join((proposed - before).elements())
        return (f"음절 다중집합이 바뀌었다 — 사라짐 {missing!r}, 새로 생김 {added!r}. "
                f"가사 재배치는 그 마디의 모든 음절을 다시 나열해야 한다")

    for beat_index, beat in enumerate(measure["beats"]):
        beat["lyric"] = placement.get(beat_index) or None
    return None


def _reject(correction: dict, reason: str) -> dict:
    return {"correction": correction, "reason": reason}


def apply_corrections(ir: dict, proposals: list[dict]) -> tuple[dict, Outcome]:
    """보정안을 검증해 새 IR 을 만든다. 원본은 그대로 둔다.

    가사 보정은 마디 단위로 모아 원자적으로 적용한다. 나머지는 건별이다.
    """
    result = copy.deepcopy(ir)
    measures = {measure["index"]: measure for measure in result["measures"]}
    applied: list[dict] = []
    rejected: list[dict] = []

    lyric_groups: dict[object, list[dict]] = collections.defaultdict(list)
    for correction in proposals:
        if not isinstance(correction, dict):
            rejected.append(_reject({"raw": correction}, "객체가 아니다"))
            continue
        op = correction.get("op")
        if op == "lyric":
            lyric_groups[correction.get("measure")].append(correction)
            continue
        if op == "technique":
            reason = _apply_technique(measures, correction)
        elif op == "chord":
            reason = _apply_chord(measures, correction)
        elif op == "voicing":
            reason = _apply_voicing(result, correction)
        else:
            reason = f"op {op!r} 를 모른다. {' | '.join(OPS)} 중 하나여야 한다"
        if reason:
            rejected.append(_reject(correction, reason))
        else:
            applied.append(correction)

    for measure_index, group in lyric_groups.items():
        measure = measures.get(measure_index)
        if measure is None:
            reason = f"마디 {measure_index!r} 가 없다"
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

    return result, Outcome(tuple(applied), tuple(rejected))
