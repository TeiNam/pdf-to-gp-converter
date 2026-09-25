"""PDF 글리프에서 프렛·코드·연주법·주석을 읽는다.

좌표를 수집하는 geometry와 마디를 조립하는 extract 사이의 표기 해석을 맡는다.
"""

from . import bands, chords, geometry, lyrics, rhythm, smufl
from .bands import CHORD_BAND_HEIGHT
from .durations import REST_NAMES
from .marks import ARTICULATION_KINDS

# 프렛 숫자로 인정할 글리프 크기 상한 (pt). 'T','A','B' 세로 라벨은 14.4pt 로 더 크다
MAX_FRET_GLYPH_SIZE = 11.0
# 꾸밈음/정규 프렛을 가를 크기 차 하한 (pt). 시스템의 숫자 크기가 이보다
# 좁게 모여 있으면 꾸밈음이 없는 것이다 — 절대 문턱을 쓰면 정규 프렛이
# 8pt 인 악보의 모든 음이 꾸밈음으로 오인된다 (실측: 정규 9.3, 꾸밈음 8.0)
GRACE_SIZE_MIN_DELTA = 1.0
# 셋잇단 숫자 '3' — 이 글리프가 타브 대역에 있는 마디만 셋잇단 후보를 연다
TUPLET_3 = chr(0xE883)
# 같은 beat(화음)로 묶을 x 허용 오차 (pt)
BEAT_CLUSTER_TOLERANCE = 2.0
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

# 두 자리 프렛 의심 구간 — 같은 줄 인접 숫자의 origin 간격 상한 (pt).
# 실측: 이 PDF 의 별개 음은 6.8pt 이상 떨어져 있어 오탐이 없다
KERNED_DIGIT_ORIGIN_GAP = 6.0

# 코드포인트 구획은 SMuFL 표준이라 smufl 모듈이 단독으로 들고 있다
SMUFL_SLASH_RANGE = smufl.SLASH
SMUFL_ARTICULATION_RANGE = smufl.ARTICULATION
SMUFL_TIMESIG_DIGIT_RANGE = smufl.TIMESIG_DIGIT
# 타브에 찍히는 연주법 약어 → 우리 연주법 이름. 표기는 같은 줄의 두 음 사이에
# 놓이고 효과는 앞 음이 갖는다. GP 는 해머온/풀오프를 한 플래그로 다룬다.
# 값은 corrections.TECHNIQUE_KINDS 의 부분집합이어야 한다 — 아니면 build 가
# 조용히 버린다 (테스트로 묶어 뒀다).
TECHNIQUE_GLYPHS = {"H": "hammer", "P": "hammer", "S": "slide"}
# SMuFL 음악 기호는 유니코드 사설 영역에 있다 — 가사·코드 판정에서 제외한다
PRIVATE_USE_RANGE = smufl.PRIVATE_USE
SMUFL_REST_RANGE = smufl.REST
# X 음표머리(noteheadXBlack) — 타브 대역에서는 뮤트 노트다. 프렛 숫자가 없는
# 자기만의 리듬 이벤트라 beat 으로 세워야 한다 (실측: m23·m48 에 8개)
X_NOTEHEAD = chr(0xE0A9)
SMUFL_STROKE_DOWN = ""
SMUFL_STROKE_UP = ""


_in_tab_band = bands.in_tab_band
_snap_to_string = bands.snap_to_string
_band_of = bands.band_of
_nearest_beat = bands.nearest_beat
_is_lyric_syllable = lyrics.is_syllable


def _in_range(char: str, bounds: tuple[int, int]) -> bool:
    low, high = bounds
    return low <= ord(char) <= high


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


