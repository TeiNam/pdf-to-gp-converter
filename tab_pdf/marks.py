"""음표가 아닌 표기의 해석 — 진행 기호·반복·박자표·아티큘레이션·타이·꾸밈음.

extract 가 마디를 세우는 동안 이 모듈이 "그 마디에 어떤 표기가 걸렸는가"를
답한다. PDF 를 직접 열지 않고 geometry 가 모은 글리프·곡선만 본다.
"""

from . import bands, geometry, smufl

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
# 타브 대역 아티큘레이션 글리프 → 박 전체 연주법. 좌표·정체가 결정론적이라
# AI 를 거칠 이유가 없다 (실측: 악센트 44개가 --ai 에서만 살아났다).
# 값은 corrections.BEAT_TECHNIQUES 의 부분집합이어야 한다 — 테스트로 묶어 둔다.
ARTICULATION_KINDS = {
    "articAccentAbove": "accent", "articAccentBelow": "accent",
    "articMarcatoAbove": "heavy_accent", "articMarcatoBelow": "heavy_accent",
    "articStaccatoAbove": "staccato", "articStaccatoBelow": "staccato",
    "articStaccatissimoAbove": "staccato", "articStaccatissimoBelow": "staccato",
}
# 타이 곡선의 끝점이 beat x 에서 이보다 멀면 음악 호가 아니다 (pt).
# 배경 삽화의 곡선이 타브 대역을 지나가도 타이로 오인하지 않게 한다
TIE_ENDPOINT_TOLERANCE = 15.0
# 박자표 숫자 무리를 서로 다른 표기로 볼 x 간격 (pt). 한 표기의 숫자들은
# 몇 pt 안에 모이고, 마디 중간 박자 변경은 마디 하나(수십 pt) 이상 떨어진다
TIMESIG_REGION_GAP = 20.0
SMUFL_TIMESIG_DIGIT_BASE = smufl.TIMESIG_DIGIT[0]  # E080='0' … E089='9'


def glyph_directions(geo, system, bounds) -> tuple[str | None, str | None]:
    """segno/coda 글리프에서 (도착 direction, 도약 from_direction) 을 읽는다.

    `band_of` 로 이 시스템의 대역만 본다 — 페이지에 시스템이 4~5개 쌓여
    있어 x 만으로 거르면 위 시스템의 기호가 아래 시스템 마디에 잡힌다
    (실측: 세뇨 1개가 3개로 부풀었다).
    """
    x0, x1 = bounds
    direction = from_direction = None
    for glyph in geo.glyphs:
        if not (x0 <= glyph.x < x1) or bands.band_of(glyph, system) is None:
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


def repeat_flags(geo, system, bounds) -> dict[str, bool]:
    """반복 바라인 글리프를 읽는다.

    돌려주는 키 — open/close 는 이 마디, close_previous/open_next 는 이웃
    마디 몫이다. 겹반복(:‖:)은 마디 경계 기호라 한 마디에 열림·닫힘을 둘 다
    걸면 그 마디 하나가 반복되는 다른 악보가 된다 — 글리프가 마디 앞쪽이면
    "앞 마디를 닫고 이 마디를 연다", 뒤쪽이면 "이 마디를 닫고 다음을 연다".

    도트가 그려진 원(드로잉)으로만 표기된 반복은 여기서 못 본다 — 그 경우는
    extract 의 반복기호 경고로 드러난다.
    """
    x0, x1 = bounds
    flags = {"open": False, "close": False,
             "close_previous": False, "open_next": False}
    for glyph in geo.glyphs:
        if not (x0 <= glyph.x < x1) or bands.band_of(glyph, system) is None:
            continue
        name = smufl.name(glyph.char)
        if name == "repeatLeft":
            flags["open"] = True
        elif name == "repeatRight":
            flags["close"] = True
        elif name == "repeatRightLeft":
            ratio = (glyph.x - x0) / max(x1 - x0, 1e-9)
            if ratio < DIRECTION_TARGET_RATIO:
                flags["close_previous"] = True
                flags["open"] = True
            else:
                flags["close"] = True
                flags["open_next"] = True
    return flags


def text_directions(glyph_entries: list[dict]) -> tuple[str | None, str | None]:
    """'D.S. al Coda' 류 진행 지시 텍스트에서 direction 을 읽는다.

    글리프 스트림에는 공백이 없어 이어붙인 문자열로 맞춘다 (실측:
    'D.S.alCoda'). 'Fine' 은 도약이 아니라 도착점이다.
    """
    text = "".join(g["char"] for g in glyph_entries if "char" in g)
    for pattern, name in JUMP_TEXTS:
        if pattern in text:
            return None, name
    if FINE_TEXT in text:
        return FINE_TEXT, None
    return None, None


