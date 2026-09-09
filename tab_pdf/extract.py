"""PDF 기하를 음악적 중간표현(IR)으로 해석한다."""

import os
import re
from dataclasses import dataclass, field

import pymupdf

from . import chords, durations, geometry, smufl

STANDARD_TUNING = (64, 59, 55, 50, 45, 40)      # 1=고음 E … 6=저음 E
DEFAULT_TEMPO = 80
DEFAULT_TIME_SIG = (4, 4)

# 프렛 숫자로 인정할 글리프 크기 상한 (pt). 'T','A','B' 세로 라벨은 14.4pt 로 더 크다
MAX_FRET_GLYPH_SIZE = 11.0
# 이 크기 이하의 프렛 숫자는 꾸밈음이다 (실측: 정규 9.3pt, 꾸밈음 8.0pt).
# 정식 beat 으로 세우면 리듬이 왜곡된다 — 다음 음의 grace 로 붙인다.
GRACE_FRET_GLYPH_SIZE = 8.5
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
# 타이 곡선의 끝점이 beat x 에서 이보다 멀면 음악 호가 아니다 (pt).
# 배경 삽화의 곡선이 타브 대역을 지나가도 타이로 오인하지 않게 한다
TIE_ENDPOINT_TOLERANCE = 15.0

# 코드포인트 구획은 SMuFL 표준이라 smufl 모듈이 단독으로 들고 있다
SMUFL_SLASH_RANGE = smufl.SLASH
SMUFL_ARTICULATION_RANGE = smufl.ARTICULATION     # 악센트 등 — 반영하지 않고 경고
SMUFL_TIMESIG_DIGIT_BASE = smufl.TIMESIG_DIGIT[0]  # E080='0' … E089='9'
SMUFL_TIMESIG_DIGIT_RANGE = smufl.TIMESIG_DIGIT
# 타브에 찍히는 연주법 약어 → 우리 연주법 이름. 표기는 같은 줄의 두 음 사이에
# 놓이고 효과는 앞 음이 갖는다. GP 는 해머온/풀오프를 한 플래그로 다룬다.
# 값은 corrections.TECHNIQUE_KINDS 의 부분집합이어야 한다 — 아니면 build 가
# 조용히 버린다 (테스트로 묶어 뒀다).
TECHNIQUE_GLYPHS = {"H": "hammer", "P": "hammer", "S": "slide"}
# 타브 대역 아티큘레이션 글리프 → 박 전체 연주법. 좌표·정체가 결정론적이라
# AI 를 거칠 이유가 없다 (실측: 악센트 44개가 --ai 에서만 살아났다).
# 값은 corrections.BEAT_TECHNIQUES 의 부분집합이어야 한다 — 테스트로 묶어 둔다.
ARTICULATION_KINDS = {
    "articAccentAbove": "accent", "articAccentBelow": "accent",
    "articMarcatoAbove": "heavy_accent", "articMarcatoBelow": "heavy_accent",
    "articStaccatoAbove": "staccato", "articStaccatoBelow": "staccato",
    "articStaccatissimoAbove": "staccato", "articStaccatissimoBelow": "staccato",
}
# 가사는 멜로디 staff 아래 ~ 타브 staff 위 대역에 놓인다.
# 이 대역에는 영문 연주 지시("with 16beat arp play")도 있어 한글만 취한다.
# 영문 가사 악보에는 이 규칙이 통하지 않는다 (이 곡은 한글 가사다).
LYRIC_MIN_CODEPOINT = 0x1100
# SMuFL 음악 기호는 유니코드 사설 영역에 있어 위 하한을 그냥 넘는다 — 제외해야 한다
PRIVATE_USE_RANGE = smufl.PRIVATE_USE
# 음절-beat 거리에는 상한을 두지 않는다. 실측 분포가 p50=2.8 / p99=12.3 /
# 최대 20.3pt 인데 beat 간격도 5.2~30.6pt 로 넓어서, 이상치를 걸러낼 만큼 낮은
# 상한은 진짜 음절부터 버린다 (12.0 으로 두었을 때 4개 유실). 가사가 아닌 글리프는
# 아래 한글 하한이 걸러낸다 — 이 악보에서 96자를 제외하고 317자를 정확히 남긴다.
# 늘임표 — 앞 음절이 다음 음까지 이어진다는 표시. 자기 음 위치를 차지하지만
# GP5 가사에는 담을 자리가 없어 반영하지 않는다.
LYRIC_HOLD_MARK = "-"
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
SMUFL_REST_RANGE = smufl.REST
# 쉼표 글리프 이름 → 아는 길이. 이름이 곧 길이라 x 간격 추정보다 정확하다.
# 온쉼표(restWhole)는 "마디 전체" 관례가 있어 박자표마다 길이가 달라진다 —
# 고정하지 않고 x 간격에 맡긴다.
REST_DURATIONS: dict[str, durations.LegalDuration] = {
    "restHalf": durations.LegalDuration(2, False, 2.0),
    "restQuarter": durations.LegalDuration(4, False, 1.0),
    "rest8th": durations.LegalDuration(8, False, 0.5),
    "rest16th": durations.LegalDuration(16, False, 0.25),
    "rest32nd": durations.LegalDuration(32, False, 0.125),
}
REST_NAMES = frozenset(REST_DURATIONS) | {"restWhole"}
# 첫·끝 마디의 추정 길이가 박자표의 이 비율 미만이면 못갖춘마디로 본다.
# 정상 마디의 폭 변동(justification)은 ±15% 안이라 0.75 문턱에 오탐이 없다.
PICKUP_MAX_RATIO = 0.75
# X 음표머리(noteheadXBlack) — 타브 대역에서는 뮤트 노트다. 프렛 숫자가 없는
# 자기만의 리듬 이벤트라 beat 으로 세워야 한다 (실측: m23·m48 에 8개)
X_NOTEHEAD = chr(0xE0A9)
# 곡 진행 기호. 코다 글리프는 마디 앞쪽이면 도착점(Coda), 뒤쪽이면 도약점
# (To Coda = GP 의 'Da Coda') 이다 — 실측: 도착 코다는 마디 시작(x비 0.0),
# 도약 코다는 마지막 beat 뒤(x비 0.9)에 놓인다.
DIRECTION_TARGET_RATIO = 0.5
# 진행 지시 텍스트 → GP5 direction 이름. 글리프 스트림에 공백이 없어 붙은
# 형태로 맞춘다. 순서가 곧 우선순위다 — 'D.S.alFine' 이 'Fine' 보다 먼저다.
JUMP_TEXTS: tuple[tuple[str, str], ...] = (
    ("D.S.alCoda", "Da Segno al Coda"),
    ("D.S.alFine", "Da Segno al Fine"),
    ("D.C.alCoda", "Da Capo al Coda"),
    ("D.C.alFine", "Da Capo al Fine"),
    ("D.S.", "Da Segno"),
    ("D.C.", "Da Capo"),
    ("toCoda", "Da Coda"),
    ("ToCoda", "Da Coda"),
)
FINE_TEXT = "Fine"
SMUFL_STROKE_DOWN = ""
SMUFL_STROKE_UP = ""


