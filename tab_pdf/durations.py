"""음길이를 4분음표 단위로 다루고, 마디 합 제약 아래 legal 값으로 스냅한다.

PDF 를 모르는 순수 계산 모듈이다. 추출기가 확정한 음가는 고정하고,
나머지 음가만 x 간격 비례와 마디 합 제약으로 추정한다.
"""

from dataclasses import dataclass, field
from functools import lru_cache

EPSILON = 1e-9
# 모든 legal 길이는 1/96 박의 배수다 — 점64분(0.09375)과 셋잇단까지의 공배수.
# 정수 단위로 환산해 DP 로 정확히 푼다 — 64분음표 = 6단위, 점64분 = 9단위,
# 셋잇단 8분 = 32단위. 1/48 이면 점64분이 4.5단위라 반올림돼 넘친 마디가 통과했다.
UNIT_QUARTERS = 1.0 / 96.0
# DP 캐시 크기 — (beat index, 남은 단위) 조합 상한. beat 수십 × 단위 수백이면 충분
DP_CACHE_SIZE = 100_000

TRIPLET = (3, 2)        # 3개를 2개 자리에 — pyguitarpro Tuplet(enters, times)


@dataclass(frozen=True)
class LegalDuration:
    value: int          # pyguitarpro Duration.value (1/2/4/8/16/32/64)
    dotted: bool
    quarters: float     # 4분음표 단위 길이
    tuplet: tuple[int, int] | None = None


# 순서가 곧 동률의 승자다 — 비용이 같으면 앞선(평범한) 길이를 고른다.
# 셋잇단이 정수 리듬을 밀어내지 않게 평범한 길이를 전부 앞에 둔다.
LEGAL: tuple[LegalDuration, ...] = (
    LegalDuration(1, True, 6.0),
    LegalDuration(1, False, 4.0),
    LegalDuration(2, True, 3.0),
    LegalDuration(2, False, 2.0),
    LegalDuration(4, True, 1.5),
    LegalDuration(4, False, 1.0),
    LegalDuration(8, True, 0.75),
    LegalDuration(8, False, 0.5),
    LegalDuration(16, True, 0.375),
    LegalDuration(16, False, 0.25),
    LegalDuration(32, False, 0.125),
    LegalDuration(64, False, 0.0625),
    # 셋잇단 — 4분·8분·16분. 단위 합 제약(mod 3)이 홀로 떨어진 셋잇단을
    # 걸러내므로 완결된 묶음으로만 해에 들어온다.
    LegalDuration(4, False, 2.0 / 3.0, TRIPLET),
    LegalDuration(8, False, 1.0 / 3.0, TRIPLET),
    LegalDuration(16, False, 1.0 / 6.0, TRIPLET),
)


def target_quarters(numerator: int, denominator: int) -> float:
    """박자표의 마디 길이를 4분음표 단위로."""
    return 4.0 * numerator / denominator


def proportions(beat_xs: list[float], measure_end_x: float,
                target: float) -> list[float]:
    """beat x 간격을 target 으로 정규화한 길이 목록. 합은 정확히 target."""
    if not beat_xs:
        return []
    gaps = [beat_xs[i + 1] - beat_xs[i] for i in range(len(beat_xs) - 1)]
    gaps.append(measure_end_x - beat_xs[-1])
    span = sum(gaps)
    if span <= 0:
        # 좌표가 퇴화했다 — 균등 분배로 떨어뜨리되 합은 유지한다
        return [target / len(beat_xs)] * len(beat_xs)
    return [target * gap / span for gap in gaps]


def _nearest(prop: float) -> LegalDuration:
    return min(LEGAL, key=lambda legal: abs(legal.quarters - prop))


def _units(quarters: float) -> int | None:
    """단위 수. 단위의 배수가 아니면 None — 반올림한 값으로 합을 속이지 않는다."""
    units = round(quarters / UNIT_QUARTERS)
    return units if abs(units * UNIT_QUARTERS - quarters) < EPSILON else None


