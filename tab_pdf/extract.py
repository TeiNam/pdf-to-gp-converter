"""PDF 기하를 음악적 중간표현(IR)으로 해석한다.

프렛·코드·주석 글리프는 glyphs, 대역·스냅은 bands, 진행·반복·박자표·
아티큘레이션·타이·꾸밈음은 marks, 가사는 lyrics, 머리글·튜닝은 header가 맡는다.
여기는 마디를 세우고 IR로 조립하는 뼈대다.
"""

import math
import os
from dataclasses import dataclass, field, replace

import pymupdf

from . import (
    bands,
    chords,
    durations,
    geometry,
    header,
    lyrics,
    marks,
    rhythm,
    smufl,
    timeline,
)
from .bands import CHORD_BAND_HEIGHT
from .durations import REST_DURATIONS, REST_NAMES
from .header import STANDARD_TUNING
# 기존 호출부와 테스트가 쓰는 이름을 유지한다.
from .glyphs import (
    MAX_FRET_GLYPH_SIZE, GRACE_SIZE_MIN_DELTA, TUPLET_3, BEAT_CLUSTER_TOLERANCE,
    CHORD_CHAR_GAP, CHORD_APPLY_SLACK, STROKE_X_WINDOW, SAME_BASELINE_TOLERANCE,
    LETTER_ADJACENCY_GAP, KERNED_DIGIT_INK_GAP, MAX_FRET, KERNED_DIGIT_ORIGIN_GAP,
    SMUFL_SLASH_RANGE, SMUFL_ARTICULATION_RANGE, SMUFL_TIMESIG_DIGIT_RANGE,
    TECHNIQUE_GLYPHS, PRIVATE_USE_RANGE, SMUFL_REST_RANGE, X_NOTEHEAD,
    SMUFL_STROKE_DOWN, SMUFL_STROKE_UP, _in_range, build_letter_index,
    _has_adjacent_letter, _grace_cutoff, _fret_glyphs, _grace_glyphs,
    _merge_two_digit_frets, _slash_xs, _dead_glyphs, _rest_glyphs, CHORD_ROW_TOLERANCE,
    _chord_char, _chord_tokens, _chord_at, _stroke_at, _techniques, _annotation_glyphs,
    _cluster, _classify, _UNSUPPORTED_TAB_RANGES, _warn_unsupported, _beat_notes,
)

# CLI·테스트가 extract 이름으로 부르는 재수출
parse_tuning = header.parse_tuning
LYRIC_MIN_CODEPOINT = lyrics.LYRIC_MIN_CODEPOINT
ARTICULATION_KINDS = marks.ARTICULATION_KINDS

DEFAULT_TEMPO = 80
DEFAULT_TIME_SIG = (4, 4)

# 쉼표 오른쪽 이 거리 안의 붙임점은 그 쉼표의 것이다 (pt)
REST_DOT_WINDOW = 10.0
# 붙임점이 그 쉼표의 것이라고 볼 세로 거리 (pt) — 다른 줄의 점음표 점과 가른다
REST_DOT_Y_TOLERANCE = 4.0
# 첫·끝 마디의 추정 길이가 박자표의 이 비율 미만이면 못갖춘마디로 본다.
# 정상 마디의 폭 변동(justification)은 ±15% 안이라 0.75 문턱에 오탐이 없다.
PICKUP_MAX_RATIO = 0.75


class NotATabPdf(ValueError):
    """타브 악보로 해석할 수 없는 입력."""


@dataclass
class _Warnings:
    items: list[dict] = field(default_factory=list)

    def add(self, measure: int, kind: str, detail: str) -> None:
        self.items.append({"measure": measure, "kind": kind, "detail": detail})


# 분리된 모듈의 이름을 기존 내부명으로 되묶는다 — 호출부와 테스트가 그대로 쓴다
_in_tab_band = bands.in_tab_band
_snap_to_string = bands.snap_to_string
_band_of = bands.band_of
_nearest_beat = bands.nearest_beat
_glyph_directions = marks.glyph_directions
_text_directions = marks.text_directions
_repeat_flags = marks.repeat_flags
_articulations = marks.articulations
_attach_graces = marks.attach_graces
_apply_tie_curves = marks.apply_tie_curves
_detect_time_signatures = marks.detect_time_signatures
_is_lyric_syllable = lyrics.is_syllable
_lyric_syllables = lyrics.syllable_rows
_assign_lyrics = lyrics.assign
_header_fields = header.header_fields


def _set_row_chord(beat: dict, name: str, index: int, warn, tuning) -> None:
    beat["chord"] = name
    if chords.voicing_for(name, tuning) is None:
        # 다이어그램은 보이싱이 있어야 만들어진다 — 조용히 사라지지 않게
        # 남긴다. AI 보정(voicing)이 채우면 build 가 그때 그린다.
        warn.add(index, "chord_no_voicing",
                 f"{name} 의 보이싱을 몰라 다이어그램을 만들 수 없다 "
                 f"— .gp5 에 이 코드 표기가 보이지 않는다")


