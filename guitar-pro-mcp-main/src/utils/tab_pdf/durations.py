"""음길이를 4분음표 단위로 다루고, 마디 합 제약 아래 legal 값으로 스냅한다.

PDF 를 모르는 순수 계산 모듈이다. x 간격이 음길이에 비례한다는 조판 성질만 쓴다.
빔 기하를 쓰지 않는 이유는 docs/superpowers/plans/2026-08-31-pdf-tab-to-gp5.md 2절 참조.
"""

from dataclasses import dataclass

# 그리디 보정 반복 상한 — beat 수가 수십 개여도 넉넉하다
MAX_REPAIR_STEPS = 200
EPSILON = 1e-9


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


def fit_durations(props: list[float],
                  target: float) -> tuple[list[LegalDuration], bool]:
    """비례값을 legal 값으로 스냅하되 합이 정확히 target 이 되게 보정한다.

    독립 스냅은 반올림 때문에 합이 어긋난다. 스냅 오차가 가장 적게 늘어나는
    beat 하나를 다른 legal 값으로 바꾸는 그리디를 합이 맞을 때까지 반복한다.

    Returns: (스냅 결과, 합이 정확히 맞았는지)
    """
    if not props:
        return [], True
    current = [_nearest(prop) for prop in props]
    for _ in range(MAX_REPAIR_STEPS):
        diff = target - sum(legal.quarters for legal in current)
        if abs(diff) < EPSILON:
            return current, True
        best = None
        for index, prop in enumerate(props):
            for candidate in LEGAL:
                if candidate is current[index]:
                    continue
                remaining = diff - (candidate.quarters - current[index].quarters)
                if abs(remaining) >= abs(diff) - EPSILON:
                    continue          # 차이를 줄이지 못하는 후보
                cost = (abs(candidate.quarters - prop)
                        - abs(current[index].quarters - prop))
                if best is None or cost < best[0]:
                    best = (cost, index, candidate)
        if best is None:
            return current, False     # 더 줄일 수 없다 — 거짓말하지 않고 실패 보고
        current[best[1]] = best[2]
    return current, False