class NotATabPdf(ValueError):
    """타브 악보로 해석할 수 없는 입력."""


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
    """폰트 이름에 의존하지 않는다 — 숫자 + 타브 대역 + 크기 범위 + 텍스트 배제."""
    return [g for g in geo.glyphs
            if x0 <= g.x < x1 and g.char.isdigit()
            and GRACE_FRET_GLYPH_SIZE < g.size <= MAX_FRET_GLYPH_SIZE
            and _in_tab_band(g, system)
            and not _has_adjacent_letter(letter_index, g)]


def _grace_glyphs(geo, system, x0, x1, letter_index) -> list[geometry.Glyph]:
    """꾸밈음 프렛 숫자 — 정규보다 작게 조판된다."""
    return [g for g in geo.glyphs
            if x0 <= g.x < x1 and g.char.isdigit()
            and g.size <= GRACE_FRET_GLYPH_SIZE and _in_tab_band(g, system)
            and not _has_adjacent_letter(letter_index, g)]


def _merge_two_digit_frets(glyphs, system, index, warn) -> list[geometry.Glyph]:
    """같은 줄에서 커닝으로 붙은 숫자쌍을 두 자리 프렛 한 음으로 병합한다.

    '12' 는 숫자 글리프 2개(origin 간격 ~5pt)로 오는데, beat 클러스터 허용
    (2pt)보다 넓어서 병합하지 않으면 1프렛·2프렛 **별개 beat 둘**이 된다.
    실측: 이 PDF 의 별개 음은 6.8pt 이상 떨어져 있어 6pt 문턱에 오탐이 없다.
    병합값이 프렛 상한을 넘는 쌍(붙은 '2''5' 따위)은 별개 음으로 두되 경고한다.
    """
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
            merged.append(geometry.Glyph(
                x=left.x, y=left.y, x_end=right.x_end,
                char=left.char + right.char, font=left.font, size=left.size))
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