def _grace_cutoff(geo, system, letter_index) -> float | None:
    """이 시스템의 숫자 크기 분포에서 꾸밈음 문턱을 정한다. 없으면 None.

    꾸밈음은 정규 프렛보다 작게 조판된다 — 크기가 두 무리로 갈릴 때만
    작은 쪽을 꾸밈음으로 본다. 크기가 한 종류면 전부 정규다.
    """
    sizes = sorted({round(g.size, 1) for g in geo.glyphs
                    if g.char.isdigit() and g.size <= MAX_FRET_GLYPH_SIZE
                    and _in_tab_band(g, system)
                    and not _has_adjacent_letter(letter_index, g)})
    if len(sizes) < 2 or sizes[-1] - sizes[0] < GRACE_SIZE_MIN_DELTA:
        return None
    return (sizes[0] + sizes[-1]) / 2


def _fret_glyphs(geo, system, x0, x1, letter_index,
                 grace_cutoff: float | None) -> list[geometry.Glyph]:
    """폰트 이름에 의존하지 않는다 — 숫자 + 타브 대역 + 크기 범위 + 텍스트 배제."""
    return [g for g in geo.glyphs
            if x0 <= g.x < x1 and g.char.isdigit()
            and g.size <= MAX_FRET_GLYPH_SIZE
            and (grace_cutoff is None or g.size > grace_cutoff)
            and _in_tab_band(g, system)
            and not _has_adjacent_letter(letter_index, g)]


def _grace_glyphs(geo, system, x0, x1, letter_index,
                  grace_cutoff: float | None) -> list[geometry.Glyph]:
    """꾸밈음 프렛 숫자 — 정규보다 작게 조판된다."""
    if grace_cutoff is None:
        return []
    return [g for g in geo.glyphs
            if x0 <= g.x < x1 and g.char.isdigit()
            and g.size <= grace_cutoff and _in_tab_band(g, system)
            and not _has_adjacent_letter(letter_index, g)]


def _merge_two_digit_frets(geo, glyphs, system, index, warn,
                           *, sources: dict | None = None) -> list[geometry.Glyph]:
    """같은 줄에서 커닝으로 붙은 숫자쌍을 두 자리 프렛 한 음으로 병합한다.

    '12' 는 숫자 글리프 2개(origin 간격 ~5pt)로 오는데, beat 클러스터 허용
    (2pt)보다 넓어서 병합하지 않으면 1프렛·2프렛 **별개 beat 둘**이 된다.
    배율·조판에 따라 별개 음도 6pt보다 가까울 수 있다. 각각 독립된 기둥이
    있으면 거리와 무관하게 두 음으로 보존한다.
    병합값이 프렛 상한을 넘는 쌍(붙은 '2''5' 따위)은 별개 음으로 두되 경고한다.
    """
    if sources is not None:
        sources.update((glyph, glyph) for glyph in glyphs)
    by_string: dict[int, list[geometry.Glyph]] = {}
    loose: list[geometry.Glyph] = []
    for glyph in glyphs:
        string = _snap_to_string(glyph.y, system.tab_ys)
        if string is None:
            loose.append(glyph)         # 스냅 실패는 _beat_notes 가 경고한다
        else:
            by_string.setdefault(string, []).append(glyph)
    merged: list[geometry.Glyph] = loose
    for string, group in by_string.items():
        group.sort(key=lambda g: g.x)
        position = 0
        while position < len(group):
            left = group[position]
            right = group[position + 1] if position + 1 < len(group) else None
            apart = (right is None
                     or (right.x - left.x_end >= KERNED_DIGIT_INK_GAP
                         and right.x - left.x >= KERNED_DIGIT_ORIGIN_GAP))
            if not apart:
                left_stems = set(rhythm.stems_for(geo, system, left))
                right_stems = set(rhythm.stems_for(geo, system, right))
                apart = bool(left_stems and right_stems
                             and left_stems.isdisjoint(right_stems))
            if apart:
                merged.append(left)
                position += 1
                continue
            value = int(left.char + right.char)
            if value > MAX_FRET:
                warn.add(index, "unsupported_glyph",
                         f"string{string} 의 {left.char!r}{right.char!r} 가 "
                         f"붙어 있으나 {value}프렛은 불가능하다 — 별개 음으로 둔다")
                merged.append(left)
                position += 1
                continue
            # 두 자리 수는 같은 음표 중심의 한 자리 수보다 왼쪽에서 시작한다.
            # 중심을 한 글자 폭의 기준 좌표로 환산해 화음이 두 beat로 갈리지 않게 한다.
            digit_width = ((left.x_end - left.x) + (right.x_end - right.x)) / 2
            combined = geometry.Glyph(
                x=(left.x + right.x_end - digit_width) / 2,
                y=left.y, x_end=right.x_end,
                char=left.char + right.char, font=left.font, size=left.size)
            merged.append(combined)
            if sources is not None:
                sources[left] = sources[right] = combined
            position += 2
    return sorted(merged, key=lambda g: g.x)


