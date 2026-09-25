"""음길이를 4분음표 단위로 다루고, 마디 합 제약 아래 legal 값으로 스냅한다.

PDF 를 모르는 순수 계산 모듈이다. 추출기가 확정한 음가는 고정하고,
나머지 음가만 x 간격 비례와 마디 합 제약으로 추정한다.
"""

from dataclasses import dataclass
from functools import lru_cache
from math import prod

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
        return [min(candidates_at(i) or LEGAL, key=lambda d: abs(d.quarters - prop))
                for i, prop in enumerate(props)], False
    return list(solution[1]), True


def _window_runs(window: tuple[int, ...]) -> list[frozenset[int]]:
    """창 안에서 셋잇단일 수 있는 연속 구간 — 두 이벤트 이상 (4분+8분 셋잇단).

    창 전체를 맨 앞에 둔다 — 가장 흔한 읽기(세 음 셋잇단)부터 본다.
    """
    runs = [frozenset(window[a:b]) for a in range(len(window))
            for b in range(a + 2, len(window) + 1)]
    return sorted(runs, key=len, reverse=True)


# 창 조합을 전부 볼 상한 — 넘으면 좌표 하강
EXHAUSTIVE_TRIPLET_CHOICES = 256


def best_window_choice(options: list[list[frozenset[int]]], evaluate):
    """창마다 셋잇단 구간 하나를 골라 `evaluate` 가 가장 낮은 조합을 찾는다.

    `evaluate(셋잇단 인덱스 집합)`은 ((실패 여부, 비용), ...)을 돌려준다.
    큰 조합은 한 창씩 먼저 바꾸되, 실패하면 함께 바꿔야 하는 조합도 확인한다.
    여러 성부의 창이 같은 집합을 만들면 풀이 결과를 재사용한다.
    """
    cached = lru_cache(maxsize=DP_CACHE_SIZE)(evaluate)

    def trial(choice):
        return cached(frozenset().union(*choice))

    def exhaustive():
        # 같은 시간축을 만드는 성부별 조합은 한 번만 센다 — 3^16개 조합도
        # 동일한 창이 양쪽에 있으면 실제 선택은 3^8개뿐이다.
        choices = [frozenset()]
        for runs in options:
            choices = list(dict.fromkeys(chosen | run for chosen in choices for run in runs))
        return min(map(cached, choices), key=lambda result: result[0])

    if prod(len(runs) for runs in options) <= EXHAUSTIVE_TRIPLET_CHOICES:
        return exhaustive()
    choice = [runs[0] for runs in options]
    best = trial(choice)
    improved = True
    while improved:
        improved = False
        for position, runs in enumerate(options):
            for run in runs:
                if run == choice[position]:
                    continue
                candidate = trial(choice[:position] + [run] + choice[position + 1:])
                if candidate[0] < best[0]:
                    best, choice[position], improved = candidate, run, True
    # ponytail: 실패한 큰 조합만 완전 탐색한다. 후보가 더 커져 병목이 되면
    # 창 제약을 음가 DP에 합쳐, 닫힌 창의 선택을 풀이 상태에서 버린다.
    return exhaustive() if best[0][0] else best


def window_options(windows) -> list[list[frozenset[int]]]:
    return [runs for runs in map(_window_runs, windows) if runs]


def fit_with_triplets(props: list[float], target: float,
                      pinned: dict[int, LegalDuration],
                      forced: set[int],
                      windows: list[tuple[int, ...]],
                      ) -> tuple[list[LegalDuration], bool, set[int]]:
    """확정 셋잇단 묶음에 더해, '3' 표기 창마다 셋잇단 구간 하나를 반드시 고른다.

    창의 이벤트를 전부 강제하면 4분+8분 셋잇단 뒤의 정상 음이 끌려 들어가고,
    후보만 열면 간격이 고른 마디에서 표기를 버린다. 창마다 연속 구간 하나를
    골라 합이 맞는 것 중 x 간격에 가장 가까운 해를 쓴다.

    Returns: (스냅 결과, 합이 정확히 맞았는지, 셋잇단으로 푼 인덱스)
    """
    def evaluate(selected):
        tuplets = set(forced) | selected
        pins = {i: as_triplet(p) if i in tuplets and p.tuplet is None else p
                for i, p in pinned.items()}
        fitted, exact = fit_durations(props, target, pinned=pins, tuplet_indices=tuplets)
        cost = sum(abs(d.quarters - p) for d, p in zip(fitted, props))
        return (not exact, cost), fitted, exact, tuplets

    return best_window_choice(window_options(windows), evaluate)[1:]


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
