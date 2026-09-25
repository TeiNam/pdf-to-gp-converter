"""여러 성부를 하나의 시간축에 올린다 — 두 성부 타브의 음가 풀이.

성부마다 따로 x 간격을 맞추면 두 성부가 같은 x 에 둔 음이 다른 시각에
시작한다. 모든 성부 이벤트의 x 를 합친 공통 시간축의 구간 길이를 먼저 풀고,
각 성부의 음은 그 시각 사이를 채운다. 타브는 다른 성부가 연주하는 동안의
쉼표를 흔히 생략하므로 남는 몫은 채움 쉼표가 된다.
"""

from dataclasses import dataclass, field
from functools import cache

from .durations import (
    EPSILON,
    LEGAL,
    UNIT_QUARTERS,
    LegalDuration,
    _nearest,
    _units,
    as_triplet,
    best_window_choice,
    proportions,
    rest_durations,
    window_options,
)

# 값의 격자 (단위 = 1/96 박). 일반 음가는 전부 3단위의 배수이고, 셋잇단 음가는
# 그 2/3 이라 2단위의 배수다 (점64분 셋잇단 = 6단위). 6의 배수는 양쪽 해석이 된다
PLAIN_UNITS = 3
TRIPLET_UNITS = 2
# 자유 구간의 합성값 격자 — 64분(6단위)·16분 셋잇단(16단위). 더 잘게 두면 후보가
# 늘어 풀이가 느려진다. 더 잘은 값은 확정 길이가 닫는 구간에서 직접 들어온다
COARSE_PLAIN_UNITS = 6
COARSE_TRIPLET_UNITS = 16
# 구간은 음이 아니라 두 성부 시각 사이의 간격이다 — 단일 음가가 아닌 값의 벌점
COMPOSITE_PENALTY = 0.1
# 셋잇단 표시 없는 구간의 셋잇단 값, 셋잇단 구간의 일반값처럼 보이는 값의 벌점
TRIPLET_PENALTY = 0.15
# 자유 구간 합성값을 x 비례값 근처로 좁힌다 (박)
SEGMENT_REACH = 1.0
# 확정 음가가 다음 이벤트까지의 x 비례보다 이 배수 넘게 짧으면 뒤에 쉼표가 생략된
# 것으로 본다. 조판 간격은 음가에 비례보다 완만해(긴 음이 덜 넓다) 여유를 둔다
GAP_RATIO = 1.75


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


@dataclass(frozen=True)
class Span:
    """확정 길이가 공통 구간 start..stop-1 의 합을 정한다."""

    start: int
    stop: int
    length: LegalDuration
    # 이 이벤트의 성부가 셋잇단 표기를 가졌다 — 셋잇단 자리면 셋잇단 길이다
    can_triplet: bool = False


def _plain(units: int) -> bool:
    return units % PLAIN_UNITS == 0


def _triplet(units: int) -> bool:
    return units % TRIPLET_UNITS == 0


def _owns_triplet(voice: VoiceEvents, i: int) -> bool:
    """이 이벤트의 성부가 스스로 셋잇단 표기를 가졌는가."""
    return i in voice.tuplets or any(i in window for window in voice.triplet_windows)


def _union_timeline(voices, tolerance):
    union: list[float] = []
    for x in sorted(x for v in voices if not v.whole_rest for x in v.xs):
        if not union or x - union[-1] > tolerance:
            union.append(x)
    positions = [[min(range(len(union)), key=lambda k: abs(union[k] - x)) for x in v.xs]
                 if not v.whole_rest else [] for v in voices]
    return union, positions


@dataclass
class _Constraints:
    spans: list[Span] = field(default_factory=list)
    # 한 구간짜리 하한 — (길이, 셋잇단 자리면 2/3 로 줄어도 되는가)
    floors: dict[int, tuple[float, bool]] = field(default_factory=dict)
    prefer: set[int] = field(default_factory=set)
    windows: list[tuple[int, ...]] = field(default_factory=list)


