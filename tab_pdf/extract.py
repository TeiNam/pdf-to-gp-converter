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
# 글리프가 멜로디 staff 안에 있다고 볼 상하 여유 (pt)
SPAN_SLACK = 6.0
# 같은 baseline 으로 볼 y 오차 (pt)
SAME_BASELINE_TOLERANCE = 0.6
# 숫자에 알파벳이 붙어 있다고 볼 잉크 간격 (pt) — 텍스트 주석 배제용
LETTER_ADJACENCY_GAP = 2.5
# 숫자쌍이 커닝으로 붙었다고 볼 잉크 간격 (pt). 실측: 같은 숫자 -1.89, 별개 음 0.7 이상
KERNED_DIGIT_INK_GAP = 0.0
# 기타 프렛 상한 — 이보다 큰 병합값은 두 자리 프렛일 수 없다
MAX_FRET = 24

# 두 자리 프렛 의심 구간 — 같은 줄 인접 숫자의 origin 간격 상한 (pt).
# 실측: 이 PDF 의 별개 음은 6.8pt 이상 떨어져 있어 오탐이 없다
KERNED_DIGIT_ORIGIN_GAP = 6.0

SMUFL_SLASH_RANGE = (0xE100, 0xE10F)            # SMuFL Slash noteheads
SMUFL_ARTICULATION_RANGE = (0xE4A0, 0xE4BF)     # 악센트 등 — 반영하지 않고 경고
SMUFL_TIMESIG_DIGIT_BASE = 0xE080               # E080='0' … E089='9'
SMUFL_TIMESIG_DIGIT_RANGE = (0xE080, 0xE089)
# 기타 연주법 약어. 표기는 같은 줄의 두 음 사이에 놓이고, 효과는 앞 음이 갖는다.
# GP 는 해머온/풀오프를 한 플래그로 다룬다.
TECHNIQUE_KINDS = {"H": "hammer", "P": "hammer", "S": "slide"}
# 표기에 딸린 방향 주석 문자 ('S.D' 의 '.', 'D') — 별도 의미로 쓰지 않는다
TECHNIQUE_ANNOTATION = frozenset(".DU")
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


def _snap_to_string(y: float, tab_ys: tuple[float, ...]) -> int | None:
    """baseline y 를 가장 가까운 타브 선에 붙여 줄 번호(1..6)를 돌려준다."""
    index = min(range(len(tab_ys)), key=lambda i: abs(y - tab_ys[i]))
    if abs(y - tab_ys[index]) > MAX_STRING_SNAP_DISTANCE:
        return None
    return index + 1        # tab_ys 는 위→아래, 위가 고음 E = string 1


def build_letter_index(geo) -> dict[float, list[geometry.Glyph]]:
    """알파벳 글리프를 baseline 별로 묶어 둔다.

    페이지당 한 번만 만든다. 글리프마다 전체를 재순회하면 O(글리프²) 이 되어
    3페이지에서 인접 검사만 17만 회가 넘었다.
    """
    index: dict[float, list[geometry.Glyph]] = {}
    for glyph in geo.glyphs:
        if glyph.char.isalpha():
            index.setdefault(round(glyph.y, 1), []).append(glyph)
    return index


def _has_adjacent_letter(letter_index, glyph: geometry.Glyph) -> bool:
    """같은 baseline 에 알파벳이 붙어 있으면 프렛 숫자가 아니라 텍스트다.

    이 악보에는 타브 staff 바로 위에 "with 16beat arp play" 같은 연주 지시가 있고,
    그 '1','6' 이 타브 1선에서 1.2pt 거리라 선 스냅을 통과한다. 실제 프렛 숫자
    294개는 인접 알파벳이 하나도 없고 스냅 거리가 3.3~3.5pt 로 일정하다.
    """
    base = round(glyph.y, 1)
    step = round(SAME_BASELINE_TOLERANCE, 1)
    for key in (base - step, base, base + step):
        for other in letter_index.get(round(key, 1), ()):
            if abs(other.y - glyph.y) > SAME_BASELINE_TOLERANCE:
                continue
            if (abs(other.x - glyph.x_end) < LETTER_ADJACENCY_GAP
                    or abs(glyph.x - other.x_end) < LETTER_ADJACENCY_GAP):
                return True
    return False


def _fret_glyphs(geo, system, x0, x1, letter_index) -> list[geometry.Glyph]:
    """폰트 이름에 의존하지 않는다 — 숫자 + 타브 대역 + 크기 상한 + 텍스트 배제."""
    return [g for g in geo.glyphs
            if x0 <= g.x < x1 and g.char.isdigit()
            and g.size <= MAX_FRET_GLYPH_SIZE and _in_tab_band(g, system)
            and not _has_adjacent_letter(letter_index, g)]


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
            if (right.x - left.x_end >= KERNED_DIGIT_INK_GAP
                    and right.x - left.x >= KERNED_DIGIT_ORIGIN_GAP):
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
    # x 오름차순 보장 — _chord_at 이 정렬을 가정하고 break 로 조기 종료한다.
    # 코드 대역에는 baseline 이 여러 줄 있을 수 있어 (y, x) 순서로는 부족하다.
    return sorted(((x, name) for x, name in tokens
                   if chords.looks_like_chord(name)), key=lambda pair: pair[0])