def _slash_xs(geo, system, x0, x1) -> list[float]:
    return [g.x for g in geo.glyphs
            if x0 <= g.x < x1 and _in_range(g.char, SMUFL_SLASH_RANGE)
            and _in_tab_band(g, system)]


def _dead_glyphs(geo, system, x0, x1) -> list[geometry.Glyph]:
    """타브 대역의 X 음표머리 — 뮤트 노트 이벤트."""
    return [g for g in geo.glyphs
            if x0 <= g.x < x1 and g.char == X_NOTEHEAD
            and _in_tab_band(g, system)]


def _rest_glyphs(geo, system, x0, x1) -> list[geometry.Glyph]:
    """타브 대역의 쉼표 — 자기 x 자리를 차지하는 무음 이벤트."""
    return [g for g in geo.glyphs
            if x0 <= g.x < x1 and smufl.name(g.char) in REST_NAMES
            and _in_tab_band(g, system)]


# 코드 행에서 한 행으로 볼 baseline y 오차 (pt). 위첨자 성질('C⁷'의 7)은
# 루트보다 2~4pt 위에 작게 놓인다 — 같은 행이다. 별개 텍스트 행은 10pt 이상
# 떨어지므로 붙지 않는다.
CHORD_ROW_TOLERANCE = 5.0


def _chord_char(glyph: geometry.Glyph) -> str | None:
    """코드명 문자로서의 글리프. 음악 기호는 ASCII 로 눕히고 나머지는 버린다.

    - SMuFL 임시표(E260/E262)를 통째로 버리면 'C♯m' 이 'Cm' 으로 오독된다
    - 리터럴 ♯/♭ 는 cp949 에 없어 GP5 직렬화가 UnicodeEncodeError 로 죽는다
    - 세뇨·코다 같은 그 외 사설 영역 기호는 행 병합에 끼면 이웃 코드명을
      삼켜 토큰 전체가 판정 탈락한다 (실측 3건) — 제외
    """
    name = smufl.name(glyph.char)
    if name == "accidentalSharp":
        return "#"
    if name == "accidentalFlat":
        return "b"
    if _in_range(glyph.char, PRIVATE_USE_RANGE):
        return None
    return {"♯": "#", "♭": "b"}.get(glyph.char, glyph.char)


def _chord_tokens(geo, system, x_limit: float) -> list[tuple[float, str]]:
    """이 시스템의 코드 행에서 (x, 코드명) 을 복원한다.

    행(y 클러스터) 안에서 직전 문자의 잉크 끝(`x_end`) 기준으로 이어붙인다.
    origin 기준으로는 'Cadd9' 가 'C' + 'add9' 로 쪼개진다 (실측 C→a origin
    간격 7.68pt, 잉크끝 기준 0.90pt). baseline 완전 일치를 요구하면 위첨자
    성질이 루트에서 떨어져 나가 'C7' 이 'C' 로 읽힌다.
    """
    top = system.melody_ys[0]
    band = sorted((g for g in geo.glyphs
                   if top - CHORD_BAND_HEIGHT <= g.y < top and g.x < x_limit
                   and _chord_char(g) is not None),
                  key=lambda g: (g.y, g.x))
    rows: list[list[geometry.Glyph]] = []
    for glyph in band:
        if rows and glyph.y - rows[-1][0].y <= CHORD_ROW_TOLERANCE:
            rows[-1].append(glyph)
        else:
            rows.append([glyph])
    tokens: list[tuple[float, str]] = []
    for row in rows:
        row.sort(key=lambda g: g.x)
        text, start_x, prev_end = "", 0.0, None
        for glyph in row:
            if prev_end is not None and glyph.x - prev_end > CHORD_CHAR_GAP:
                tokens.append((start_x, text))
                text = ""
            if not text:
                start_x = glyph.x
            text += _chord_char(glyph)
            prev_end = glyph.x_end
        if text:
            tokens.append((start_x, text))
    # x 오름차순 보장 — _chord_at 이 정렬을 가정하고 break 로 조기 종료한다
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