def _chord_tokens(geo, system, x_limit: float) -> list[tuple[float, str]]:
    """이 시스템의 코드 행에서 (x, 코드명) 을 복원한다.

    행(y 클러스터) 안에서 직전 문자의 잉크 끝(`x_end`) 기준으로 이어붙인다.
    origin 기준으로는 'Cadd9' 가 'C' + 'add9' 로 쪼개진다 (실측 C→a origin
    간격 7.68pt, 잉크끝 기준 0.90pt). baseline 완전 일치를 요구하면 위첨자
    성질이 루트에서 떨어져 나가 'C7' 이 'C' 로 읽힌다.
    """
    top = system.melody_ys[0]
    # 음악 기호(사설 영역)는 코드명의 일부가 될 수 없다 — 세뇨·코다가 행
    # 병합에 끼면 이웃 코드명을 삼켜 토큰 전체가 판정 탈락한다 (실측 3건)
    band = sorted((g for g in geo.glyphs
                   if top - CHORD_BAND_HEIGHT <= g.y < top and g.x < x_limit
                   and not _in_range(g.char, PRIVATE_USE_RANGE)),
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
            text += glyph.char
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
TIMESIG_REGION_GAP = 20.0


def _read_timesig_region(region: list[geometry.Glyph], index: int,
                         warn: _Warnings) -> tuple[int, int] | None:
    """숫자 무리 하나를 (분자, 분모) 로 읽는다. 위/아래 행으로 가른다.

    12/8 처럼 분자가 두 자리면 x 열 묶음으로는 읽을 수 없다 — 분자·분모는
    항상 위아래 두 행이므로 y 중간값으로 가르고 행 안에서 x 순으로 잇는다.
    """
    ys = sorted(g.y for g in region)
    middle = (ys[0] + ys[-1]) / 2
    upper = sorted((g for g in region if g.y < middle), key=lambda g: g.x)
    lower = sorted((g for g in region if g.y >= middle), key=lambda g: g.x)
    if not upper or not lower:
        warn.add(index, "time_signature",
                 f"박자표 숫자 {len(region)}개를 분자/분모로 가르지 못했다 "
                 f"— 표기를 무시한다")
        return None
    def row_value(row):
        return int("".join(str(ord(g.char) - SMUFL_TIMESIG_DIGIT_BASE)
                           for g in row))
    return row_value(upper), row_value(lower)


def _detect_time_signatures(geo, system: geometry.System, index: int,
                            warn: _Warnings
                            ) -> list[tuple[float, tuple[int, int]]]:
    """멜로디 staff 의 박자표 표기를 (x, (분자, 분모)) 목록으로 읽는다.

    한 시스템에 표기가 여럿일 수 있다(마디 중간 박자 변경) — 전부 돌려주고
    적용 시점은 호출자가 마디 경계로 정한다.
    """
    low, high = SMUFL_TIMESIG_DIGIT_RANGE

    def in_melody(g: geometry.Glyph) -> bool:
        return (system.melody_ys[0] - SPAN_SLACK <= g.y
                <= system.melody_ys[-1] + SPAN_SLACK)

    result: list[tuple[float, tuple[int, int]]] = []
    # common/cut time 은 숫자가 아니라 단독 기호다
    for glyph in geo.glyphs:
        if not in_melody(glyph):
            continue
        name = smufl.name(glyph.char)
        if name == "timeSigCommon":
            result.append((glyph.x, (4, 4)))
        elif name == "timeSigCutCommon":
            result.append((glyph.x, (2, 2)))
    digits = sorted((g for g in geo.glyphs
                     if low <= ord(g.char) <= high and in_melody(g)),
                    key=lambda g: g.x)
    region: list[geometry.Glyph] = []
    for glyph in digits + [None]:
        if region and (glyph is None
                       or glyph.x - region[-1].x > TIMESIG_REGION_GAP):
            sig = _read_timesig_region(region, index, warn)
            if sig is not None:
                result.append((region[0].x, sig))
            region = []
        if glyph is not None:
            region.append(glyph)
    return sorted(result)


def _techniques(geo, system, x0, x1, fret_glyphs, letter_index) -> list[dict]:
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
        before = [f for f in fret_glyphs if f.x <= glyph.x]
        if not before:
            continue
        source = max(before, key=lambda f: f.x)
        string = _snap_to_string(source.y, system.tab_ys)
        if string is None:
            continue
        result.append({"x": source.x, "string": string, "kind": kind})
    return result


def _articulations(geo, system, x0, x1) -> list[tuple[geometry.Glyph, str]]:
    """타브 대역의 아티큘레이션 글리프 중 GP5 로 옮길 수 있는 것."""
    result = []
    for glyph in geo.glyphs:
        if not (x0 <= glyph.x < x1) or not _in_tab_band(glyph, system):
            continue
        kind = ARTICULATION_KINDS.get(smufl.name(glyph.char) or "")
        if kind is not None:
            result.append((glyph, kind))
    return result


def _is_lyric_syllable(glyph: geometry.Glyph, system: geometry.System) -> bool:
    """가사 음절인지. 멜로디와 타브 사이 대역의 한글만 인정한다.

    이 대역에는 영문 연주 지시("with 16beat arp play")·마디 번호·H/P/S 표기도
    놓여 있어 한글 하한으로 가른다. 영문 가사 악보에는 이 규칙이 통하지 않는다.
    """
    return (system.melody_ys[-1] < glyph.y < system.tab_ys[0]
            and ord(glyph.char) >= LYRIC_MIN_CODEPOINT
            and not _in_range(glyph.char, PRIVATE_USE_RANGE))


LYRIC_ROW_TOLERANCE = 2.0       # 같은 가사 행으로 볼 baseline y 오차 (pt)


def _lyric_syllables(geo, system, index, warn
                     ) -> tuple[list[tuple[float, str]],
                                list[list[tuple[float, str]]]]:
    """멜로디 staff 와 타브 staff 사이의 가사 음절을 (1절, [2절 이하]) 로 뽑는다.

    다절 악보는 가사가 위아래 여러 행이다 — x 순으로만 정렬하면 절이
    글자 단위로 섞인다. baseline y 로 행을 가르고 beat 배정은 첫 행만 받는다.
    """
    low, high = system.melody_ys[-1], system.tab_ys[0]
    band = [g for g in geo.glyphs if low < g.y < high
            and not _in_range(g.char, PRIVATE_USE_RANGE)]
    lyric_glyphs = sorted((g for g in band if _is_lyric_syllable(g, system)),
                          key=lambda g: (g.y, g.x))
    rows: list[list[geometry.Glyph]] = []
    for glyph in lyric_glyphs:
        if rows and glyph.y - rows[-1][0].y <= LYRIC_ROW_TOLERANCE:
            rows[-1].append(glyph)
        else:
            rows.append([glyph])
    as_pairs = [sorted(((g.x, g.char) for g in row), key=lambda pair: pair[0])
                for row in rows]
    syllables, extra_rows = (as_pairs[0], as_pairs[1:]) if as_pairs else ([], [])
    if extra_rows:
        warn.add(index, "lyric_extra_rows",
                 f"가사 행이 {len(as_pairs)}개다 — 2절 이하 "
                 f"{sum(len(row) for row in extra_rows)}음절은 beat 에 배정하지 "
                 f"않고 GP5 가사 줄 2~5로 넘긴다 (--lyrics row 에서 보인다)")
    # 가사가 있는 시스템에서만 센다 — 이 대역에는 'S.D' 같은 연주법 표기도 놓인다
    holds = sum(1 for g in band if g.char == LYRIC_HOLD_MARK)
    if syllables and holds:
        warn.add(index, "lyric_hold",
                 f"늘임표 {LYRIC_HOLD_MARK!r} {holds}개를 반영하지 못했다 "
                 f"— 앞 음절이 다음 음까지 늘어난다는 표시다")
    return syllables, extra_rows


def _assign_lyrics(beat_xs: list[float], syllables: list[tuple[float, str]],
                   index: int, warn: _Warnings) -> dict[float, str]:
    """음절을 x 가 가장 가까운 beat 에 배정한다.

    조판된 악보에서 x 는 시간 위치다. 같은 x 의 기타 beat 에 붙이면 노래하는
    시점과 맞는다.

    보컬이 기타 아르페지오보다 촘촘한 구간이 있어(한 마디에 음절 10개 vs beat 8.6개)
    beat 하나에 둘 이상이 몰릴 수 있다. 그때는 이어 붙인다 — 음절을 버리면 가사가
    "나는내가빛나는" 에서 "나빛나는" 처럼 망가진다.
    """
    if not beat_xs:
        if syllables:
            warn.add(index, "lyric_lost",
                     f"beat 이 없어 음절 {len(syllables)}개를 버렸다: "
                     f"{''.join(char for _, char in syllables)!r}")
        return {}
    assigned: dict[float, str] = {}
    crowded = 0
    for syllable_x, char in syllables:
        beat_x = min(beat_xs, key=lambda bx: abs(bx - syllable_x))
        if beat_x in assigned:
            crowded += 1
        assigned[beat_x] = assigned.get(beat_x, "") + char
    if crowded:
        warn.add(index, "lyric_crowding",
                 f"음절 {crowded}개가 앞 음절과 같은 beat 에 묶였다 "
                 f"— 보컬이 기타보다 잔 리듬을 쓴다")
    return assigned


def _band_of(glyph: geometry.Glyph, system: geometry.System) -> str | None:
    """글리프가 놓인 대역. 시스템 밖이면 None.

    `between` 은 멜로디 staff 와 타브 staff 사이다 — 가사와 H/P/S 연주법 표기가
    이 대역을 공유하므로 이름으로 둘을 구분하지 않는다.
    """
    top, bottom = system.melody_ys[0], system.melody_ys[-1]
    if top - SPAN_SLACK <= glyph.y <= bottom + SPAN_SLACK:
        return "melody"
    if top - CHORD_BAND_HEIGHT <= glyph.y < top - SPAN_SLACK:
        return "chord"
    if bottom + SPAN_SLACK < glyph.y < system.tab_ys[0]:
        return "between"
    if _in_tab_band(glyph, system):
        return "tab"
    return None


def _nearest_beat(beat_xs: list[float], x: float) -> int | None:
    if not beat_xs:
        return None
    return min(range(len(beat_xs)), key=lambda i: abs(beat_xs[i] - x))


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
            if name in ARTICULATION_KINDS or name in REST_NAMES:
                continue                # _articulations·_rest_glyphs 가 반영한다
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


def _glyph_directions(geo, system, bounds) -> tuple[str | None, str | None]:
    """segno/coda 글리프에서 (도착 direction, 도약 from_direction) 을 읽는다.

    `_band_of` 로 이 시스템의 대역만 본다 — 페이지에 시스템이 4~5개 쌓여
    있어 x 만으로 거르면 위 시스템의 기호가 아래 시스템 마디에 잡힌다
    (실측: 세뇨 1개가 3개로 부풀었다).
    """
    x0, x1 = bounds
    direction = from_direction = None
    for glyph in geo.glyphs:
        if not (x0 <= glyph.x < x1) or _band_of(glyph, system) is None:
            continue
        name = smufl.name(glyph.char)
        if name == "segno":
            direction = "Segno"
        elif name == "coda":
            ratio = (glyph.x - x0) / max(x1 - x0, 1e-9)
            if ratio < DIRECTION_TARGET_RATIO:
                direction = "Coda"
            else:
                from_direction = "Da Coda"
    return direction, from_direction


def _repeat_flags(geo, system, bounds) -> tuple[bool, bool]:
    """반복 바라인 글리프에서 (반복 시작, 반복 끝) 을 읽는다.

    도트가 그려진 원(드로잉)으로만 표기된 반복은 여기서 못 본다 — 그 경우는
    `_warn_unsupported` 의 반복기호 경고로 드러난다.
    """
    x0, x1 = bounds
    is_open = is_close = False
    for glyph in geo.glyphs:
        if not (x0 <= glyph.x < x1) or _band_of(glyph, system) is None:
            continue
        name = smufl.name(glyph.char)
        if name in ("repeatLeft", "repeatRightLeft"):
            is_open = True
        if name in ("repeatRight", "repeatRightLeft"):
            is_close = True
    return is_open, is_close


def _text_directions(glyph_entries: list[dict]) -> tuple[str | None, str | None]:
    """'D.S. al Coda' 류 진행 지시 텍스트에서 direction 을 읽는다.

    글리프 스트림에는 공백이 없어 이어붙인 문자열로 맞춘다 (실측:
    'D.S.alCoda'). 'Fine' 은 도약이 아니라 도착점이다.
    """
    text = "".join(g["char"] for g in glyph_entries if "char" in g)
    for pattern, name in JUMP_TEXTS:
        if pattern in text:
            return None, name
    if FINE_TEXT in text:
        return "Fine", None
    return None, None


def _set_row_chord(beat: dict, name: str, index: int, warn) -> None:
    beat["chord"] = name
    if chords.voicing_for(name) is None:
        # 다이어그램은 보이싱이 있어야 만들어진다 — 조용히 사라지지 않게
        # 남긴다. AI 보정(voicing)이 채우면 build 가 그때 그린다.
        warn.add(index, "chord_no_voicing",
                 f"{name} 의 보이싱을 몰라 다이어그램을 만들 수 없다 "
                 f"— .gp5 에 이 코드 표기가 보이지 않는다")


def _assign_row_chords(beats: list[dict], tokens, bounds, index, warn,
                       pending: str | None = None) -> str | None:
    """코드 행 토큰을 표기 위치의 beat 에 배정한다. 이월분을 돌려준다.

    슬래시 beat 은 from_chord 경로가 이미 이름을 갖는다. 프렛 beat 은 음은
    정확한데 코드 표기가 .gp5 에 전혀 남지 않았다 (실측 47개 토큰 소실) —
    표기 x 이후 첫 미배정 beat 에 이름만 붙인다. 음은 건드리지 않는다.

    이 악보의 조판은 마디 마지막 코드를 모든 beat 보다 뒤(마디선 직전)에
    표기해 **다음 마디 첫 beat** 을 선행 지시한다 (실측: Cadd9 가 마지막
    beat 보다 9~12pt 뒤). 그런 토큰은 버리지 않고 다음 마디로 넘긴다.
    """
    sounding = [b for b in beats if not b.get("rest")]
    if pending is not None and sounding and sounding[0].get("chord") is None:
        _set_row_chord(sounding[0], pending, index, warn)
    leftover = None
    x0, x1 = bounds
    for token_x, name in tokens:
        if not (x0 <= token_x < x1):
            continue
        target = next((b for b in sounding
                       if b["x"] >= token_x - CHORD_APPLY_SLACK
                       and b.get("chord") is None), None)
        if target is None:
            leftover = name         # 마디 끝 선행 표기 — 다음 마디 몫이다
            continue
        _set_row_chord(target, name, index, warn)
    return leftover


def _build_measure(geo, system, bounds, index, tokens, warn,
                   time_sig: tuple[int, int], letter_index,
                   syllables: list[tuple[float, str]],
                   carried_chord: str | None = None,
                   pending_row_chord: str | None = None,
                   pending_graces: list | None = None) -> dict:
    x0, x1 = bounds
    raw_fret_glyphs = _fret_glyphs(geo, system, x0, x1, letter_index)
    fret_glyphs = _merge_two_digit_frets(raw_fret_glyphs, system, index, warn)
    raw_grace_glyphs = _grace_glyphs(geo, system, x0, x1, letter_index)
    grace_glyphs = _merge_two_digit_frets(raw_grace_glyphs, system, index, warn)
    dead_glyphs = _dead_glyphs(geo, system, x0, x1)
    rest_glyphs = _rest_glyphs(geo, system, x0, x1)
    slash_xs = _slash_xs(geo, system, x0, x1)
    kind = _classify(fret_glyphs + dead_glyphs, slash_xs)
    _warn_unsupported(geo, system, x0, x1, index, warn)

    techniques = _techniques(geo, system, x0, x1, fret_glyphs, letter_index)
    articulations = _articulations(geo, system, x0, x1)
    beat_xs = _cluster([g.x for g in fret_glyphs + dead_glyphs + rest_glyphs]
                       + slash_xs)
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
            rest_pins[position] = legal
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
    repeat_open, repeat_close = _repeat_flags(geo, system, bounds)
    if repeat_open:
        measure["repeat_open"] = True
    if repeat_close:
        measure["repeat_close"] = True
    if not beat_xs:
        warn.add(index, "empty_measure", f"판정 {kind}, x {x0:.1f}..{x1:.1f}")
        return measure

    # 잇단음표 숫자('3')가 표기된 마디에서만 셋잇단 후보를 연다 — 늘 열어두면
    # x 간격이 우연히 3등분에 가까운 평범한 마디에 가짜 셋잇단이 유입된다
    allow_tuplets = any(
        x0 <= g.x < x1 and _in_range(g.char, smufl.TUPLET)
        and _band_of(g, system) is not None
        for g in geo.glyphs)
    target = durations.target_quarters(*time_sig)
    fitted, exact = durations.fit_durations(
        durations.proportions(beat_xs, x1, target), target, pinned=rest_pins,
        allow_tuplets=allow_tuplets)
    if not exact:
        total = sum(d.quarters for d in fitted)
        warn.add(index, "duration_mismatch",
                 f"합 {total:.3f} / 목표 {target:.3f}, "
                 f"durs={[d.value for d in fitted]}")

    for position, (beat_x, duration) in enumerate(zip(beat_xs, fitted)):
        if position in rest_positions:
            measure["beats"].append({
                "x": round(beat_x, 2), "duration": duration.value,
                "dotted": duration.dotted,
                "tuplet": list(duration.tuplet) if duration.tuplet else None,
                "rest": True, "chord": None,
                "from_chord": False, "stroke": None, "lyric": None,
                "techniques": [], "notes": [],
            })
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
            voicing = chords.voicing_for(chord)
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
            "duration": duration.value,
            "dotted": duration.dotted,
            "tuplet": list(duration.tuplet) if duration.tuplet else None,
            "chord": chord,
            "from_chord": from_chord,
            "stroke": stroke,
            "lyric": lyrics.get(beat_x),
            "techniques": [
                {"string": t["string"], "kind": t["kind"]} for t in techniques
                if abs(t["x"] - beat_x) <= BEAT_CLUSTER_TOLERANCE
            ] + [{"string": None, "kind": kind}
                 for kind in beat_articulations.get(position, ())],
            "notes": notes,
        })
    measure["pending_row_chord"] = _assign_row_chords(
        measure["beats"], tokens, bounds, index, warn,
        pending=pending_row_chord)
    measure["pending_graces"] = _attach_graces(
        measure["beats"], grace_glyphs, system, index, warn,
        pending=pending_graces)
    # 경계 마디(픽업·불완전 종지) 재판정에 쓰는 임시 값 — extract_ir 가 걷어낸다
    measure["_fit"] = {"beat_xs": beat_xs, "x1": x1, "rest_pins": rest_pins,
                       "allow_tuplets": allow_tuplets}
    return measure