def _assign_row_chords(beats: list[dict], tokens, bounds, index, warn, tuning,
                       pending: str | None = None) -> str | None:
    """코드 행 토큰을 표기 위치의 beat 에 배정한다. 이월분을 돌려준다.

    슬래시 beat 은 from_chord 경로가 이미 이름을 갖는다. 프렛 beat 은 음은
    정확한데 코드 표기가 .gp5 에 전혀 남지 않았다 (실측 47개 토큰 소실) —
    표기 위치의 첫 beat 이 **같은 이름을 이미 갖고 있으면 그 토큰은 표현된
    것**이다 (뒤 beat 으로 흘려보내면 코드가 밀린다). 아니면 표기 x 이후 첫
    미배정 beat 에 이름만 붙인다. 음은 건드리지 않는다.

    이 악보의 조판은 마디 마지막 코드를 모든 beat 보다 뒤(마디선 직전)에
    표기해 **다음 마디 첫 beat** 을 선행 지시한다 (실측: Cadd9 가 마지막
    beat 보다 9~12pt 뒤). 그런 토큰은 버리지 않고 다음 마디로 넘긴다.
    이월분은 자기 토큰이 배정된 뒤에 보고, 첫 beat 이 이미 표기를 가졌으면
    낡은 선행 표기로 보고 버린다.
    """
    sounding = [b for b in beats if not b.get("rest")]
    leftover = None
    x0, x1 = bounds
    for token_x, name in tokens:
        if not (x0 <= token_x < x1):
            continue
        anchor = next((i for i, b in enumerate(sounding)
                       if b["x"] >= token_x - CHORD_APPLY_SLACK), None)
        if anchor is None:
            leftover = name         # 마디 끝 선행 표기 — 다음 마디 몫이다
            continue
        if sounding[anchor].get("chord") == name:
            continue                # 슬래시 경로가 이미 이 자리에서 표현했다
        target = next((b for b in sounding[anchor:]
                       if b.get("chord") is None), None)
        if target is None:
            leftover = name
            continue
        _set_row_chord(target, name, index, warn, tuning)
    if pending is not None:
        if sounding and sounding[0].get("chord") is None:
            _set_row_chord(sounding[0], pending, index, warn, tuning)
        elif not sounding and leftover is None:
            # 쉼표뿐인 마디 — 이월분을 삼키지 말고 다음 마디로 계속 넘긴다
            leftover = pending
    return leftover


def _duration_fields(duration: durations.LegalDuration) -> dict:
    return {"duration": duration.value, "dotted": duration.dotted,
            "tuplet": list(duration.tuplet) if duration.tuplet else None}


def _rest_beat(x: float, duration: durations.LegalDuration) -> dict:
    return {"x": round(x, 2), **_duration_fields(duration), "rest": True,
            "chord": None, "from_chord": False, "stroke": None, "lyric": None,
            "techniques": [], "notes": []}


def _is_voice_mark(glyph, system, notes, digits) -> bool:
    """한 성부에만 속하는 표기 — 두 성부에 다 주면 양쪽 beat 에 중복으로 붙는다."""
    if rhythm.is_triplet_mark(glyph, system, notes, digits):
        return True
    return _in_tab_band(glyph, system) and (
        smufl.name(glyph.char) in ARTICULATION_KINDS
        or glyph.char in (SMUFL_STROKE_DOWN, SMUFL_STROKE_UP))


def _voice_of_marks(geo, system, bounds, notes, rests, frets, graces, raw_digits,
                    assigned) -> None:
    """꾸밈음·연주법·셋잇단·아티큘레이션·스트로크를 음의 성부에 배정한다."""
    x0, x1 = bounds
    for grace in graces:
        string = _snap_to_string(grace.y, system.tab_ys)
        same_string = [n for n in notes if _snap_to_string(n.y, system.tab_ys) == string]
        following = [n for n in same_string if n.x > grace.x]
        target = (min(following, key=lambda n: n.x) if following else
                  min(same_string, key=lambda n: abs(n.x - grace.x), default=None))
        assigned[grace] = assigned.get(target, 0)
    for mark in geo.glyphs:
        if not x0 <= mark.x < x1 or mark in assigned:
            continue
        if mark.char in TECHNIQUE_GLYPHS and _in_tab_band(mark, system):
            source = marks.technique_source(geo, system, mark, frets + graces)
            assigned[mark] = assigned.get(source, 0)
        elif _is_voice_mark(mark, system, notes, raw_digits):
            triplet = rhythm.is_triplet_mark(mark, system, notes, raw_digits)
            # 셋잇단 '3' 은 자기 빔이 묶은 음의 성부다 — 거리로 고르면 빔 반대편
            # 성부의 음이 더 가까울 때 남의 셋잇단이 된다
            held = rhythm.mark_beam_notes(geo, system, mark, notes) if triplet else []
            if held:
                voices = [assigned[n] for n in held]
                assigned[mark] = max(set(voices), key=voices.count)
                continue
            # 셋잇단 '3' 은 쉼표도 후보다 — 쉼표뿐인 성부의 '3' 이 음 쪽 성부로 가면
            # 안 된다. 악센트·스트로크는 음에만 걸린다 (쉼표 성부로 가면 사라진다)
            owners = notes + rests if triplet else notes
            if not owners:
                continue            # 쉼표뿐인 마디의 악센트 — 걸 음이 없다
            # 셋잇단은 가장 가까운 음, 스트로크·아티큘레이션은 x 가 가장 가까운 음 —
            # 그 표기는 x 로 beat 에 붙으므로 y 가 더 가까운 다른 성부로 가면 사라진다
            distances = [(math.hypot(n.x - mark.x, n.y - mark.y),) if triplet
                         else (abs(n.x - mark.x), abs(n.y - mark.y)) for n in owners]
            assigned[mark] = assigned[owners[distances.index(min(distances))]]