# 박자표 숫자 무리를 서로 다른 표기로 볼 x 간격 (pt). 한 표기의 숫자들은
# 몇 pt 안에 모이고, 마디 중간 박자 변경은 마디 하나(수십 pt) 이상 떨어진다




def _techniques(geo, system, x0, x1, fret_glyphs, letter_index,
                grace_glyphs=()) -> list[dict]:
    """연주법 표기를 (x, 대상 줄, 종류) 로 뽑는다.

    표기의 y 는 대상 줄과 무관하다 (실측: 표기 y 는 5·3·4·1번줄로 흩어지는데
    대상은 전부 2번줄이었다). 표기 x 직전의 프렛 노트가 효과를 갖는다.
    """
    result = []
    for glyph in geo.glyphs:
        if not (x0 <= glyph.x < x1) or not _in_tab_band(glyph, system):
            continue
        kind = TECHNIQUE_GLYPHS.get(glyph.char)
        if kind is None or glyph.size > MAX_FRET_GLYPH_SIZE:
            continue
        if _has_adjacent_letter(letter_index, glyph):
            continue                    # 주석 문장 속 글자다
        before = [f for f in [*fret_glyphs, *grace_glyphs] if f.x <= glyph.x]
        if not before:
            continue
        source = max(before, key=lambda f: f.x)
        string = _snap_to_string(source.y, system.tab_ys)
        if string is None:
            continue
        result.append({"x": source.x, "string": string, "kind": kind,
                       "grace": source in grace_glyphs})
    return result








def _annotation_glyphs(geo, system, bounds, beat_xs, fret_glyphs) -> list[dict]:
    """AI 보정에 넘길 주석 글리프. IR 에 이미 들어간 것은 뺀다.

    프렛 숫자와 가사 음절을 걷어내면 남는 것이 곧 "코드가 뜻을 몰라 흘린 것"
    이다 — 쉼표·아티큘레이션·다이내믹·H/P/S·D.S. al Coda·늘임표가 여기 모인다.
    코드명·스트로크는 남겨둔다: 코드가 잘못 읽었을 수 있어 AI 가 대조해야 한다.
    """
    x0, x1 = bounds
    claimed = set(fret_glyphs)
    glyphs = []
    for glyph in geo.glyphs:
        if not (x0 <= glyph.x < x1) or glyph in claimed:
            continue
        if _is_lyric_syllable(glyph, system):
            continue
        band = _band_of(glyph, system)
        if band is None:
            continue
        entry = {
            "band": band,
            "x": round(glyph.x, 1),
            "size": glyph.size,
            "beat": _nearest_beat(beat_xs, glyph.x),
        }
        if band == "tab":
            # 타브 대역 기호는 어느 줄에 놓였는지가 뜻이다 — X 음표머리(뮤트)나
            # 하모닉스 표시를 줄까지 특정하려면 y 를 줄 번호로 바꿔줘야 한다.
            entry["string"] = _snap_to_string(glyph.y, system.tab_ys)
        symbol = smufl.label(glyph.char)
        if symbol is None:
            entry["char"] = glyph.char
        else:
            # 사설 영역 문자는 그대로 넘기면 읽을 수 없다. 코드포인트와 표준 이름을
            # 줘야 '아티큘레이션' 이 악센트인지 스타카토인지 판단할 수 있다.
            entry["codepoint"] = smufl.codepoint(glyph.char)
            entry["symbol"] = symbol
            entry["smufl_name"] = smufl.name(glyph.char)
        glyphs.append(entry)
    return sorted(glyphs, key=lambda g: g["x"])


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


