"""가사 음절 추출과 beat 배정."""

from . import geometry, smufl

# 가사는 멜로디 staff 아래 ~ 타브 staff 위 대역에 놓인다.
# 이 대역에는 영문 연주 지시("with 16beat arp play")도 있어 한글만 취한다.
# 영문 가사 악보에는 이 규칙이 통하지 않는다 (이 곡은 한글 가사다).
LYRIC_MIN_CODEPOINT = 0x1100
# 음절-beat 거리에는 상한을 두지 않는다. 실측 분포가 p50=2.8 / p99=12.3 /
# 최대 20.3pt 인데 beat 간격도 5.2~30.6pt 로 넓어서, 이상치를 걸러낼 만큼 낮은
# 상한은 진짜 음절부터 버린다 (12.0 으로 두었을 때 4개 유실). 가사가 아닌 글리프는
# 위 한글 하한이 걸러낸다 — 이 악보에서 96자를 제외하고 317자를 정확히 남긴다.
# 늘임표 — 앞 음절이 다음 음까지 이어진다는 표시. 자기 음 위치를 차지하지만
# GP5 가사에는 담을 자리가 없어 반영하지 않는다.
LYRIC_HOLD_MARK = "-"
LYRIC_ROW_TOLERANCE = 2.0       # 같은 가사 행으로 볼 baseline y 오차 (pt)


def is_syllable(glyph: geometry.Glyph, system: geometry.System) -> bool:
    """가사 음절인지. 멜로디와 타브 사이 대역의 한글만 인정한다.

    이 대역에는 영문 연주 지시("with 16beat arp play")·마디 번호·H/P/S 표기도
    놓여 있어 한글 하한으로 가른다. 영문 가사 악보에는 이 규칙이 통하지 않는다.
    """
    return (system.melody_ys[-1] < glyph.y < system.tab_ys[0]
            and ord(glyph.char) >= LYRIC_MIN_CODEPOINT
            and not smufl.in_range(glyph.char, smufl.PRIVATE_USE))


def syllable_rows(geo, system, index, warn
                  ) -> tuple[list[tuple[float, str]],
                             list[list[tuple[float, str]]]]:
    """멜로디 staff 와 타브 staff 사이의 가사 음절을 (1절, [2절 이하]) 로 뽑는다.

    다절 악보는 가사가 위아래 여러 행이다 — x 순으로만 정렬하면 절이
    글자 단위로 섞인다. baseline y 로 행을 가르고 beat 배정은 첫 행만 받는다.
    """
    low, high = system.melody_ys[-1], system.tab_ys[0]
    band = [g for g in geo.glyphs if low < g.y < high
            and not smufl.in_range(g.char, smufl.PRIVATE_USE)]
    lyric_glyphs = sorted((g for g in band if is_syllable(g, system)),
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


def assign(beat_xs: list[float], syllables: list[tuple[float, str]],
           index: int, warn) -> dict[float, str]:
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