def _pending_voice(entry: dict, notes, assigned, system) -> int:
    """앞 마디에서 넘어온 꾸밈음은 이 마디에서 그 줄의 첫 음이 가진 성부로 간다.

    슬래시도 후보다 — 슬래시 화음의 보이싱은 모든 줄을 덮지만 글리프는 한 줄
    자리에만 찍힌다. 그 줄의 음과 슬래시 중 가장 앞선 것을 따른다.
    """
    same_string = [n for n in notes
                   if _snap_to_string(n.y, system.tab_ys) == entry["string"]]
    slashes = [n for n in notes if _in_range(n.char, SMUFL_SLASH_RANGE)]
    first = min(same_string + slashes, key=lambda n: n.x, default=None)
    return assigned.get(first, 0)


def _polyphonic_measure(geo, system, bounds, index, tokens, warn, time_sig,
                        letter_index, syllables, carried_chord,
                        pending_row_chord, pending_graces, tuning, cutoff):
    """두 성부 마디. 표기를 성부에 나눠 성부별로 해석한 뒤 공통 시간축에 올린다.

    판정은 이 마디의 음·쉼표만 본다 — 시스템 전체로 판정하면 같은 줄의
    단성부 마디까지 두 성부가 되어 가짜 온쉼표가 생기고 못갖춘마디 판정이 꺼진다.
    """
    x0, x1 = bounds
    raw_frets = _fret_glyphs(geo, system, x0, x1, letter_index, cutoff)
    sources: dict = {}
    # 성부 판정도 병합한 프렛 중심을 쓴다. 병합 경고는 성부별 해석이 남긴다
    frets = _merge_two_digit_frets(
        geo, raw_frets, system, index, _Warnings(), sources=sources)
    graces = _grace_glyphs(geo, system, x0, x1, letter_index, cutoff)
    notes = frets + _dead_glyphs(geo, system, x0, x1) + [
        g for g in geo.glyphs if x0 <= g.x < x1
        and _in_range(g.char, SMUFL_SLASH_RANGE) and _in_tab_band(g, system)]
    rests = _rest_glyphs(geo, system, x0, x1)
    assigned = rhythm.voice_assignments(geo, system, notes, rests,
                                        durations.target_quarters(*time_sig))
    if assigned is None:
        return None
    assigned.update((raw, assigned[merged]) for raw, merged in sources.items())
    _voice_of_marks(geo, system, bounds, notes, rests, frets, graces,
                    raw_frets + graces, assigned)
    _warn_unsupported(geo, system, x0, x1, index, warn)
    pending_voices = [_pending_voice(g, notes, assigned, system)
                      for g in pending_graces or []]
    parts = []
    for voice in (0, 1):
        part_geo = replace(geo, glyphs=[g for g in geo.glyphs
                                        if assigned.get(g, voice) == voice])
        part = _build_measure(
            part_geo, system, bounds, index, tokens, warn, time_sig,
            letter_index, syllables if voice == 0 else [],
            carried_chord, None,
            [g for g, v in zip(pending_graces or [], pending_voices) if v == voice],
            tuning, _voice=voice)
        parts.append(part)
    merged = parts[0]
    merged["kind"] = _classify(
        any(p["kind"] in ("fret", "mixed") for p in parts),
        any(p["kind"] in ("slash", "mixed") for p in parts))
    merged["pending_graces"] = [g for p in parts for g in p.get("pending_graces", [])]
    merged["_fit"] = {
        "x0": x0, "x1": x1, "full_measure_rest": False,
        "events": [p["_fit"]["events"] for p in parts],
        "event_beats": [p["beats"] for p in parts],
        "annotations": [(voice, entry) for voice, p in enumerate(parts)
                        for entry in p["glyphs"]],
    }
    _assemble_voices(merged, durations.target_quarters(*time_sig), warn)
    # 코드 행은 두 성부를 합친 순서로 배정한다 — 첫 성부가 쉬는 자리의 코드가
    # 다음 첫 성부 음으로 밀리지 않고, 두 성부에 겹쳐 붙지도 않는다
    merged["pending_row_chord"] = _assign_row_chords(
        merged["beats"], tokens, bounds, index, warn, tuning, pending=pending_row_chord)
    return merged