def _chord_at(tokens: list[tuple[float, str]], x: float) -> str | None:
    """x 이전(또는 같은 위치)의 가장 가까운 코드명.

    `tokens` 는 x 오름차순이어야 한다 — 아래 break 가 그 전제에 의존한다.
    """
    current = None
    for token_x, name in tokens:
        if token_x <= x + CHORD_APPLY_SLACK:
            current = name
        else:
            break
    return current


def _stroke_at(geo, system: geometry.System, x: float) -> str | None:
    """이 시스템 타브 대역에서만 스트로크 기호를 찾는다.

    한 페이지에 시스템이 4~5개 쌓여 있어 x 만으로 찾으면 다른 시스템의 기호를
    집어온다. 실측으로 실제 9개가 27개로 부풀었다.
    """
    for glyph in geo.glyphs:
        if abs(glyph.x - x) > STROKE_X_WINDOW or not _in_tab_band(glyph, system):
            continue
        if glyph.char == SMUFL_STROKE_DOWN:
            return "down"
        if glyph.char == SMUFL_STROKE_UP:
            return "up"
    return None


def _detect_time_signature(geo, system: geometry.System) -> tuple[int, int] | None:
    """멜로디 staff 의 SMuFL timeSig 숫자에서 박자표를 읽는다.

    분자·분모가 같은 x 에 위로/아래로 쌓여 있다. 못 찾으면 None.
    """
    low, high = SMUFL_TIMESIG_DIGIT_RANGE
    digits = [g for g in geo.glyphs
              if low <= ord(g.char) <= high
              and system.melody_ys[0] - SPAN_SLACK <= g.y
              <= system.melody_ys[-1] + SPAN_SLACK]
    if len(digits) < 2:
        return None
    columns: dict[float, list[geometry.Glyph]] = {}
    for glyph in sorted(digits, key=lambda g: (g.x, g.y)):
        key = next((k for k in columns if abs(k - glyph.x) <= BEAT_CLUSTER_TOLERANCE),
                   glyph.x)
        columns.setdefault(key, []).append(glyph)
    for _, column in sorted(columns.items()):
        if len(column) != 2:
            continue
        upper, lower = sorted(column, key=lambda g: g.y)
        return (ord(upper.char) - SMUFL_TIMESIG_DIGIT_BASE,
                ord(lower.char) - SMUFL_TIMESIG_DIGIT_BASE)
    return None


def _techniques(geo, system, x0, x1, fret_glyphs, letter_index) -> list[dict]:
    """연주법 표기를 (x, 대상 줄, 종류) 로 뽑는다.

    표기의 y 는 대상 줄과 무관하다 (실측: 표기 y 는 5·3·4·1번줄로 흩어지는데
    대상은 전부 2번줄이었다). 표기 x 직전의 프렛 노트가 효과를 갖는다.
    """
    result = []
    for glyph in geo.glyphs:
        if not (x0 <= glyph.x < x1) or not _in_tab_band(glyph, system):
            continue
        kind = TECHNIQUE_KINDS.get(glyph.char)
        if kind is None or glyph.size > MAX_FRET_GLYPH_SIZE:
            continue
        if _has_adjacent_letter(letter_index, glyph):
            continue                    # 주석 문장 속 글자다
        before = [f for f in fret_glyphs if f.x <= glyph.x]
        if not before:
            continue
        source = max(before, key=lambda f: f.x)
        string = _snap_to_string(source.y, system.tab_ys)
        if string is None:
            continue
        result.append({"x": source.x, "string": string, "kind": kind})
    return result


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
    """반영하지 못하는 표기를 종류별 1건으로 집계해 남긴다. 조용히 버리지 않는다.

    - 타브 대역 쉼표: x간격 기반 음길이를 틀어뜨린다
    - 악센트 등 아티큘레이션: 음정·리듬에는 영향 없지만 표현이 사라진다

    H/P/S 연주법은 `_techniques` 가 반영하므로 여기서 세지 않는다.
    """
    counts: dict[str, int] = {}
    for glyph in geo.glyphs:
        if not (x0 <= glyph.x < x1) or not _in_tab_band(glyph, system):
            continue
        if _in_range(glyph.char, SMUFL_REST_RANGE):
            counts["쉼표"] = counts.get("쉼표", 0) + 1
        elif _in_range(glyph.char, SMUFL_ARTICULATION_RANGE):
            counts["아티큘레이션"] = counts.get("아티큘레이션", 0) + 1
    for label, count in sorted(counts.items()):
        detail = f"{label} {count}개를 반영하지 못했다"
        if label == "쉼표":
            detail += " — x간격 기반 음길이가 틀어질 수 있다"
        warn.add(index, "unsupported_glyph", detail)


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