# 잇단음표 표기가 없는 마디용 후보 — 셋잇단 제외
PLAIN_LEGAL: tuple[LegalDuration, ...] = tuple(
    legal for legal in LEGAL if legal.tuplet is None)

# 쉼표 글리프 이름 → 아는 길이. 이름이 곧 길이라 x 간격 추정보다 정확하다.
# 온쉼표(restWhole)는 "마디 전체" 관례가 있어 박자표마다 길이가 달라진다 —
# 고정하지 않고 x 간격에 맡긴다.
REST_DURATIONS: dict[str, LegalDuration] = {
    "restHalf": LegalDuration(2, False, 2.0),
    "restQuarter": LegalDuration(4, False, 1.0),
    "rest8th": LegalDuration(8, False, 0.5),
    "rest16th": LegalDuration(16, False, 0.25),
    "rest32nd": LegalDuration(32, False, 0.125),
}
REST_NAMES = frozenset(REST_DURATIONS) | {"restWhole"}


def as_triplet(legal: LegalDuration) -> LegalDuration:
    """같은 음표 모양의 셋잇단 길이 (3개를 2개 자리에)."""
    return LegalDuration(legal.value, legal.dotted, legal.quarters * 2 / 3, TRIPLET)


def fit_durations(props: list[float], target: float,
                  pinned: dict[int, LegalDuration] | None = None,
                  allow_tuplets: bool = False,
                  tuplet_indices: set[int] | None = None,
                  open_tuplet_indices: set[int] | frozenset[int] = frozenset(),
                  ) -> tuple[list[LegalDuration], bool]:
    """비례값을 legal 값으로 스냅하되 합이 정확히 target 이 되게 맞춘다.

    독립 스냅은 반올림 때문에 합이 어긋난다. legal 길이가 모두 1/96박의 배수이므로
    정수 단위 DP 로 "합이 정확히 target 이면서 스냅 오차 총합이 최소" 인 조합을
    찾는다. 그리디와 달리 해가 존재하면 반드시 찾는다 — 예: `[4.0, 4.0]` 을
    target 4.0 에 맞출 때 그리디는 실패했지만 DP 는 `[2.0, 2.0]` 을 찾는다.

    `pinned` 는 길이를 이미 아는 beat 이다 (쉼표 글리프는 이름이 곧 길이다).
    그 자리는 후보를 고정하고 나머지만 x 간격으로 맞춘다.

    `allow_tuplets` 는 악보에 잇단음표 숫자가 표기된 마디에서만 켠다 —
    항상 켜 두면 x 간격이 우연히 3등분에 가까운 평범한 마디에 가짜
    셋잇단이 유입된다 (실측: 이 악보 m5·m7 이 바뀌었다).

    `tuplet_indices`를 주면 그 묶음만 셋잇단으로, 나머지는 일반 음가로 푼다.
    `open_tuplet_indices` 는 셋잇단일 수도 아닐 수도 있는 자리다 — '3' 표기는
    있는데 묶음 범위를 확정할 빔이 없을 때 강제하지 않고 후보만 연다.

    Returns: (스냅 결과, 합이 정확히 맞았는지)
    """
    if not props:
        return [], True
    pins = pinned or {}
    legal_pool = LEGAL if allow_tuplets else PLAIN_LEGAL

    def candidates_at(index):
        if index in pins:
            pin = pins[index]
            if index in open_tuplet_indices and pin.tuplet is None:
                return (pin, as_triplet(pin))
            return (pin,)
        if index in open_tuplet_indices:
            return LEGAL
        if tuplet_indices is not None:
            return tuple(d for d in LEGAL
                         if bool(d.tuplet) == (index in tuplet_indices))
        return legal_pool

    target_units = _units(target)
    if target_units is None or target_units <= 0:
        return [_nearest(prop) for prop in props], False

    @lru_cache(maxsize=DP_CACHE_SIZE)
    def solve(index: int,
              remaining: int) -> tuple[float, tuple[LegalDuration, ...]] | None:
        """props[index:] 로 remaining 단위를 정확히 채우는 최소비용 선택."""
        if index == len(props):
            return (0.0, ()) if remaining == 0 else None
        best: tuple[float, tuple[LegalDuration, ...]] | None = None
        candidates = candidates_at(index)
        for legal in candidates:
            need = _units(legal.quarters)
            if need is None or need > remaining:
                continue
            tail = solve(index + 1, remaining - need)
            if tail is None:
                continue
            cost = abs(legal.quarters - props[index]) + tail[0]
            if best is None or cost < best[0] - EPSILON:
                best = (cost, (legal,) + tail[1])
        return best

    try:
        solution = solve(0, target_units)
    finally:
        solve.cache_clear()

    if solution is None:
        # 정확히 맞출 조합이 없다 — 거짓말하지 않고 최근접 스냅 + 실패 보고.
        # 고정 자리는 아는 값을 그대로 쓴다.
        return [min(candidates_at(i), key=lambda d: abs(d.quarters - prop))
                for i, prop in enumerate(props)], False
    return list(solution[1]), True