def _assemble_voices(measure: dict, target: float, warn: _Warnings) -> None:
    """성부별 이벤트를 공통 시간축에 올려 beat 목록과 주석 인덱스를 만든다."""
    fit = measure["_fit"]
    alignment = timeline.align_voices(
        fit["events"], fit["x1"], target, BEAT_CLUSTER_TOLERANCE)
    entries = []
    for voice, (slots, beats) in enumerate(zip(alignment.slots, fit["event_beats"])):
        x = fit["x0"]
        for order, (event, duration) in enumerate(slots):
            if event is None:
                beat = _rest_beat(x, duration)      # 생략된 쉼표를 채운다
                beat["fill"] = True                 # 타이 인접성 판정에서 뺀다
            else:
                beat = beats[event]                 # 타이가 붙은 같은 객체를 다시 쓴다
                beat.update(_duration_fields(duration))
                x = beat["x"]
            beat["voice"] = voice
            entries.append(((beat["x"], voice, order), voice, event, beat))
    entries.sort(key=lambda entry: entry[0])
    measure["beats"] = [beat for *_, beat in entries]
    positions = {(voice, event): new for new, (_, voice, event, _) in enumerate(entries)
                 if event is not None}
    annotations: dict = {}
    for voice, entry in fit["annotations"]:
        mapped = dict(entry)
        mapped["beat"] = positions.get((voice, entry["beat"]))
        key = tuple((k, v) for k, v in entry.items() if k != "beat")
        target_beat = (measure["beats"][mapped["beat"]]
                       if mapped["beat"] is not None else None)
        # 두 성부가 같은 주석을 가지면 그 줄의 음이 있는 성부 쪽 beat 을 고른다
        if key not in annotations or (
                entry.get("string") is not None and target_beat
                and any(n["string"] == entry["string"] for n in target_beat["notes"])):
            annotations[key] = mapped
    measure["glyphs"] = list(annotations.values())
    fit["beat_xs"] = list(alignment.union_xs)
    # 모든 성부가 온쉼표(또는 비었다) — 단성부의 온쉼표 마디처럼 폭으로 줄이지 않는다
    fit["full_measure_rest"] = not alignment.union_xs
    # 한 성부가 확정 음가만으로(채움 쉼표 없이) 마디를 채웠는가 — 못갖춘마디 보호
    fit["pins_fill"] = any(
        voice.whole_rest or (
            voice.xs and len(voice.pins) == len(voice.xs)
            and all(event is not None for event, _ in slots)
            and abs(sum(d.quarters for _, d in slots) - target) < durations.EPSILON)
        for voice, slots in zip(fit["events"], alignment.slots))
    if not alignment.exact:
        warn.add(measure["index"], "duration_mismatch",
                 f"두 성부를 목표 {target:.3f}박에 맞추지 못했다"
                 + (": " + "; ".join(alignment.problems) if alignment.problems
                    else " — 공통 시간축 합 불일치"))


