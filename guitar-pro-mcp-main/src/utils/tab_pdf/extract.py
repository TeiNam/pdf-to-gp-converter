"""PDF 기하를 음악적 중간표현(IR)으로 해석한다."""

import os
from dataclasses import dataclass, field

import pymupdf

from . import chords, durations, geometry

STANDARD_TUNING = (64, 59, 55, 50, 45, 40)      # 1=고음 E … 6=저음 E
DEFAULT_TEMPO = 80
DEFAULT_TIME_SIG = (4, 4)

# 프렛 숫자로 인정할 글리프 크기 상한 (pt). 'T','A','B' 세로 라벨은 14.4pt 로 더 크다
MAX_FRET_GLYPH_SIZE = 11.0
# 숫자 baseline 이 이 거리 안이면 그 타브 선에 속한다 (선 간격 7.7 의 절반 미만)
MAX_STRING_SNAP_DISTANCE = 4.0
# 같은 beat(화음)로 묶을 x 허용 오차 (pt)
BEAT_CLUSTER_TOLERANCE = 2.0
# 글리프가 타브 staff 에 속한다고 볼 상하 여유 (pt)
TAB_BAND_MARGIN = 8.0
# 코드 행 대역: 멜로디 5선 위쪽 이만큼 (pt). 제목·부제를 배제한다
CHORD_BAND_HEIGHT = 20.0
# 코드명 문자를 한 토큰으로 이을 간격 상한 (pt). 직전 문자의 잉크 끝 기준.
# 실측: 코드명 내부 최대 1.41, 코드명 사이 최소 40.32
CHORD_CHAR_GAP = 5.0
# 코드가 beat 에 적용된다고 볼 x 여유 (pt)
CHORD_APPLY_SLACK = 5.0
# 스트로크 기호가 beat 에 속한다고 볼 x 거리 (pt)
STROKE_X_WINDOW = 6.0
# 같은 baseline 으로 볼 y 오차 (pt)
SAME_BASELINE_TOLERANCE = 0.6
# 숫자에 알파벳이 붙어 있다고 볼 잉크 간격 (pt) — 텍스트 주석 배제용
LETTER_ADJACENCY_GAP = 2.5
# 숫자쌍이 커닝으로 붙었다고 볼 잉크 간격 (pt). 실측: 같은 숫자 -1.89, 별개 음 0.7 이상
KERNED_DIGIT_INK_GAP = 0.0
# 기타 프렛 상한 — 이보다 큰 병합값은 두 자리 프렛일 수 없다
MAX_FRET = 24

SMUFL_SLASH_RANGE = (0xE100, 0xE10F)            # SMuFL Slash noteheads
SMUFL_REST_RANGE = (0xE4E0, 0xE4FF)             # 타브 대역에 나오면 경고
SMUFL_STROKE_DOWN = ""
SMUFL_STROKE_UP = ""


class NotATabPdf(ValueError):
    """타브 악보로 해석할 수 없는 입력."""


@dataclass
class _Warnings:
    items: list[dict] = field(default_factory=list)

    def add(self, measure: int, kind: str, detail: str) -> None:
        self.items.append({"measure": measure, "kind": kind, "detail": detail})


def _in_range(char: str, bounds: tuple[int, int]) -> bool:
    low, high = bounds
    return low <= ord(char) <= high


def _in_tab_band(glyph: geometry.Glyph, system: geometry.System) -> bool:
    return (system.tab_ys[0] - TAB_BAND_MARGIN <= glyph.y
            <= system.tab_ys[-1] + TAB_BAND_MARGIN)


def _snap_to_string(y: float, tab_ys: list[float]) -> int | None:
    """baseline y 를 가장 가까운 타브 선에 붙여 줄 번호(1..6)를 돌려준다."""
    index = min(range(len(tab_ys)), key=lambda i: abs(y - tab_ys[i]))
    if abs(y - tab_ys[index]) > MAX_STRING_SNAP_DISTANCE:
        return None
    return index + 1        # tab_ys 는 위→아래, 위가 고음 E = string 1


def _has_adjacent_letter(geo, glyph: geometry.Glyph) -> bool:
    """같은 baseline 에 알파벳이 붙어 있으면 프렛 숫자가 아니라 텍스트다.

    이 악보에는 타브 staff 바로 위에 "with 16beat arp play" 같은 연주 지시가 있고,
    그 '1','6' 이 타브 1선에서 1.2pt 거리라 선 스냅을 통과한다. 실제 프렛 숫자
    294개는 인접 알파벳이 하나도 없고 스냅 거리가 3.3~3.5pt 로 일정하다.
    """
    for other in geo.glyphs:
        if not other.char.isalpha():
            continue
        if abs(other.y - glyph.y) > SAME_BASELINE_TOLERANCE:
            continue
        if (abs(other.x - glyph.x_end) < LETTER_ADJACENCY_GAP
                or abs(glyph.x - other.x_end) < LETTER_ADJACENCY_GAP):
            return True
    return False