def _constraints(voices, positions, props) -> _Constraints:
    """이벤트 제약을 공통 구간 제약으로 옮긴다.

    다음 이벤트가 있는 쉼표 글리프의 길이는 정확한 제약이다. 음(과 성부 마지막
    쉼표)의 확정 길이는 다음 이벤트까지의 x 비례와 맞으면 정확한 제약, 훨씬
    짧으면 뒤에 쉼표가 생략된 것이라 하한만 준다 — 하한을 여러 구간에 걸쳐
    풀이 상태로 들고 다니면 조밀한 마디에서 상태가 폭증했다.
    """
    result = _Constraints()
    count = len(props)
    for voice, where in zip(voices, positions):
        for i, k in enumerate(where):
            if i in voice.tuplets:
                result.prefer.add(k)
            if i not in voice.pins:
                continue
            last = i + 1 == len(where)
            stop = count if last else where[i + 1]
            pin = voice.pins[i]
            owns = _owns_triplet(voice, i) and pin.tuplet is None
            fits = sum(props[k:stop]) <= GAP_RATIO * pin.quarters + EPSILON
            if (i in voice.rests and not last) or fits:
                result.spans.append(Span(k, stop, pin, owns))
            elif stop == k + 1 and pin.quarters > result.floors.get(k, (0.0, False))[0]:
                result.floors[k] = (pin.quarters, owns)
        for window in voice.triplet_windows:
            mapped = [where[i] for i in window]
            result.windows.append(tuple(range(min(mapped), max(mapped) + 1)))
    return result


def _span_length(span: Span, triplets: set[int]) -> int | None:
    length = span.length
    if span.can_triplet and span.start in triplets:
        length = as_triplet(length)
    return _units(length.quarters)


def _candidates(prop, low, convertible, single_units, composite_units, triplet, plain):
    """(구간 단위, 비용) 후보 — 셋잇단·일반 자리 제약과 하한을 건다."""
    result = []
    floor_units = low / UNIT_QUARTERS
    for units in sorted(single_units | composite_units):
        if triplet and not _triplet(units):
            continue
        if plain and not _plain(units):
            continue
        shortest = floor_units * 2 / 3 if (convertible and triplet) else floor_units
        if units < shortest - EPSILON:
            continue
        quarters = units * UNIT_QUARTERS
        cost = abs(quarters - prop)
        if units not in single_units:
            if abs(quarters - prop) > prop + SEGMENT_REACH:
                continue
            cost += COMPOSITE_PENALTY
        if (triplet and _plain(units)) or (not triplet and not _plain(units)):
            cost += TRIPLET_PENALTY
        result.append((units, cost))
    return sorted(result, key=lambda pair: pair[1])


def _solve(props, target_units, constraints, triplets, plains):
    """셋잇단 자리를 정한 뒤 구간 단위를 푼다. Returns: (비용, 단위 튜플) | None."""
    ends: dict[int, list[tuple[int, int]]] = {}
    for span in constraints.spans:
        units = _span_length(span, triplets)
        if units is None:
            return None
        ends.setdefault(span.start, []).append((span.stop, units))
    singles = {_units(d.quarters) for d in LEGAL}
    singles |= {u for s in constraints.spans if (u := _span_length(s, triplets))}
    composites = {u for u in range(1, target_units + 1)
                  if u % COARSE_PLAIN_UNITS == 0 or u % COARSE_TRIPLET_UNITS == 0}
    table = [_candidates(props[k], *constraints.floors.get(k, (0.0, False)),
                         singles, composites, k in triplets, k in plains)
             for k in range(len(props))]
    # 뒤 구간들이 채울 수 있는 단위 범위 — 못 채우는 상태를 일찍 버린다. Span 이
    # 닫히는 구간은 후보표 밖의 길이(1/32 박 등)를 직접 받으므로 범위를 열어 둔다
    closers = {span.stop - 1 for span in constraints.spans}
    # 닫는 구간도 그 자리 음의 하한을 지킨다 (셋잇단 자리면 2/3 까지)
    floor_units = []
    for k in range(len(props)):
        low_q, convertible = constraints.floors.get(k, (0.0, False))
        scale = 2 / 3 if (convertible and k in triplets) else 1.0
        floor_units.append(low_q * scale / UNIT_QUARTERS)
    low = [0] * (len(props) + 1)
    high = [0] * (len(props) + 1)
    for k in range(len(props) - 1, -1, -1):
        units = [u for u, _ in table[k]] or [0]
        smallest, largest = (1, target_units) if k in closers else (min(units), max(units))
        low[k], high[k] = low[k + 1] + smallest, high[k + 1] + largest

    @cache
    def solve(index, onset, pending):
        # pending: 열린 Span 이 닫혀야 할 (구간, 시각) — 닫는 구간은 길이가 정해진다
        pending = tuple(p for p in pending if p[0] != index)
        if index == len(props):
            return (0.0, ()) if onset == target_units and not pending else None
        remaining = target_units - onset
        if not low[index] <= remaining <= high[index] and not pending:
            return None
        opened = pending + tuple((stop, onset + units)
                                 for stop, units in ends.get(index, ()))
        closing = {required - onset for stop, required in opened if stop == index + 1}
        if len(closing) > 1:
            return None
        if closing:
            need = closing.pop()
            options = [(need, next((c for u, c in table[index] if u == need), 0.0))]
            if need <= 0 or need < floor_units[index] - EPSILON or (
                    index in triplets and not _triplet(need)) or (
                    index in plains and not _plain(need)):
                return None
        else:
            options = table[index]
        state = tuple(sorted(opened))
        best = None
        for units, cost in options:
            if units > remaining:
                continue
            if any(onset + units > required for _, required in opened):
                continue            # 열린 Span 의 닫는 시각을 넘어선다
            tail = solve(index + 1, onset + units, state)
            if tail is not None and (best is None or cost + tail[0] < best[0] - EPSILON):
                best = (cost + tail[0], (units,) + tail[1])
        return best

    try:
        return solve(0, 0, ())
    finally:
        solve.cache_clear()