def _attach_graces(beats: list[dict], grace_glyphs, system, index, warn,
                   pending: list | None = None) -> list:
    """꾸밈음을 다음 음(같은 줄)에 붙인다. 못 붙인 것은 다음 마디로 넘긴다.

    GP5 는 노트당 grace 를 하나만 담는다 — 둘 이상이 몰리면 마지막(주음에
    가장 가까운) 것만 남기고 경고한다.
    """
    entries = list(pending or [])
    for glyph in sorted(grace_glyphs, key=lambda g: g.x):
        string = _snap_to_string(glyph.y, system.tab_ys)
        if string is None:
            warn.add(index, "grace_dropped",
                     f"꾸밈음 {glyph.char!r} 가 어느 줄에도 스냅되지 않았다")
            continue
        entries.append({"x": glyph.x, "string": string, "fret": int(glyph.char)})
    leftover: list = []
    for entry in entries:
        target = next(
            (note for beat in beats if beat["x"] > entry["x"]
             for note in beat["notes"] if note["string"] == entry["string"]),
            None)
        if target is None:
            if entry.get("carried"):
                # 한 마디를 넘겨도 주음이 없다 — 끝없이 이월하면 엉뚱한
                # 자리에 붙는다. 버리고 드러낸다.
                warn.add(index, "grace_dropped",
                         f"string{entry['string']} 꾸밈음 {entry['fret']} 의 "
                         f"주음을 찾지 못했다")
            else:
                leftover.append({**entry, "x": -1.0, "carried": True})
            continue
        if target.get("grace_fret") is not None:
            warn.add(index, "grace_dropped",
                     f"string{entry['string']} 의 꾸밈음 {target['grace_fret']} 이 "
                     f"뒤따르는 꾸밈음 {entry['fret']} 에 밀려났다 "
                     f"— GP5 는 노트당 하나만 담는다")
        target["grace_fret"] = entry["fret"]
    return leftover


