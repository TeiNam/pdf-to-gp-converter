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
# 타브의 한 줄에 붙는 표기 — 대상 줄이 반드시 있어야 한다. 박 전체에 걸면
# 스트럼 6음을 모두 벤딩하는 식의 말이 안 되는 악보가 된다.
STRING_TECHNIQUES = frozenset({
    "hammer",           # 해머온·풀오프. GP 는 둘을 한 플래그로 다룬다
    "slide",            # 다음 음으로 시프트 슬라이드
    "slide_out_down", "slide_out_up",
    "slide_in_below", "slide_in_above",
    "bend",             # 반음 벤드 (build 에서 기본 곡선을 만든다)
    "harmonic",         # 내추럴 하모닉스
})
# 박 전체에 걸릴 수 있는 표기 — `string` 을 생략하면 그 beat 의 모든 음에 붙는다.
# 악보 위·아래에 그려지는 아티큘레이션과 주법 지시가 여기 든다.
BEAT_TECHNIQUES = frozenset({
    "vibrato",
    "palm_mute",
    "let_ring",
    "staccato",
    "dead",             # 뮤트 노트 'x' — 한 줄에도, 6현 스트럼 전체에도 쓴다
    "accent",
    "heavy_accent",
    "ghost",
})
# 이 둘은 악보 위·아래에 그려지는 표기라 대상 줄이라는 개념이 아예 없다.
# `string` 을 받아주면 모델이 화음 중 하나를 짐작해 고른다 — 실측에서 악센트 44개가
# 1·3·4·5·6번줄로 흩어져 스트럼 6음 중 1음만 악센트가 됐다.
BEAT_ONLY_TECHNIQUES = frozenset({"accent", "heavy_accent"})
TECHNIQUE_KINDS = STRING_TECHNIQUES | BEAT_TECHNIQUES

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
    if string is None:
        if kind in STRING_TECHNIQUES:
            return (f"{kind} 는 한 줄에만 걸리는 표기다 — string 을 지정해야 한다 "
                    f"(박 전체로 걸 수 있는 것: {', '.join(sorted(BEAT_TECHNIQUES))})")
    else:
        if kind in BEAT_ONLY_TECHNIQUES:
            return (f"{kind} 는 악보 위·아래에 그려지는 박 단위 표기다 — string 을 "
                    f"짐작해서 붙이면 화음 중 한 음만 표시된다")
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
    name, reason = _chord_name(correction)
    if reason:
        return reason
    key = (correction.get("measure"), correction.get("beat"))
    if key in claimed:
        return f"이 beat 에 이미 다른 코드 보정을 적용했다 — {name} 은 버린다"
    if beat.get("chord") == name:
        return f"이미 {name} 다"
    # 보이싱이 없으면 build 가 다이어그램을 못 만들어 .gp5 에 코드가 아예 안 남는다.
    # IR 에만 남는 이름은 사용자에게 보이지 않으므로 여기서 거절해 경고로 드러낸다.
    voicing = chords.voicing_in(ir, name)
    if voicing is None:
        return (f"{name} 의 보이싱을 몰라 .gp5 에 표시할 수 없다 "
                f"— voicing 보정을 같이 내거나 chords.VOICINGS 에 넣어야 한다")
    if beat.get("from_chord"):
        # 음을 갈아치우면 이미 붙어 있던 연주법이 없는 줄을 가리킬 수 있다.
        # build 는 그런 연주법을 조용히 버리므로, 지우지 말고 보정을 거절한다 —
        # 무엇을 잃는지 사용자가 보고 결정해야 한다.
        strings = {string for string, _ in voicing}
        orphaned = [t for t in beat.get("techniques", ())
                    if t.get("string") is not None and t["string"] not in strings]
        if orphaned:
            kinds = ", ".join(f"{t['string']}번줄 {t['kind']}" for t in orphaned)
            return (f"{name} 으로 바꾸면 {kinds} 가 붙을 음이 없어진다 "
                    f"— 연주법을 버리지 않으려고 코드명 보정을 거절한다")
        beat["notes"] = [{"string": string, "fret": fret}
                         for string, fret in voicing]
    beat["chord"] = name
    claimed.add(key)
    return None


def _chord_name(correction: dict) -> tuple[str | None, str | None]:
    name = correction.get("name")
    if not isinstance(name, str) or not chords.looks_like_chord(name):
        return None, f"코드명 형태가 아니다: {name!r}"
    if not chords.name_fits(name):
        return None, (f"코드명이 GP5 의 {chords.MAX_NAME_BYTES}바이트 필드보다 길다 "
                      f"— 조용히 잘린다: {name!r}")
    return name.strip(), None