def fit_timeline(props: list[float], target: float, constraints: _Constraints,
                 ) -> tuple[list[float], bool, set[int]]:
    """공통 시간축 구간 길이(박)를 푼다. 셋잇단 표기 창마다 셋잇단 구간 하나를 고른다.

    Span 은 "이 구간들의 합" 제약이라 구간 하나씩 고정하면 겹친 Span 에서 틀린
    분할에 갇힌다 — 열린 Span 이 닫혀야 할 시각을 풀이 상태에 싣는다.
    Returns: (구간 길이, 정확히 풀렸는가, 셋잇단 자리)
    """
    target_units = _units(target)
    if not props:
        return [], True, set()      # 모든 성부가 온쉼표 — 풀 구간이 없다
    if target_units is None:
        return [_nearest(p).quarters for p in props], False, set()

    def evaluate(choice):
        triplets = set(constraints.prefer).union(*choice)
        plains = {k for window, run in zip(constraints.windows, choice)
                  for k in window if k not in run} - triplets
        solution = _solve(props, target_units, constraints, triplets, plains)
        if solution is None:
            return (True, 0.0), [_nearest(p).quarters for p in props], False, triplets
        return (False, solution[0]), [u * UNIT_QUARTERS for u in solution[1]], True, triplets

    return best_window_choice(window_options(constraints.windows), evaluate)[1:]


def _exact_legal(quarters: float, tuplet: bool) -> LegalDuration | None:
    """정확히 이 길이인 legal 값. 셋잇단 자리면 셋잇단을 먼저 본다."""
    matches = [d for d in LEGAL if abs(d.quarters - quarters) < EPSILON]
    matches.sort(key=lambda d: bool(d.tuplet) != tuplet)
    return matches[0] if matches else None


def _largest_within(quarters: float, tuplet: bool) -> LegalDuration | None:
    fitting = [d for d in LEGAL if bool(d.tuplet) == tuplet
               and d.quarters <= quarters + EPSILON]
    return max(fitting, key=lambda d: d.quarters, default=None)


def _voice_slots(voice, where, onsets, triplets, total, problems):
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
        tuplet = k in triplets and _owns_triplet(voice, i)
        if i in voice.rests and i not in voice.pins:
            fill(span, event=i)         # 길이를 모르는 쉼표는 다음 이벤트까지 쉰다
            continue
        pin = voice.pins.get(i)
        if pin is not None and pin.tuplet is None and tuplet:
            pin = as_triplet(pin)       # 시간축이 셋잇단으로 푼 자리의 꼬리 음가
        # 길이를 모르는 음은 다음 음까지 이어진다고 본다. 그 길이가 음가가 아니면
        # 첫 구간 길이, 그다음 들어가는 최대 음가를 쓰고 나머지를 쉰다
        first = onsets[k + 1] - onsets[k]
        chosen = (pin or _exact_legal(span, k in triplets)
                  or _exact_legal(first, k in triplets)
                  or _largest_within(span, k in triplets) or _largest_within(span, False))
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
    """여러 성부를 하나의 시간축에 올린다."""
    union, positions = _union_timeline(voices, tolerance)
    props = proportions(union, measure_end_x, target)
    constraints = _constraints(voices, positions, props)
    segments, exact, triplets = fit_timeline(props, target, constraints)
    onsets = [0.0]
    for segment in segments:
        onsets.append(onsets[-1] + segment)
    total = onsets[-1] if union else target
    problems: list[str] = []
    slots = tuple(
        tuple((0, d) if i == 0 else (None, d)
              for i, d in enumerate(rest_durations(target))) if v.whole_rest
        else tuple(_voice_slots(v, where, onsets, triplets, total, problems))
        for v, where in zip(voices, positions))
    return Alignment(slots, tuple(union), exact and not problems, tuple(problems))