def _fret_glyphs(geo, system, x0, x1) -> list[geometry.Glyph]:
    """폰트 이름에 의존하지 않는다 — 숫자 + 타브 대역 + 크기 상한 + 텍스트 배제."""
    return [g for g in geo.glyphs
            if x0 <= g.x < x1 and g.char.isdigit()
            and g.size <= MAX_FRET_GLYPH_SIZE and _in_tab_band(g, system)
            and not _has_adjacent_letter(geo, g)]


def _warn_kerned_digit_pairs(glyphs, system, index, warn) -> None:
    """커닝으로 붙은 숫자쌍은 두 자리 프렛일 수 있다 — 쪼개고 조용히 넘기지 않는다.

    이 PDF 에는 해당 사례가 없다 (실제 인접쌍은 모두 잉크 간격 0.7pt 이상의 빠른
    연속 음이고, 합치면 25 이상이 되어 프렛으로 불가능하다). 다른 악보에서 나오면
    경고로 드러나게 한다.
    """
    by_string: dict[int, list[geometry.Glyph]] = {}
    for glyph in glyphs:
        string = _snap_to_string(glyph.y, system.tab_ys)
        if string is not None:
            by_string.setdefault(string, []).append(glyph)
    for string, group in by_string.items():
        group.sort(key=lambda g: g.x)
        for left, right in zip(group, group[1:]):
            if right.x - left.x_end >= KERNED_DIGIT_INK_GAP:
                continue
            merged = int(left.char + right.char)
            if merged > MAX_FRET:
                continue
            warn.add(index, "unsupported_glyph",
                     f"string{string} 의 {left.char!r}{right.char!r} 가 붙어 있다 "
                     f"— 두 자리 프렛 {merged} 일 수 있으나 별개 음으로 처리했다")


def _slash_xs(geo, system, x0, x1) -> list[float]:
    return [g.x for g in geo.glyphs
            if x0 <= g.x < x1 and _in_range(g.char, SMUFL_SLASH_RANGE)
            and _in_tab_band(g, system)]


def _chord_tokens(geo, system, x_limit: float) -> list[tuple[float, str]]:
    """이 시스템의 코드 행에서 (x, 코드명) 을 복원한다.

    직전 문자의 잉크 끝(`x_end`) 기준으로 이어붙인다. origin 기준으로는 'Cadd9' 가
    'C' + 'add9' 로 쪼개진다 (실측 C→a origin 간격 7.68pt, 잉크끝 기준 0.90pt).
    """
    top = system.melody_ys[0]
    candidates = sorted(
        (g for g in geo.glyphs
         if top - CHORD_BAND_HEIGHT <= g.y < top and g.x < x_limit),
        key=lambda g: (round(g.y, 1), g.x),
    )
    tokens: list[tuple[float, str]] = []
    text, start_x, prev_end, prev_y = "", None, None, None
    for glyph in candidates:
        if (prev_end is None or round(glyph.y, 1) != prev_y
                or glyph.x - prev_end > CHORD_CHAR_GAP):
            if text:
                tokens.append((start_x, text))
            text, start_x = "", glyph.x
        text += glyph.char
        prev_end, prev_y = glyph.x_end, round(glyph.y, 1)
    if text:
        tokens.append((start_x, text))
    return [(x, name) for x, name in tokens if chords.looks_like_chord(name)]


def _chord_at(tokens: list[tuple[float, str]], x: float) -> str | None:
    """x 이전(또는 같은 위치)의 가장 가까운 코드명."""
    current = None
    for token_x, name in tokens:
        if token_x <= x + CHORD_APPLY_SLACK:
            current = name
        else:
            break
    return current


def _stroke_at(geo, x: float) -> str | None:
    for glyph in geo.glyphs:
        if abs(glyph.x - x) > STROKE_X_WINDOW:
            continue
        if glyph.char == SMUFL_STROKE_DOWN:
            return "down"
        if glyph.char == SMUFL_STROKE_UP:
            return "up"
    return None


def _cluster(xs: list[float]) -> list[float]:
    clustered: list[float] = []
    for x in sorted(xs):
        if not clustered or x - clustered[-1] > BEAT_CLUSTER_TOLERANCE:
            clustered.append(x)
    return clustered


def _classify(fret_glyphs, slash_xs) -> str:
    if fret_glyphs and slash_xs:
        return "mixed"
    if fret_glyphs:
        return "fret"
    if slash_xs:
        return "slash"
    return "empty"