def articulations(geo, system, x0, x1) -> list[tuple[geometry.Glyph, str]]:
    """타브 대역의 아티큘레이션 글리프 중 GP5 로 옮길 수 있는 것."""
    result = []
    for glyph in geo.glyphs:
        if not (x0 <= glyph.x < x1) or not bands.in_tab_band(glyph, system):
            continue
        kind = ARTICULATION_KINDS.get(smufl.name(glyph.char) or "")
        if kind is not None:
            result.append((glyph, kind))
    return result


def attach_graces(beats: list[dict], grace_glyphs, system, index, warn,
                  pending: list | None = None, techniques=()) -> list:
    """꾸밈음을 다음 음(같은 줄)에 붙인다. 못 붙인 것은 다음 마디로 넘긴다.

    GP5 는 노트당 grace 를 하나만 담는다 — 둘 이상이 몰리면 마지막(주음에
    가장 가까운) 것만 남기고 경고한다.
    """
    entries = list(pending or [])
    for glyph in sorted(grace_glyphs, key=lambda g: g.x):
        string = bands.snap_to_string(glyph.y, system.tab_ys)
        if string is None:
            warn.add(index, "grace_dropped",
                     f"꾸밈음 {glyph.char!r} 가 어느 줄에도 스냅되지 않았다")
            continue
        entry = {"x": glyph.x, "string": string, "fret": int(glyph.char)}
        transition = next((t["kind"] for t in techniques
                           if t.get("grace") and t["x"] == glyph.x
                           and t["string"] == string), None)
        if transition:
            entry["transition"] = transition
        entries.append(entry)
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
        target.pop("grace_transition", None)
        if entry.get("transition"):
            target["grace_transition"] = entry["transition"]
    return leftover


def apply_tie_curves(geo, system, system_measures: list[dict], warn) -> None:
    """타브 대역 곡선 중 같은 (줄, 프렛)을 잇는 것을 타이로 반영한다.

    다른 프렛을 잇는 곡선은 슬러다 — H/P/S 표기가 이미 결정론적으로
    반영하므로 여기서 손대지 않는다. 시스템(줄바꿈)을 건너는 타이는 두
    반쪽 호로 그려져 여기서 못 본다.
    ponytail: 시스템 경계 타이는 미지원 — 필요해지면 시스템 끝/시작 반쪽
    호를 짝짓는다.
    """
    beats = [beat for measure in system_measures for beat in measure["beats"]]
    for voice in sorted({beat.get("voice", 0) for beat in beats}):
        _apply_voice_ties(geo, system, [b for b in beats if b.get("voice", 0) == voice])


def _apply_voice_ties(geo, system, beats):
    if not beats:
        return
    low = system.tab_ys[0] - bands.TAB_BAND_MARGIN
    high = system.tab_ys[-1] + bands.TAB_BAND_MARGIN
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
        if not shared:
            continue
        # 슬래시 하나가 화음 전체를 나타낼 때만 모든 구성음에 타이를 건다.
        if not (beats[left].get("from_chord") and beats[right].get("from_chord")):
            middle = (curve.y0 + curve.y1) / 2
            strings = {string for string, _ in shared
                       if curve.above is None
                       or (system.tab_ys[string - 1] >= middle
                           if curve.above else system.tab_ys[string - 1] <= middle)}
            if not strings:
                continue
            string = min(strings, key=lambda s: abs(system.tab_ys[s - 1] - middle))
            spacing = (system.tab_ys[-1] - system.tab_ys[0]) / (len(system.tab_ys) - 1)
            if max(abs(y - system.tab_ys[string - 1])
                   for y in (curve.y0, curve.y1)) > spacing:
                continue
            shared = {pair for pair in shared if pair[0] == string}
        for note in beats[right]["notes"]:
            if (note["string"], note["fret"]) in shared:
                note["tie"] = True


def _read_timesig_region(region: list[geometry.Glyph], index: int,
                         warn) -> tuple[int, int] | None:
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


def detect_time_signatures(geo, system: geometry.System, index: int,
                           warn) -> list[tuple[float, tuple[int, int]]]:
    """멜로디 staff 의 박자표 표기를 (x, (분자, 분모)) 목록으로 읽는다.

    한 시스템에 표기가 여럿일 수 있다(마디 중간 박자 변경) — 전부 돌려주고
    적용 시점은 호출자가 마디 경계로 정한다.
    """
    low, high = smufl.TIMESIG_DIGIT

    def in_melody(g: geometry.Glyph) -> bool:
        return (system.melody_ys[0] - bands.SPAN_SLACK <= g.y
                <= system.melody_ys[-1] + bands.SPAN_SLACK)

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