# 타브 대역에서 반영하지 못하는 SMuFL 구획 → 집계 라벨. 멜로디 staff 의 같은
# 기호(노래 선율의 쉼표·트릴)는 기타 파트가 아니므로 세지 않는다.
_UNSUPPORTED_TAB_RANGES: tuple[tuple[tuple[int, int], str], ...] = (
    (smufl.REST, "쉼표"),
    (smufl.ARTICULATION, "아티큘레이션"),
    (smufl.ORNAMENT, "장식음"),
    (smufl.TREMOLO, "트레몰로"),
    (smufl.TUPLET, "잇단음표"),         # '3' 은 반영, 5잇단 등은 경고
)


def _warn_unsupported(geo, system, x0, x1, index, warn) -> None:
    """반영하지 못하는 표기를 종류별 1건으로 집계해 남긴다. 조용히 버리지 않는다.

    - 타브 대역 쉼표: x간격 기반 음길이를 틀어뜨린다
    - 악센트 등 아티큘레이션: 음정·리듬에는 영향 없지만 표현이 사라진다
    - 장식음·트레몰로: GP5 로 옮기려면 프렛·박자 인자가 필요해 못 옮긴다
    - 반복 기호(도트 등)·늘임표: 시스템 어디에 있든 연주 순서·길이에 영향

    H/P/S 연주법은 `_techniques` 가, segno/coda 는 `_glyph_directions` 가
    반영하므로 여기서 세지 않는다.
    """
    counts: dict[str, int] = {}
    handled_directions = {"segno", "coda",
                          "repeatLeft", "repeatRight", "repeatRightLeft"}
    for glyph in geo.glyphs:
        if not (x0 <= glyph.x < x1):
            continue
        if _in_tab_band(glyph, system):
            name = smufl.name(glyph.char)
            if (name in ARTICULATION_KINDS or name in REST_NAMES
                    or glyph.char == TUPLET_3):
                continue                # 이미 반영되는 표기다
            for bounds_range, label in _UNSUPPORTED_TAB_RANGES:
                if _in_range(glyph.char, bounds_range):
                    counts[label] = counts.get(label, 0) + 1
                    break
            continue
        if _band_of(glyph, system) is None:
            continue                    # 다른 시스템 몫이다
        if (_in_range(glyph.char, smufl.REPEAT)
                and smufl.name(glyph.char) not in handled_directions):
            counts["반복기호"] = counts.get("반복기호", 0) + 1
        elif _in_range(glyph.char, smufl.HOLD_PAUSE):
            counts["늘임표·숨표"] = counts.get("늘임표·숨표", 0) + 1
    for label, count in sorted(counts.items()):
        detail = f"{label} {count}개를 반영하지 못했다"
        if label == "쉼표":
            detail += " — x간격 기반 음길이가 틀어질 수 있다"
        warn.add(index, "unsupported_glyph", detail)


def _beat_notes(fret_glyphs, dead_glyphs, beat_x, system, index, warn) -> list[dict]:
    notes = []
    for glyph in fret_glyphs + dead_glyphs:
        if abs(glyph.x - beat_x) > BEAT_CLUSTER_TOLERANCE:
            continue
        string = _snap_to_string(glyph.y, system.tab_ys)
        if string is None:
            warn.add(index, "unsnapped_digit",
                     f"글리프 {glyph.char!r} at ({glyph.x:.1f}, {glyph.y:.1f}) "
                     f"가 어느 줄에도 스냅되지 않았다")
            continue
        if glyph.char == X_NOTEHEAD:
            # 뮤트 노트 — 프렛은 뜻이 없어 0 을 넣고 dead 로 표시한다
            notes.append({"string": string, "fret": 0, "dead": True})
        else:
            notes.append({"string": string, "fret": int(glyph.char)})
    return notes