def rest_durations(quarters: float) -> list[LegalDuration]:
    """성부의 남은 무음을 표현한다. 기존 합 제약 풀이로 정확히 분할한다."""
    if abs(quarters) < EPSILON:
        return []
    for count in range(1, 17):
        result, exact = fit_durations(
            [quarters / count] * count, quarters, allow_tuplets=True)
        if exact:
            return result
    raise ValueError(f"쉼표로 표현할 수 없는 길이: {quarters}")


def _exact_legal(quarters: float, tuplet: bool) -> LegalDuration | None:
    """정확히 이 길이인 legal 값. 셋잇단 자리면 셋잇단을 먼저 본다."""
    matches = [d for d in LEGAL if abs(d.quarters - quarters) < EPSILON]
    matches.sort(key=lambda d: bool(d.tuplet) != tuplet)
    return matches[0] if matches else None


def _largest_within(quarters: float, tuplet: bool) -> LegalDuration | None:
    fitting = [d for d in LEGAL if bool(d.tuplet) == tuplet
               and d.quarters <= quarters + EPSILON]
    return max(fitting, key=lambda d: d.quarters, default=None)


@dataclass(frozen=True)
class VoiceEvents:
    """한 성부의 리듬 이벤트 — x 오름차순. 인덱스는 xs 의 인덱스다."""

    xs: tuple[float, ...]
    pins: dict[int, LegalDuration] = field(default_factory=dict)
    rests: frozenset[int] = frozenset()
    tuplets: frozenset[int] = frozenset()
    open_tuplets: frozenset[int] = frozenset()
    # 이 성부가 온쉼표 하나뿐 — 놓인 x 와 무관하게 마디 전체를 쉰다
    whole_rest: bool = False


@dataclass(frozen=True)
class Alignment:
    """성부별 (이벤트 인덱스 | None=채움 쉼표, 길이) 목록과 공통 시간축."""

    slots: tuple[tuple[tuple[int | None, LegalDuration], ...], ...]
    union_xs: tuple[float, ...]
    union_pins: dict[int, LegalDuration]
    exact: bool
    problems: tuple[str, ...]


def _union_timeline(voices, tolerance):
    active = [v for v in voices if not v.whole_rest]
    union: list[float] = []
    for x in sorted(x for v in active for x in v.xs):
        if not union or x - union[-1] > tolerance:
            union.append(x)
    # 각 성부 이벤트 → 공통 시간축 인덱스
    positions = [[min(range(len(union)), key=lambda k: abs(union[k] - x)) for x in v.xs]
                 if not v.whole_rest else [] for v in voices]
    return union, positions