def _voicing_mismatch(name: str, voicing, tuning) -> str:
    """왜 안 맞는지 구체적으로 말한다 — '틀렸다' 만으론 고칠 수 없다.

    검사 순서는 `chords.voicing_matches` 와 같아야 한다. 어긋나면 실제로 걸린
    이유가 아닌 항목을 지목해 "최저음은 4여야 하는데 4다" 같은 모순이 나온다.
    """
    spec = chords.parse(name)
    played = chords.voicing_pitch_classes(voicing, tuning)
    extra = sorted(played - spec.classes)
    if extra:
        return f"이 프렛은 {name} 에 없는 음을 낸다 (반음값 {extra})"
    missing = sorted(spec.required - played)
    if missing:
        return (f"{name} 이 주장하는 음이 빠졌다 (반음값 {missing}) "
                f"— 다른 코드가 된다")
    lowest = chords.lowest_pitch_class(voicing, tuning)
    return (f"{name} 의 최저음은 반음값 {spec.bass} 여야 하는데 {lowest} 다")


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
    name, reason = _chord_name(correction)
    if reason:
        return reason
    if chords.voicing_for(name) is not None:
        return f"{name} 은 손으로 검증한 보이싱이 이미 있다"
    reason = _validate_frets(correction.get("frets"))
    if reason:
        return reason
    proposed = [[string, fret]
                for string, fret in enumerate(correction["frets"], start=1)
                if fret != UNUSED_STRING]
    # 이름과 프렛이 맞는지 음정으로 확인한다. 이름만 믿으면 악보가 조용히 틀린다
    verdict = chords.voicing_matches(name, proposed, ir["tuning"])
    if verdict is None:
        # 검증할 수 없는 것은 통과시키지 않는다. 신뢰 경계 밖의 입력에서
        # "판정 불가" 를 허용으로 읽으면 정규식만 통과하는 이름(`Cfoo`)에
        # 아무 프렛이나 붙여 검증을 통째로 우회할 수 있다.
        return (f"{name} 의 코드 성질을 몰라 보이싱을 검증할 수 없다 "
                f"— chords._QUALITIES 에 넣어야 받는다")
    if verdict is False:
        return _voicing_mismatch(name, proposed, ir["tuning"])
    voicing = ir.setdefault("ai_voicings", {})
    if name in voicing:
        return f"{name} 보이싱을 이미 받았다"
    voicing[name] = proposed
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


def apply_corrections(ir: dict, proposals: list[dict]) -> tuple[dict, Outcome]:
    """보정안을 검증해 새 IR 을 만든다. 원본은 그대로 둔다.

    처리 순서가 결과를 바꾼다:
    1. `voicing` — 코드명 보정이 새 보이싱으로 음을 다시 만들 수 있어야 한다
    2. 보이싱으로 무음 beat 를 채운다 — 그래야 그 beat 의 연주법이 통과한다
    3. 나머지 보정 (건별)
    4. 가사 (마디 단위 원자적)
    5. 다시 채우기 + 고아 연주법 정리 — 3에서 음이 바뀐 beat 를 수습한다
    """
    result = copy.deepcopy(ir)
    measures = {measure["index"]: measure for measure in result["measures"]}
    applied: list[dict] = []
    rejected: list[dict] = []
    chord_claims: set = set()

    others, lyric_groups, malformed = _sort_proposals(proposals)
    rejected.extend(malformed)

    for correction in (c for c in others if c.get("op") == "voicing"):
        reason = _apply_voicing(result, correction)
        (rejected.append(_reject(correction, reason)) if reason
         else applied.append(correction))
    realized = _realize_chord_beats(result)

    # chord 를 technique 보다 먼저 본다 — 코드명 보정이 beat 에 음을 만들어줄 수
    # 있어서, 순서가 뒤면 같은 입력이 순서에 따라 다른 결과를 낸다.
    for correction in (c for c in others if c.get("op") == "chord"):
        reason = _apply_chord(result, measures, correction, chord_claims)
        (rejected.append(_reject(correction, reason)) if reason
         else applied.append(correction))

    for correction in others:
        op = correction.get("op")
        if op in ("voicing", "chord"):
            continue
        if op == "technique":
            reason = _apply_technique(measures, correction)
        else:
            reason = f"op {op!r} 를 모른다. {' | '.join(OPS)} 중 하나여야 한다"
        if reason:
            rejected.append(_reject(correction, reason))
        else:
            applied.append(correction)

    for measure_index, group in lyric_groups.items():
        measure = measures.get(measure_index)
        if measure is None:
            reason = f"마디 {measure_index} 가 없다"
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

    realized += _realize_chord_beats(result)
    return result, Outcome(tuple(applied), tuple(rejected), realized)


def _sort_proposals(proposals: list) -> tuple[list[dict], dict, list[dict]]:
    """보정안을 (건별, 마디별 가사 그룹, 형식 불량) 으로 가른다.

    가사 그룹의 키는 **검증을 통과한 정수**여야 한다. `measure` 를 그대로 키로
    쓰면 `True` 와 `1` 이 같은 키가 되어 (`hash(True) == hash(1)`) boolean 보정이
    멀쩡한 마디의 그룹에 섞여 들어간다.
    """
    others: list[dict] = []
    groups: dict[int, list[dict]] = collections.defaultdict(list)
    malformed: list[dict] = []
    for correction in proposals:
        if not isinstance(correction, dict):
            malformed.append(_reject({"raw": correction}, "객체가 아니다"))
        elif correction.get("op") != "lyric":
            others.append(correction)
        else:
            measure_index, reason = _index_of(correction.get("measure"), "measure")
            if reason:
                malformed.append(_reject(correction, reason))
            else:
                groups[measure_index].append(correction)
    return others, groups, malformed