def _apply_tie_curves(geo, system, system_measures: list[dict],
                      warn: _Warnings) -> None:
    """타브 대역 곡선 중 같은 (줄, 프렛)을 잇는 것을 타이로 반영한다.

    다른 프렛을 잇는 곡선은 슬러다 — H/P/S 표기가 이미 결정론적으로
    반영하므로 여기서 손대지 않는다. 시스템(줄바꿈)을 건너는 타이는 두
    반쪽 호로 그려져 여기서 못 본다.
    ponytail: 시스템 경계 타이는 미지원 — 필요해지면 시스템 끝/시작 반쪽
    호를 짝짓는다.
    """
    beats = [beat for measure in system_measures for beat in measure["beats"]]
    if not beats:
        return
    low = system.tab_ys[0] - TAB_BAND_MARGIN
    high = system.tab_ys[-1] + TAB_BAND_MARGIN
    for curve in geo.curves:
        if not (low <= curve.y0 <= high and low <= curve.y1 <= high):
            continue
        left = min(range(len(beats)), key=lambda i: abs(beats[i]["x"] - curve.x0))
        right = min(range(len(beats)), key=lambda i: abs(beats[i]["x"] - curve.x1))
        if right != left + 1:
            # 타이는 인접한 두 이벤트를 잇는다. 여러 beat 을 덮는 호는
            # 슬러(해머온·풀오프 묶음)다 — H/P 표기가 이미 반영한다
            continue
        if (abs(beats[left]["x"] - curve.x0) > TIE_ENDPOINT_TOLERANCE
                or abs(beats[right]["x"] - curve.x1) > TIE_ENDPOINT_TOLERANCE):
            continue                    # 끝점이 음표와 무관하다 — 삽화 곡선
        shared = ({(n["string"], n["fret"]) for n in beats[left]["notes"]}
                  & {(n["string"], n["fret"]) for n in beats[right]["notes"]})
        for note in beats[right]["notes"]:
            if (note["string"], note["fret"]) in shared:
                note["tie"] = True