def _warn_unsupported(geo, system, x0, x1, index, warn) -> None:
    """타브 대역의 쉼표는 x간격 기반 음길이를 틀어뜨린다 — 조용히 넘기지 않는다."""
    for glyph in geo.glyphs:
        if not (x0 <= glyph.x < x1) or not _in_tab_band(glyph, system):
            continue
        if _in_range(glyph.char, SMUFL_REST_RANGE):
            warn.add(index, "unsupported_glyph",
                     f"타브 대역 쉼표 {hex(ord(glyph.char))} at x={glyph.x:.1f} "
                     f"— x간격 기반 음길이가 틀어질 수 있다")


def _beat_notes(fret_glyphs, beat_x, system, index, warn) -> list[dict]:
    notes = []
    for glyph in fret_glyphs:
        if abs(glyph.x - beat_x) > BEAT_CLUSTER_TOLERANCE:
            continue
        string = _snap_to_string(glyph.y, system.tab_ys)
        if string is None:
            warn.add(index, "unsnapped_digit",
                     f"숫자 {glyph.char!r} at ({glyph.x:.1f}, {glyph.y:.1f}) "
                     f"가 어느 줄에도 스냅되지 않았다")
            continue
        notes.append({"string": string, "fret": int(glyph.char)})
    return notes


def _build_measure(geo, system, bounds, index, tokens, warn) -> dict:
    x0, x1 = bounds
    fret_glyphs = _fret_glyphs(geo, system, x0, x1)
    slash_xs = _slash_xs(geo, system, x0, x1)
    kind = _classify(fret_glyphs, slash_xs)
    _warn_unsupported(geo, system, x0, x1, index, warn)
    _warn_kerned_digit_pairs(fret_glyphs, system, index, warn)

    beat_xs = _cluster([g.x for g in fret_glyphs] + slash_xs)
    measure = {"index": index, "time_sig": list(DEFAULT_TIME_SIG),
               "kind": kind, "beats": []}
    if not beat_xs:
        warn.add(index, "empty_measure", f"판정 {kind}, x {x0:.1f}..{x1:.1f}")
        return measure

    target = durations.target_quarters(*DEFAULT_TIME_SIG)
    fitted, exact = durations.fit_durations(
        durations.proportions(beat_xs, x1, target), target)
    if not exact:
        total = sum(d.quarters for d in fitted)
        warn.add(index, "duration_mismatch",
                 f"합 {total:.3f} / 목표 {target:.3f}, "
                 f"durs={[d.value for d in fitted]}")

    for beat_x, duration in zip(beat_xs, fitted):
        notes = _beat_notes(fret_glyphs, beat_x, system, index, warn)
        chord = stroke = None
        if not notes:                   # 슬래시 beat — 코드 보이싱으로 채운다
            chord = _chord_at(tokens, beat_x)
            voicing = chords.voicing_for(chord)
            if chord is None:
                warn.add(index, "unknown_chord",
                         f"x={beat_x:.1f} 슬래시 beat 앞에 코드명이 없다")
            elif voicing is None:
                warn.add(index, "unknown_chord", f"{chord} — VOICINGS 에 없음")
            notes = [{"string": s, "fret": f} for s, f in (voicing or ())]
            stroke = _stroke_at(geo, beat_x)

        measure["beats"].append({
            "x": round(beat_x, 2),
            "duration": duration.value,
            "dotted": duration.dotted,
            "chord": chord,
            "stroke": stroke,
            "notes": notes,
        })
    return measure


def extract_ir(pdf_path: str, tempo: int | None = None,
               title: str | None = None, artist: str | None = None) -> dict:
    """PDF 전체를 IR 로 만든다.

    제목은 PDF 메타데이터를 쓰지 않는다 — 대상 PDF 의 메타 제목은 mojibake 된
    "Ÿfl˘'‹.musx" 다. 파일명 stem 을 쓰고 인자로 덮어쓸 수 있다.
    """
    document = pymupdf.open(pdf_path)
    warn = _Warnings()
    measures: list[dict] = []
    saw_text = False

    for page in document:
        geo = geometry.load_page_geometry(page)
        if geo.glyphs:
            saw_text = True
        for system in geometry.find_systems(geo):
            all_bounds = geometry.measure_bounds(geo, system)
            if not all_bounds:
                continue
            tokens = _chord_tokens(geo, system, all_bounds[-1][1] + 1.0)
            for bounds in all_bounds:
                measures.append(_build_measure(
                    geo, system, bounds, len(measures), tokens, warn))

    if not saw_text:
        raise NotATabPdf(
            f"{pdf_path}: 텍스트 레이어가 없다. 스캔 이미지 PDF 는 변환할 수 없다")
    if not measures:
        raise NotATabPdf(f"{pdf_path}: 6줄 타브 staff 를 찾지 못했다")

    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    return {
        "title": title if title is not None else stem,
        "artist": artist if artist is not None else "",
        "tempo": tempo or DEFAULT_TEMPO,
        "tuning": list(STANDARD_TUNING),
        "measures": measures,
        "warnings": warn.items,
    }