def _build_measure(geo, system, bounds, index, tokens, warn,
                   time_sig: tuple[int, int], letter_index,
                   syllables: list[tuple[float, str]],
                   carried_chord: str | None = None,
                   pending_row_chord: str | None = None,
                   pending_graces: list | None = None,
                   tuning: tuple[int, ...] = STANDARD_TUNING,
                   *, _voice: int | None = None) -> dict:
    x0, x1 = bounds
    grace_cutoff = _grace_cutoff(geo, system, letter_index)
    if _voice is None:
        polyphonic = _polyphonic_measure(
            geo, system, bounds, index, tokens, warn, time_sig, letter_index,
            syllables, carried_chord, pending_row_chord, pending_graces,
            tuning, grace_cutoff)
        if polyphonic is not None:
            return polyphonic
    raw_fret_glyphs = _fret_glyphs(geo, system, x0, x1, letter_index,
                                   grace_cutoff)
    fret_glyphs = _merge_two_digit_frets(geo, raw_fret_glyphs, system, index, warn)
    raw_grace_glyphs = _grace_glyphs(geo, system, x0, x1, letter_index,
                                     grace_cutoff)
    grace_glyphs = _merge_two_digit_frets(geo, raw_grace_glyphs, system, index, warn)
    dead_glyphs = _dead_glyphs(geo, system, x0, x1)
    rest_glyphs = _rest_glyphs(geo, system, x0, x1)
    slash_xs = _slash_xs(geo, system, x0, x1)
    kind = _classify(fret_glyphs + dead_glyphs, slash_xs)
    if _voice is None:
        _warn_unsupported(geo, system, x0, x1, index, warn)   # 성부 모드는 한 번만 센다

    techniques = _techniques(
        geo, system, x0, x1, fret_glyphs, letter_index, grace_glyphs)
    articulations = _articulations(geo, system, x0, x1)
    beat_xs = _cluster([g.x for g in fret_glyphs + dead_glyphs + rest_glyphs]
                       + slash_xs)
    note_glyphs = fret_glyphs + dead_glyphs + [
        g for g in geo.glyphs
        if x0 <= g.x < x1 and _in_range(g.char, SMUFL_SLASH_RANGE)
        and _in_tab_band(g, system)]
    known_pins = rhythm.note_pins(geo, system, beat_xs, note_glyphs, x1)
    # 쉼표는 자기 beat 자리를 갖는다. 이름이 길이를 말해주면 DP 에 고정한다.
    rest_positions: set[int] = set()
    rest_pins: dict[int, durations.LegalDuration] = {}
    for rest_glyph in rest_glyphs:
        position = _nearest_beat(beat_xs, rest_glyph.x)
        if position is None:
            continue
        rest_positions.add(position)
        legal = REST_DURATIONS.get(smufl.name(rest_glyph.char) or "")
        if legal is not None:
            # 쉼표 오른쪽의 붙임점 — 점4분쉼표를 4분으로 고정하면 남는
            # 반박이 이웃 beat 에 잘못 재배분된다. 같은 마디 안, 비슷한
            # 높이의 점만 받는다 — 다른 줄 점음표의 점·다음 마디의 점이
            # 남의 쉼표를 점쉼표로 만들면 안 된다
            has_dot = any(
                smufl.name(g.char) == "augmentationDot"
                and 0 < g.x - rest_glyph.x <= REST_DOT_WINDOW
                and g.x < x1
                and abs(g.y - rest_glyph.y) <= REST_DOT_Y_TOLERANCE
                for g in geo.glyphs)
            if has_dot:
                legal = durations.LegalDuration(
                    legal.value, True, legal.quarters * 1.5)
            rest_pins[position] = legal
    known_pins.update(rest_pins)
    # 아티큘레이션은 표기 x 가 가장 가까운 beat 의 박 전체에 걸린다
    beat_articulations: dict[int, list[str]] = {}
    for art_glyph, art_kind in articulations:
        position = _nearest_beat(beat_xs, art_glyph.x)
        if position is not None:
            beat_articulations.setdefault(position, []).append(art_kind)
    # 가사는 소리 나는 beat 에만 붙는다 — 쉼표 자리는 후보에서 뺀다
    lyric_xs = [x for i, x in enumerate(beat_xs) if i not in rest_positions]
    lyrics = _assign_lyrics(
        lyric_xs, [(x, c) for x, c in syllables if x0 <= x < x1], index, warn)
    measure = {
        "index": index, "time_sig": list(time_sig), "kind": kind, "beats": [],
        # 병합 전 원본 숫자와 꾸밈음도 걷어낸다 — 남기면 AI 의 unread 목록에
        # 이미 반영된 글리프가 섞여 중복 제안을 부른다
        "glyphs": _annotation_glyphs(
            geo, system, bounds, beat_xs,
            raw_fret_glyphs + raw_grace_glyphs + dead_glyphs + rest_glyphs
            + [glyph for glyph, _ in articulations]),
        # 추출기가 코드 행에서 이미 조립해 읽은 이름. AI 가 낱글자를 다시 조립하면
        # 'Cadd9' 를 'C' 로 끊는 오독이 생긴다 — 읽은 결과를 그대로 넘긴다.
        "chord_row": [{"x": round(x, 1), "name": name} for x, name in tokens
                      if x0 <= x < x1],
        "chord_in_effect": _chord_at(tokens, x0) or carried_chord,
    }
    direction, from_direction = _glyph_directions(geo, system, bounds)
    text_direction, text_from = _text_directions(measure["glyphs"])
    if direction or text_direction:
        measure["direction"] = direction or text_direction
    if from_direction or text_from:
        measure["from_direction"] = from_direction or text_from
    repeats = _repeat_flags(geo, system, bounds)
    if repeats["open"]:
        measure["repeat_open"] = True
    if repeats["close"]:
        measure["repeat_close"] = True
    # 겹반복의 이웃 마디 몫 — _resolve_boundary_repeats 가 걷어 옮긴다
    if repeats["close_previous"]:
        measure["_repeat_close_previous"] = True
    if repeats["open_next"]:
        measure["_repeat_open_next"] = True
    if not beat_xs:
        if _voice is None:
            warn.add(index, "empty_measure", f"판정 {kind}, x {x0:.1f}..{x1:.1f}")
            return measure

    # 타브의 표기 묶음만 셋잇단으로 푼다. 오선 표기만 있는 경우에는
    # 모든 타브 음가와 빔 묶음이 확정되고 마디 합을 유일하게 맞출 때만 보완한다.
    target = durations.target_quarters(*time_sig)
    groups, windows = rhythm.triplet_groups(
        geo, system, beat_xs, note_glyphs, bounds, raw_fret_glyphs + raw_grace_glyphs)
    if not groups and not windows:
        shared = rhythm.shared_triplet_group(
            geo, system, beat_xs, note_glyphs, bounds, known_pins, target)
        if shared:
            groups = [shared]
    tuplet_indices = {i for group in groups for i in group}
    for position in tuplet_indices & known_pins.keys():
        known_pins[position] = durations.as_triplet(known_pins[position])
    # 온쉼표 하나뿐인 마디는 놓인 x 와 무관하게 박자표 길이 전체를 쉰다
    full_measure_rest = (len(rest_glyphs) == 1 and not note_glyphs
                         and smufl.name(rest_glyphs[0].char) == "restWhole")
    if _voice is not None or full_measure_rest:
        # 자리표시 길이 — 성부 모드는 _assemble_voices 가 공통 시간축에서,
        # 온쉼표는 아래 쉼표 분할이 정한다
        fitted = [known_pins.get(i, durations.LEGAL[0]) for i in range(len(beat_xs))]
        exact = True
    else:
        fitted, exact, _ = durations.fit_with_triplets(
            durations.proportions(beat_xs, x1, target), target, known_pins,
            tuplet_indices, windows)
    if not exact:
        total = sum(d.quarters for d in fitted)
        warn.add(index, "duration_mismatch",
                 f"합 {total:.3f} / 목표 {target:.3f}, "
                 f"durs={[d.value for d in fitted]}")

    for position, (beat_x, duration) in enumerate(zip(beat_xs, fitted)):
        if position in rest_positions:
            # 7/8 의 온쉼표는 온음표 한 개로 담을 수 없다 — 쉼표 여러 개로 나눈다
            parts = (durations.rest_durations(target)
                     if full_measure_rest and _voice is None else [duration])
            measure["beats"].extend(_rest_beat(beat_x, part) for part in parts)
            continue
        notes = _beat_notes(fret_glyphs, dead_glyphs, beat_x, system, index, warn)
        # 슬래시에서 온 beat 인지 좌표로 판정한다. 노트가 비었다는 이유만으로
        # 코드 보이싱을 채우면, 줄 스냅 실패한 프렛 하나가 추측한 화음으로 증폭된다.
        from_slash = any(abs(slash_x - beat_x) <= BEAT_CLUSTER_TOLERANCE
                         for slash_x in slash_xs)
        chord = None
        # 스트로크는 슬래시 beat 전용이 아니다 — 프렛 코드 위 스트럼 화살표도
        # 같은 기호다 (from_chord 안에서만 찾으면 조용히 사라진다)
        stroke = _stroke_at(geo, system, beat_x)
        # 음이 코드명에서 만들어졌는지 기록한다. 나중에 AI 가 코드명을 고치면
        # 음도 새 코드로 다시 만들어야 하는데, 여기 아니면 구분할 근거가 없다.
        from_chord = from_slash and not notes
        if from_chord:
            # 시스템이 슬래시 beat 로 시작하면 그 앞에 코드 토큰이 없다. 넘겨받은
            # 코드를 쓰지 않으면 그 beat 은 무음이 된다 — 마디 단위 chord_in_effect
            # 와 같은 근거를 쓴다. 이 악보에서는 매 시스템이 코드를 재표기해
            # 실제로 걸리지 않지만, 다른 악보에서는 걸린다.
            chord = _chord_at(tokens, beat_x) or carried_chord
            # 튜닝까지 넘겨 검증한다 — 표의 모양은 표준 튜닝 기준이라
            # Drop-D 에서 표준 G 모양을 그대로 치면 6번줄이 F 가 된다
            voicing = chords.voicing_for(chord, tuning)
            if chord is None:
                warn.add(index, "unknown_chord",
                         f"x={beat_x:.1f} 슬래시 beat 앞에 코드명이 없다")
            elif voicing is None:
                warn.add(index, "unknown_chord", f"{chord} — VOICINGS 에 없음")
            notes = [{"string": s, "fret": f} for s, f in (voicing or ())]
        elif not notes:
            warn.add(index, "empty_beat",
                     f"x={beat_x:.1f} 에 프렛 숫자가 있었으나 노트가 만들어지지 않았다")

        measure["beats"].append({
            "x": round(beat_x, 2),
            **_duration_fields(duration),
            "chord": chord,
            "from_chord": from_chord,
            "stroke": stroke,
            "lyric": lyrics.get(beat_x),
            "techniques": [
                {"string": t["string"], "kind": t["kind"]} for t in techniques
                if not t["grace"] and abs(t["x"] - beat_x) <= BEAT_CLUSTER_TOLERANCE
            ] + [{"string": None, "kind": kind}
                 for kind in beat_articulations.get(position, ())],
            "notes": notes,
        })
    # 성부 모드의 코드 행은 _polyphonic_measure 가 두 성부를 합친 뒤 배정한다
    measure["pending_row_chord"] = (
        _assign_row_chords(measure["beats"], tokens, bounds, index, warn, tuning,
                           pending=pending_row_chord)
        if _voice is None else None)
    measure["pending_graces"] = _attach_graces(
        measure["beats"], grace_glyphs, system, index, warn,
        pending=pending_graces, techniques=techniques)
    # 경계 마디(픽업·불완전 종지) 재판정에 쓰는 임시 값 — extract_ir 가 걷어낸다
    measure["_fit"] = {
        "beat_xs": beat_xs, "x1": x1, "pins": known_pins,
        "tuplet_indices": tuplet_indices,
        "triplet_windows": windows,
        "full_measure_rest": full_measure_rest,
        # 확정 음가만으로 박자표를 채웠다 — 셋잇단 창을 푼 뒤의 합으로 본다
        "pins_fill": bool(beat_xs) and len(known_pins) == len(beat_xs) and exact,
    }
    if _voice is not None:
        measure["_fit"]["events"] = timeline.VoiceEvents(
            xs=tuple(beat_xs), pins=dict(known_pins),
            rests=frozenset(rest_positions), tuplets=frozenset(tuplet_indices),
            triplet_windows=tuple(windows), whole_rest=full_measure_rest)
    return measure




