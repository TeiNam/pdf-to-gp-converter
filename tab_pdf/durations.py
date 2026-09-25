"""음길이를 4분음표 단위로 다루고, 마디 합 제약 아래 legal 값으로 스냅한다.

PDF 를 모르는 순수 계산 모듈이다. 추출기가 확정한 음가는 고정하고,
나머지 음가만 x 간격 비례와 마디 합 제약으로 추정한다.
"""

from dataclasses import dataclass, field
from functools import lru_cache
from itertools import product

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
                  floors: dict[int, float] | None = None,
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
    `floors` 는 길이의 하한만 아는 자리다 — 뒤에 쉼표가 생략됐을 수 있는 음.

    Returns: (스냅 결과, 합이 정확히 맞았는지)
    """
    if not props:
        return [], True
    pins = pinned or {}
    legal_pool = LEGAL if allow_tuplets else PLAIN_LEGAL

    lows = floors or {}

    def candidates_at(index):
        if index in pins:
            return (pins[index],)
        pool = legal_pool
        if tuplet_indices is not None:
            pool = tuple(d for d in LEGAL
                         if bool(d.tuplet) == (index in tuplet_indices))
        if index in lows:
            pool = tuple(d for d in pool if d.quarters >= lows[index] - EPSILON)
        return pool

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


# 표기 창 조합 상한 — 창 하나는 많아야 수십 가지라 실제 악보에서는 닿지 않는다
MAX_TRIPLET_CHOICES = 512


def _window_runs(window: tuple[int, ...]) -> list[frozenset[int]]:
    """창 안에서 셋잇단일 수 있는 연속 구간 — 두 이벤트 이상 (4분+8분 셋잇단)."""
    return [frozenset(window[a:b]) for a in range(len(window))
            for b in range(a + 2, len(window) + 1)]


@dataclass(frozen=True)
class Span:
    """쉼표 글리프가 확정한 길이 — 구간 start..stop-1 의 합이다."""

    start: int
    stop: int
    length: LegalDuration
    # 이 쉼표의 성부가 셋잇단 표기를 가져 셋잇단 자리면 셋잇단 길이가 된다
    can_triplet: bool = False


def _spread_spans(props, pins, spans, tuplets, floors):
    """여러 구간을 덮는 쉼표 길이를 구간별 길이로 나눠 고정한다. 실패하면 None.

    한 구간이면 그대로 고정하고, 여러 구간이면 그 구간들만 따로 풀어 합을
    맞춘다 — 마디 첫머리와 이어지지 않은 중간 쉼표도 제약이 된다.
    """
    pins = dict(pins)
    for span in spans:
        length = span.length
        if span.can_triplet and span.start in tuplets and length.tuplet is None:
            length = as_triplet(length)
        indices = range(span.start, span.stop)
        if len(indices) == 1:
            if span.start in pins and pins[span.start] != length:
                return None
            pins[span.start] = length
            continue
        inner, exact = fit_durations(
            props[span.start:span.stop], length.quarters,
            pinned={i - span.start: pins[i] for i in indices if i in pins},
            tuplet_indices={i - span.start for i in indices if i in tuplets},
            floors={i - span.start: floors[i] for i in indices if i in floors})
        if not exact:
            return None
        pins.update((span.start + i, d) for i, d in enumerate(inner))
    return pins


def fit_with_triplets(props: list[float], target: float,
                      pinned: dict[int, LegalDuration],
                      forced: set[int],
                      windows: list[tuple[int, ...]],
                      spans: tuple[Span, ...] = (),
                      floors: dict[int, tuple[float, bool]] | None = None,
                      triplet_pins: set[int] | None = None,
                      ) -> tuple[list[LegalDuration], bool, set[int]]:
    """확정 셋잇단 묶음에 더해, '3' 표기 창마다 셋잇단 구간 하나를 반드시 고른다.

    창의 이벤트를 전부 강제하면 4분+8분 셋잇단 뒤의 정상 음이 끌려 들어가고,
    후보만 열면 간격이 고른 마디에서 표기를 버린다. 창마다 연속 구간 하나를
    골라 보고 합이 맞는 것 중 x 간격에 가장 가까운 해를 쓴다.

    셋잇단 자리의 고정 길이는 셋잇단 길이로 바꾼다 — 단, 그 음의 성부가 스스로
    셋잇단 표기를 가질 때만이다(`triplet_pins`, None 이면 전부). 다른 성부의
    셋잇단 때문에 일반 8분이 짧아지면 안 된다. `floors` 는 (하한, 셋잇단 가능).

    Returns: (스냅 결과, 합이 정확히 맞았는지, 셋잇단으로 푼 인덱스)
    """
    options = [runs for runs in map(_window_runs, windows) if runs]
    best = None
    for count, choice in enumerate(product(*options)):
        if count >= MAX_TRIPLET_CHOICES:
            break       # ponytail: 창이 많은 마디는 앞 조합만 본다 — 넘치면 경고로 드러난다
        tuplets = set(forced).union(*choice)
        pins = {i: as_triplet(p) if (i in tuplets and p.tuplet is None
                                     and (triplet_pins is None or i in triplet_pins))
                else p for i, p in pinned.items()}
        lows = {i: q * 2 / 3 if convertible and i in tuplets else q
                for i, (q, convertible) in (floors or {}).items()}
        pins = _spread_spans(props, pins, spans, tuplets, lows)
        if pins is None:
            fitted, exact = [_nearest(p) for p in props], False
        else:
            fitted, exact = fit_durations(props, target, pinned=pins,
                                          tuplet_indices=tuplets, floors=lows)
        cost = sum(abs(d.quarters - p) for d, p in zip(fitted, props))
        if best is None or (not exact, cost) < best[0]:
            best = ((not exact, cost), fitted, exact, tuplets)
    return best[1], best[2], best[3]


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
    triplet_windows: tuple[tuple[int, ...], ...] = ()
    # 이 성부가 온쉼표 하나뿐 — 놓인 x 와 무관하게 마디 전체를 쉰다
    whole_rest: bool = False


@dataclass(frozen=True)
class Alignment:
    """성부별 (이벤트 인덱스 | None=채움 쉼표, 길이) 목록과 공통 시간축."""

    slots: tuple[tuple[tuple[int | None, LegalDuration], ...], ...]
    union_xs: tuple[float, ...]
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


def _owns_triplet(voice: VoiceEvents, i: int) -> bool:
    """이 이벤트의 성부가 스스로 셋잇단 표기를 가졌는가."""
    return i in voice.tuplets or any(i in window for window in voice.triplet_windows)


def _segment_constraints(voices, positions, count):
    """이벤트 제약을 공통 구간 제약으로 옮긴다.

    다음 이벤트가 있는 쉼표 글리프의 길이는 정확한 제약(Span)이다. 음의 확정
    길이와 성부 마지막 쉼표의 길이는 하한일 뿐이다 — 두 성부 타브는 그 뒤의
    쉼표를 흔히 생략한다. 셋잇단은 이벤트가 시작하는 구간에 준다 — 마지막 음이
    덮는 마디 끝 구간을 전부 셋잇단으로 만들면 안 된다.

    Returns: (구간 하한, 확정 셋잇단 구간, 표기 창, 쉼표 Span)
    """
    floors: dict[int, tuple[float, bool]] = {}
    forced: set[int] = set()
    windows: list[tuple[int, ...]] = []
    spans: list[Span] = []
    for voice, where in zip(voices, positions):
        for i, k in enumerate(where):
            if i in voice.tuplets:
                forced.add(k)
            if i not in voice.pins:
                continue
            last = i + 1 == len(where)
            stop = count if last else where[i + 1]
            pin = voice.pins[i]
            if i in voice.rests and not last:
                spans.append(Span(k, stop, pin, _owns_triplet(voice, i)))
            elif stop == k + 1 and pin.quarters > floors.get(k, (0.0, False))[0]:
                floors[k] = (pin.quarters, _owns_triplet(voice, i) and pin.tuplet is None)
        for window in voice.triplet_windows:
            mapped = [where[i] for i in window]
            windows.append(tuple(range(min(mapped), max(mapped) + 1)))
    return floors, forced, windows, tuple(spans)


def _voice_slots(voice, where, onsets, segments, tuplet_segments, total, problems):
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
            fill(span, event=i)         # 길이를 모르는 쉼표는 다음 이벤트까지 쉰다
            continue
        # 길이를 모르는 음은 다음 음까지 이어진다고 본다. 그 길이가 음가가 아니면
        # 셋잇단 음은 첫 구간 길이를, 아니면 들어가는 최대 음가를 쓰고 나머지를 쉰다
        pin = voice.pins.get(i)
        if (pin is not None and pin.tuplet is None and k in tuplet_segments
                and _owns_triplet(voice, i)):
            pin = as_triplet(pin)       # 시간축이 셋잇단으로 푼 자리의 꼬리 음가
        chosen = (pin or _exact_legal(span, first.tuplet is not None)
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
    floors, forced, windows, spans = _segment_constraints(voices, positions, len(union))
    segments, exact, tuplet_segments = fit_with_triplets(
        proportions(union, measure_end_x, target), target, {}, forced, windows,
        spans=spans, floors=floors)
    onsets = [0.0]
    for segment in segments:
        onsets.append(onsets[-1] + segment.quarters)
    total = onsets[-1] if union else target
    problems: list[str] = []
    slots = tuple(
        tuple((0, d) if i == 0 else (None, d)
              for i, d in enumerate(rest_durations(target))) if v.whole_rest
        else tuple(_voice_slots(v, where, onsets, segments, tuplet_segments, total,
                                problems))
        for v, where in zip(voices, positions))
    return Alignment(slots, tuple(union), exact and not problems,
                     tuple(problems))