def _measure_span(measure: dict) -> float:
    """마디의 음악 구간 폭(pt) — 첫 beat 부터 마디선까지. proportions 와 같은 정의다."""
    fit = measure["_fit"]
    return fit["x1"] - fit["beat_xs"][0]


def _refit_measure(measure: dict, numerator: int, denominator: int,
                   warn: _Warnings) -> None:
    """줄어든 박자표로 beat 길이를 다시 맞춘다."""
    fit = measure["_fit"]
    target = durations.target_quarters(numerator, denominator)
    fitted, exact = durations.fit_durations(
        durations.proportions(fit["beat_xs"], fit["x1"], target), target,
        pinned=fit["rest_pins"], allow_tuplets=fit["allow_tuplets"])
    old = measure["time_sig"]
    measure["time_sig"] = [numerator, denominator]
    for beat, duration in zip(measure["beats"], fitted):
        beat["duration"] = duration.value
        beat["dotted"] = duration.dotted
        beat["tuplet"] = list(duration.tuplet) if duration.tuplet else None
    warn.add(measure["index"], "pickup_measure",
             f"{old[0]}/{old[1]} 보다 확실히 짧은 경계 마디 — 박자표를 "
             f"{numerator}/{denominator} 로 줄였다 (못갖춘마디로 판단)")
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
    scored = [m for m in measures if m["beats"] and "_fit" in m]
    if len(scored) < 3:
        return                      # 스케일을 잴 안쪽 표본이 없다
    scales = sorted(
        _measure_span(m) / durations.target_quarters(*m["time_sig"])
        for m in scored[1:-1] if _measure_span(m) > 0)
    if not scales:
        return
    scale = scales[len(scales) // 2]
    for measure in (scored[0], scored[-1]):
        target = durations.target_quarters(*measure["time_sig"])
        estimated = _measure_span(measure) / scale
        if estimated >= PICKUP_MAX_RATIO * target:
            continue
        denominator = measure["time_sig"][1]
        numerator = max(1, round(estimated * denominator / 4.0))
        if numerator >= measure["time_sig"][0]:
            continue
        _refit_measure(measure, numerator, denominator, warn)


def _header_lines(geo, system) -> list[str]:
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


def _header_fields(geo, system) -> dict[str, str]:
    """머리글에서 크레디트·조성·리듬 표기를 뽑는다.

    조판이 항목을 라벨+값으로 붙여 쓰므로 (`작사정중식`) 라벨을 접두어로 떼어낸다.
    라벨이 없는 리듬 지시(`Slow 16Beat`)는 낱말로 알아본다.
    """
    fields: dict[str, str] = {}
    for line in _header_lines(geo, system):
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
    header: dict[str, str] = {}
    saw_text = False

    time_sig = DEFAULT_TIME_SIG
    carried_chord: str | None = None
    pending_row_chord: str | None = None
    pending_graces: list = []
    extra_lyric_rows: dict[int, list[str]] = {}
    with pymupdf.open(pdf_path) as document:
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
                    header.update(_header_fields(geo, system))
                tokens = _chord_tokens(geo, system, all_bounds[-1][1] + 1.0)
                syllables, extra_rows = _lyric_syllables(
                    geo, system, len(measures), warn)
                for row_index, row in enumerate(extra_rows):
                    extra_lyric_rows.setdefault(row_index, []).extend(
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
                        pending_row_chord, pending_graces)
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

    _adjust_boundary_measures(measures, warn)
    for measure in measures:
        measure.pop("_fit", None)

    if tempo is None:
        # 이 악보에는 BPM 표기(♩=N·메트로놈 글리프)가 아예 없다. 리듬 지시
        # ('Slow 16Beat') 는 세분 표기라 템포가 아니다 — 추측한 값을 조용히
        # 내보내면 사용자가 악보에서 읽은 값이라고 믿는다.
        warn.add(0, "tempo_guessed",
                 f"악보에 BPM 표기가 없어 {DEFAULT_TEMPO} 을 넣었다"
                 + (f" (리듬 지시: {header['rhythm']!r})" if "rhythm" in header else "")
                 + " — 음원과 맞추려면 --tempo 로 지정할 것")

    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    result = {
        "title": title if title is not None else stem,
        "artist": artist if artist is not None else header.get("artist", ""),
        "tempo": tempo or DEFAULT_TEMPO,
        "tempo_source": "지정" if tempo else "기본값(악보에 표기 없음)",
        "credits": {key: value for key, value in header.items()
                    if key in ("words", "music", "arranger", "performer")},
        "key": header.get("key", ""),
        "rhythm": header.get("rhythm", ""),
        "tuning": list(tuning) if tuning else list(STANDARD_TUNING),
        # CLI 인자가 머리글 표기('Capo 3')를 이긴다
        "capo": capo if capo is not None else int(header.get("capo", 0)),
        "measures": measures,
        "warnings": warn.items,
    }
    if extra_lyric_rows:
        result["extra_lyric_rows"] = [
            " ".join(chars)
            for _, chars in sorted(extra_lyric_rows.items())]
    return result