def _resolve_boundary_repeats(measures: list[dict]) -> None:
    """겹반복(:‖:)의 이웃 마디 몫을 실제 이웃에 옮긴다."""
    for position, measure in enumerate(measures):
        if measure.pop("_repeat_close_previous", False) and position > 0:
            measures[position - 1]["repeat_close"] = True
        if (measure.pop("_repeat_open_next", False)
                and position + 1 < len(measures)):
            measures[position + 1]["repeat_open"] = True


def _measure_span(measure: dict) -> float:
    """마디의 음악 구간 폭(pt) — 첫 beat 부터 마디선까지. proportions 와 같은 정의다."""
    fit = measure["_fit"]
    return fit["x1"] - fit["beat_xs"][0]


def _refit_measure(measure: dict, numerator: int, denominator: int,
                   warn: _Warnings) -> None:
    """줄어든 박자표로 beat 길이를 다시 맞춘다."""
    fit = measure["_fit"]
    target = durations.target_quarters(numerator, denominator)
    old = measure["time_sig"]
    measure["time_sig"] = [numerator, denominator]
    # 확정 빔 때문에 원래 박자표와 어긋났던 경고도 새 박자표 기준으로 바꾼다.
    warn.items[:] = [w for w in warn.items
                     if not (w["measure"] == measure["index"]
                             and w["kind"] == "duration_mismatch")]
    warn.add(measure["index"], "pickup_measure",
             f"{old[0]}/{old[1]} 보다 확실히 짧은 경계 마디 — 박자표를 "
             f"{numerator}/{denominator} 로 줄였다 (못갖춘마디로 판단)")
    if "events" in fit:
        _assemble_voices(measure, target, warn)     # 두 성부는 공통 시간축째로 다시 푼다
        return
    fitted, exact, _ = durations.fit_with_triplets(
        durations.proportions(fit["beat_xs"], fit["x1"], target), target,
        fit["pins"], fit["tuplet_indices"], fit["triplet_windows"])
    for beat, duration in zip(measure["beats"], fitted):
        beat.update(_duration_fields(duration))
    if not exact:
        total = sum(d.quarters for d in fitted)
        warn.add(measure["index"], "duration_mismatch",
                 f"합 {total:.3f} / 목표 {target:.3f} (픽업 재맞춤)")