def _build_measure(geo, system, bounds, index, tokens, warn,
                   time_sig: tuple[int, int], letter_index) -> dict:
    x0, x1 = bounds
    fret_glyphs = _fret_glyphs(geo, system, x0, x1, letter_index)
    slash_xs = _slash_xs(geo, system, x0, x1)
    kind = _classify(fret_glyphs, slash_xs)
    _warn_unsupported(geo, system, x0, x1, index, warn)
    _warn_kerned_digit_pairs(fret_glyphs, system, index, warn)

    techniques = _techniques(geo, system, x0, x1, fret_glyphs, letter_index)
    beat_xs = _cluster([g.x for g in fret_glyphs] + slash_xs)
    measure = {"index": index, "time_sig": list(time_sig),
               "kind": kind, "beats": []}
    if not beat_xs:
        warn.add(index, "empty_measure", f"판정 {kind}, x {x0:.1f}..{x1:.1f}")
        return measure

    target = durations.target_quarters(*time_sig)
    fitted, exact = durations.fit_durations(
        durations.proportions(beat_xs, x1, target), target)
    if not exact:
        total = sum(d.quarters for d in fitted)
        warn.add(index, "duration_mismatch",
                 f"합 {total:.3f} / 목표 {target:.3f}, "
                 f"durs={[d.value for d in fitted]}")

    for beat_x, duration in zip(beat_xs, fitted):
        notes = _beat_notes(fret_glyphs, beat_x, system, index, warn)
        # 슬래시에서 온 beat 인지 좌표로 판정한다. 노트가 비었다는 이유만으로
        # 코드 보이싱을 채우면, 줄 스냅 실패한 프렛 하나가 추측한 화음으로 증폭된다.
        from_slash = any(abs(slash_x - beat_x) <= BEAT_CLUSTER_TOLERANCE
                         for slash_x in slash_xs)
        chord = stroke = None
        if from_slash and not notes:
            chord = _chord_at(tokens, beat_x)
            voicing = chords.voicing_for(chord)
            if chord is None:
                warn.add(index, "unknown_chord",
                         f"x={beat_x:.1f} 슬래시 beat 앞에 코드명이 없다")
            elif voicing is None:
                warn.add(index, "unknown_chord", f"{chord} — VOICINGS 에 없음")
            notes = [{"string": s, "fret": f} for s, f in (voicing or ())]
            stroke = _stroke_at(geo, system, beat_x)
        elif not notes:
            warn.add(index, "empty_beat",
                     f"x={beat_x:.1f} 에 프렛 숫자가 있었으나 노트가 만들어지지 않았다")

        measure["beats"].append({
            "x": round(beat_x, 2),
            "duration": duration.value,
            "dotted": duration.dotted,
            "chord": chord,
            "stroke": stroke,
            "techniques": [
                {"string": t["string"], "kind": t["kind"]} for t in techniques
                if abs(t["x"] - beat_x) <= BEAT_CLUSTER_TOLERANCE
            ],
            "notes": notes,
        })
    return measure


def extract_ir(pdf_path: str, tempo: int | None = None,
               title: str | None = None, artist: str | None = None) -> dict:
    """PDF 전체를 IR 로 만든다.

    제목은 PDF 메타데이터를 쓰지 않는다 — 대상 PDF 의 메타 제목은 mojibake 된
    "Ÿfl˘'‹.musx" 다. 파일명 stem 을 쓰고 인자로 덮어쓸 수 있다.
    """
    warn = _Warnings()
    measures: list[dict] = []
    saw_text = False

    time_sig = DEFAULT_TIME_SIG
    with pymupdf.open(pdf_path) as document:
        for page in document:
            geo = geometry.load_page_geometry(page)
            letter_index = build_letter_index(geo)
            if geo.glyphs:
                saw_text = True
            for system in geometry.find_systems(geo):
                all_bounds = geometry.measure_bounds(geo, system)
                if not all_bounds:
                    continue
                detected = _detect_time_signature(geo, system)
                if detected is not None and detected != time_sig:
                    time_sig = detected
                    if detected != DEFAULT_TIME_SIG:
                        warn.add(len(measures), "time_signature",
                                 f"{detected[0]}/{detected[1]} 박자를 감지했다 "
                                 f"— 이 경로는 실제 악보로 검증되지 않았다")
                tokens = _chord_tokens(geo, system, all_bounds[-1][1] + 1.0)
                for bounds in all_bounds:
                    measures.append(_build_measure(
                        geo, system, bounds, len(measures), tokens, warn,
                        time_sig, letter_index))

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
