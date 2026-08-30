"""음길이를 4분음표 단위로 다루고, 마디 합 제약 아래 legal 값으로 스냅한다.

PDF 를 모르는 순수 계산 모듈이다. x 간격이 음길이에 비례한다는 조판 성질만 쓴다.
빔 기하를 쓰지 않는 이유는 docs/superpowers/plans/2026-08-31-pdf-tab-to-gp5.md 2절 참조.
"""

from dataclasses import dataclass
from functools import lru_cache

EPSILON = 1e-9
# 모든 legal 길이는 0.125 (32분음표) 의 배수다. 정수 단위로 환산해 DP 로 정확히 푼다.
UNIT_QUARTERS = 0.125
# DP 캐시 크기 — (beat index, 남은 단위) 조합 상한. beat 수십 × 단위 수백이면 충분
DP_CACHE_SIZE = 100_000


@dataclass(frozen=True)
class LegalDuration:
    value: int          # pyguitarpro Duration.value (1/2/4/8/16/32)
    dotted: bool
    quarters: float     # 4분음표 단위 길이


LEGAL: tuple[LegalDuration, ...] = (
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


def _units(quarters: float) -> int:
    return round(quarters / UNIT_QUARTERS)


def fit_durations(props: list[float],
                  target: float) -> tuple[list[LegalDuration], bool]:
    """비례값을 legal 값으로 스냅하되 합이 정확히 target 이 되게 맞춘다.

    독립 스냅은 반올림 때문에 합이 어긋난다. legal 길이가 모두 0.125 의 배수이므로
    정수 단위 DP 로 "합이 정확히 target 이면서 스냅 오차 총합이 최소" 인 조합을
    찾는다. 그리디와 달리 해가 존재하면 반드시 찾는다 — 예: `[4.0, 4.0]` 을
    target 4.0 에 맞출 때 그리디는 실패했지만 DP 는 `[2.0, 2.0]` 을 찾는다.

    Returns: (스냅 결과, 합이 정확히 맞았는지)
    """
    if not props:
        return [], True

    target_units = _units(target)
    if target_units <= 0:
        return [_nearest(prop) for prop in props], False

    @lru_cache(maxsize=DP_CACHE_SIZE)
    def solve(index: int, remaining: int) -> tuple[float, tuple[int, ...]] | None:
        """props[index:] 로 remaining 단위를 정확히 채우는 최소비용 선택."""
        if index == len(props):
            return (0.0, ()) if remaining == 0 else None
        best: tuple[float, tuple[int, ...]] | None = None
        for choice, legal in enumerate(LEGAL):
            need = _units(legal.quarters)
            if need > remaining:
                continue
            tail = solve(index + 1, remaining - need)
            if tail is None:
                continue
            cost = abs(legal.quarters - props[index]) + tail[0]
            if best is None or cost < best[0] - EPSILON:
                best = (cost, (choice,) + tail[1])
        return best

    try:
        solution = solve(0, target_units)
    finally:
        solve.cache_clear()

    if solution is None:
        # 정확히 맞출 조합이 없다 — 거짓말하지 않고 최근접 스냅 + 실패 보고
        return [_nearest(prop) for prop in props], False
    return [LEGAL[choice] for choice in solution[1]], True