def _adjust_boundary_measures(measures: list[dict], warn: _Warnings) -> None:
    """첫·끝 마디가 박자표보다 확실히 짧으면 못갖춘마디로 보고 박자표를 줄인다.

    proportions 는 모든 마디를 박자표 길이로 정규화한다 — 4분음표 하나짜리
    픽업이 온음표로 늘어난다. 마디 폭이 음길이에 비례한다는 같은 성질을
    거꾸로 써서, 안쪽 마디들의 pt/4분음표 중앙값으로 경계 마디의 실제
    길이를 추정한다. 안쪽 마디는 손대지 않는다 — 조판 변동으로 좁아진
    마디를 오판하면 멀쩡한 마디가 망가진다.
    """
    scored = [m for m in measures if m["beats"] and "_fit" in m and m["_fit"]["beat_xs"]]
    if len(scored) < 3:
        return                      # 스케일을 잴 안쪽 표본이 없다
    scales = sorted(
        _measure_span(m) / durations.target_quarters(*m["time_sig"])
        for m in scored[1:-1]
        if not m["_fit"]["full_measure_rest"] and _measure_span(m) > 0)
    if not scales:
        return
    scale = scales[len(scales) // 2]
    for measure in (measures[0], measures[-1]):
        if "_fit" not in measure:
            continue
        fit = measure["_fit"]
        if fit["full_measure_rest"]:
            continue                 # 온쉼표는 가운데 놓여도 마디 전체다
        target = durations.target_quarters(*measure["time_sig"])
        if fit["pins_fill"]:
            continue                 # 확정 음가가 채운 마디를 폭으로 줄이지 않는다
        estimated = _measure_span(measure) / scale
        if estimated >= PICKUP_MAX_RATIO * target:
            continue
        denominator = measure["time_sig"][1]
        numerator = max(1, round(estimated * denominator / 4.0))
        if numerator >= measure["time_sig"][0]:
            continue
        _refit_measure(measure, numerator, denominator, warn)




def extract_ir(pdf_path: str, tempo: int | None = None,
               title: str | None = None, artist: str | None = None,
               tuning: list[int] | None = None,
               capo: int | None = None) -> dict:
    """PDF 전체를 IR 로 만든다.

    제목은 PDF 메타데이터를 쓰지 않는다 — 대상 PDF 의 메타 제목은 mojibake 된
    "Ÿfl˘'‹.musx" 다. 파일명 stem 을 쓰고 인자로 덮어쓸 수 있다.
    """
    warn = _Warnings()
    measures: list[dict] = []
    header_info: dict[str, str] = {}
    saw_text = False
    active_tuning = tuple(tuning) if tuning else STANDARD_TUNING

    time_sig = DEFAULT_TIME_SIG
    carried_chord: str | None = None
    pending_row_chord: str | None = None
    pending_graces: list = []
    extra_lyric_rows: dict[int, dict] = {}
    try:
        document = pymupdf.open(pdf_path)
    except pymupdf.FileDataError as exc:
        raise NotATabPdf(f"{pdf_path}: PDF 로 열 수 없다 ({exc})") from exc
    with document:
        for page in document:
            geo = geometry.load_page_geometry(page)
            letter_index = build_letter_index(geo)
            if geo.glyphs:
                saw_text = True
            systems = geometry.find_systems(geo)
            # 2줄 이상인 staff 그룹이 시스템으로 묶이지 못하면 그 단은 통째로
            # 사라진다 — 조용히 넘기지 않는다 (1줄짜리는 제목 밑줄 따위다)
            stafflike = [len(g) for g in geometry.staff_groups(geo) if len(g) >= 2]
            if len(stafflike) != 2 * len(systems):
                warn.add(len(measures), "system_skipped",
                         f"staff 그룹(선 수 {stafflike}) 중 일부를 5+6선 시스템으로 "
                         f"묶지 못했다 — 그 단의 마디가 통째로 빠진다")
            for system in systems:
                system_first_measure = len(measures)
                all_bounds = geometry.measure_bounds(geo, system)
                if not all_bounds:
                    warn.add(len(measures), "system_skipped",
                             "시스템을 찾았으나 두 staff 를 관통하는 마디선이 없다 "
                             "— 이 단을 건너뛴다")
                    continue
                detected_sigs = _detect_time_signatures(
                    geo, system, len(measures), warn)
                if not measures:
                    header_info.update(_header_fields(geo, system))
                tokens = _chord_tokens(geo, system, all_bounds[-1][1] + 1.0)
                syllables, extra_rows = _lyric_syllables(
                    geo, system, len(measures), warn)
                for row_index, row in enumerate(extra_rows):
                    if row_index not in extra_lyric_rows:
                        # 절의 시작은 첫 음절이 놓인 마디다 — 시스템 첫
                        # 마디로 뭉뚱그리면 GP 가 절을 앞당겨 분배한다
                        first_x = row[0][0]
                        offset = next(
                            (i for i, (a, b) in enumerate(all_bounds)
                             if a <= first_x < b), 0)
                        extra_lyric_rows[row_index] = {
                            "start": len(measures) + offset, "chars": []}
                    extra_lyric_rows[row_index]["chars"].extend(
                        char for _, char in row)
                for bounds in all_bounds:
                    # 박자표는 표기된 마디부터 적용된다 — 시스템 첫 마디로
                    # 소급하면 중간 박자 변경이 앞 마디를 망가뜨린다
                    while detected_sigs and detected_sigs[0][0] < bounds[1]:
                        _, sig = detected_sigs.pop(0)
                        if sig == time_sig:
                            continue
                        time_sig = sig
                        if sig != DEFAULT_TIME_SIG:
                            warn.add(len(measures), "time_signature",
                                     f"{sig[0]}/{sig[1]} 박자를 감지했다 "
                                     f"— 이 경로는 실제 악보로 검증되지 않았다")
                    measure = _build_measure(
                        geo, system, bounds, len(measures), tokens, warn,
                        time_sig, letter_index, syllables, carried_chord,
                        pending_row_chord, pending_graces, active_tuning)
                    # 빈 마디는 조기 반환이라 pending 키가 없다 — 이월분 유지
                    pending_row_chord = measure.pop("pending_row_chord",
                                                    pending_row_chord)
                    pending_graces = measure.pop("pending_graces",
                                                 pending_graces)
                    measures.append(measure)
                _apply_tie_curves(
                    geo, system, measures[system_first_measure:], warn)
                # 코드는 줄바꿈을 넘어 유지된다. 시스템마다 tokens 가 새로
                # 시작하므로, 다음 시스템 첫 마디가 "코드 없음" 이 되지 않게 넘긴다
                if tokens:
                    carried_chord = tokens[-1][1]

    if not saw_text:
        raise NotATabPdf(
            f"{pdf_path}: 텍스트 레이어가 없다. 스캔 이미지 PDF 는 변환할 수 없다")
    if not measures:
        raise NotATabPdf(f"{pdf_path}: 6줄 타브 staff 를 찾지 못했다")

    _resolve_boundary_repeats(measures)
    _adjust_boundary_measures(measures, warn)
    for measure in measures:
        measure.pop("_fit", None)

    if tempo is None:
        # 이 악보에는 BPM 표기(♩=N·메트로놈 글리프)가 아예 없다. 리듬 지시
        # ('Slow 16Beat') 는 세분 표기라 템포가 아니다 — 추측한 값을 조용히
        # 내보내면 사용자가 악보에서 읽은 값이라고 믿는다.
        warn.add(0, "tempo_guessed",
                 f"악보에 BPM 표기가 없어 {DEFAULT_TEMPO} 을 넣었다"
                 + (f" (리듬 지시: {header_info['rhythm']!r})"
                    if "rhythm" in header_info else "")
                 + " — 음원과 맞추려면 --tempo 로 지정할 것")

    key_text = header_info.get("key", "")
    if key_text and header.parse_key(key_text) is None:
        warn.add(0, "key_unreadable",
                 f"조성 표기 {key_text!r} 를 읽지 못해 조표를 비워 뒀다")

    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    result = {
        "title": title if title is not None else stem,
        "artist": artist if artist is not None else header_info.get("artist", ""),
        "tempo": tempo or DEFAULT_TEMPO,
        "tempo_source": "지정" if tempo else "기본값(악보에 표기 없음)",
        "credits": {key: value for key, value in header_info.items()
                    if key in ("words", "music", "arranger", "performer")},
        "key": header_info.get("key", ""),
        "rhythm": header_info.get("rhythm", ""),
        "tuning": list(active_tuning),
        # CLI 인자가 머리글 표기('Capo 3')를 이긴다
        "capo": capo if capo is not None else int(header_info.get("capo", 0)),
        "measures": measures,
        "warnings": warn.items,
    }
    if extra_lyric_rows:
        result["extra_lyric_rows"] = [
            {"start": entry["start"], "text": " ".join(entry["chars"])}
            for _, entry in sorted(extra_lyric_rows.items())]
    return result