def _segment_constraints(voices, positions, count):
    """한 구간만 덮는 이벤트의 확정 길이를 공통 구간에 옮긴다.

    셋잇단 표시는 이벤트가 시작하는 구간에만 준다 — 성부의 마지막 음처럼
    생략된 쉼표까지 덮는 이벤트가 마디 끝 구간을 전부 셋잇단으로 만들면 안 된다.
    """
    pins: dict[int, LegalDuration] = {}
    forced: set[int] = set()
    opened: set[int] = set()
    for voice, where in zip(voices, positions):
        for i, k in enumerate(where):
            following = where[i + 1] if i + 1 < len(where) else count
            if following == k + 1 and i in voice.pins:
                pins.setdefault(k, voice.pins[i])
            if i in voice.tuplets:
                forced.add(k)
            if i in voice.open_tuplets:
                opened.add(k)
    return pins, forced, opened - forced


def _voice_slots(voice, where, onsets, segments, total, problems):
    """공통 시각에 맞춰 이 성부의 이벤트 길이를 정하고 빈 곳은 쉼표로 채운다."""
    slots: list[tuple[int | None, LegalDuration]] = []

    def fill(quarters, event=None):
        try:
            parts = rest_durations(quarters)
        except ValueError as exc:
            problems.append(str(exc))
            return
        for position, part in enumerate(parts):
            slots.append((event if position == 0 else None, part))

    if where and onsets[where[0]] > EPSILON:
        fill(onsets[where[0]])
    for i, k in enumerate(where):
        end = onsets[where[i + 1]] if i + 1 < len(where) else total
        span = end - onsets[k]
        first = segments[k]
        if i in voice.rests and i not in voice.pins:
            fill(span, event=i)
            continue
        # 길이를 모르는 음은 다음 음까지 이어진다고 본다. 그 길이가 음가가 아니면
        # 셋잇단 음은 첫 구간 길이를, 아니면 들어가는 최대 음가를 쓰고 나머지를 쉰다
        chosen = (voice.pins.get(i) or _exact_legal(span, first.tuplet is not None)
                  or (first if first.tuplet else _largest_within(span, False)))
        if chosen is None:
            problems.append(f"이벤트 {i}: {span:.3f}박을 음가로 나타낼 수 없다")
            continue
        if chosen.quarters > span + EPSILON:
            problems.append(f"이벤트 {i}: 확정 음가 {chosen.quarters:.3f}박이 "
                            f"다음 이벤트까지 {span:.3f}박보다 길다")
        slots.append((i, chosen))
        if span - chosen.quarters > EPSILON:
            fill(span - chosen.quarters)
    return slots


def align_voices(voices: list[VoiceEvents], measure_end_x: float, target: float,
                 tolerance: float) -> Alignment:
    """여러 성부를 하나의 시간축에 올린다.

    성부마다 따로 x 간격을 맞추면 두 성부가 같은 x 에 둔 음이 다른 시각에
    시작한다. 모든 성부 이벤트의 x 를 합친 공통 시간축을 먼저 풀고, 각 성부의
    음은 그 시각 사이를 채운다. 확정 음가가 구간보다 짧으면 남는 몫은 쉼표다
    (타브는 다른 성부가 연주하는 동안의 쉼표를 흔히 생략한다).
    """
    union, positions = _union_timeline(voices, tolerance)
    union_pins, forced, opened = _segment_constraints(voices, positions, len(union))
    segments, exact = fit_durations(
        proportions(union, measure_end_x, target), target, pinned=union_pins,
        tuplet_indices=forced, open_tuplet_indices=opened)
    onsets = [0.0]
    for segment in segments:
        onsets.append(onsets[-1] + segment.quarters)
    total = onsets[-1] if union else target
    problems: list[str] = []
    slots = tuple(
        tuple((0, d) if i == 0 else (None, d)
              for i, d in enumerate(rest_durations(target))) if v.whole_rest
        else tuple(_voice_slots(v, where, onsets, segments, total, problems))
        for v, where in zip(voices, positions))
    return Alignment(slots, tuple(union), union_pins, exact and not problems,
                     tuple(problems))
