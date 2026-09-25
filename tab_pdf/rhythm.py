"""타브의 기둥·빔·꼬리·붙임점에서 확정할 수 있는 음가를 읽는다."""

from . import bands, durations, smufl

# 프렛 숫자는 선을 가리지 않게 기둥에서 조금 떨어져 조판된다.
STEM_GAP = 11.0
STEM_MARGIN = 35.0
DOT_WINDOW = 10.0
DOT_Y_TOLERANCE = 4.0
# 빔 스택은 기둥 끝에서 시작해 일정 간격으로 쌓인다 — 줄 간격 비율로 잰다.
# 실측: 첫 빔은 기둥 끝 0.1pt 안, 빔 사이 3.8pt (줄 간격 7.7 의 절반)
BEAM_END_RATIO = 0.5
BEAM_GAP_RATIO = 0.8


def stems_for(geo, system, glyph):
    """이 글리프에 닿는 타브 기둥. 멜로디 기둥·마디선은 제외한다."""
    return [stem for stem in geo.vlines
            if glyph.x - 1.0 <= stem.x <= glyph.x_end + 1.0
            and system.tab_ys[0] - STEM_MARGIN <= stem.y0
            and stem.y1 <= system.tab_ys[-1] + STEM_MARGIN
            and stem.y1 - stem.y0 >= 8.0
            and min(abs(stem.y0 - glyph.y), abs(stem.y1 - glyph.y)) <= STEM_GAP]


def has_dot(geo, glyph, end_x):
    return any(smufl.name(g.char) == "augmentationDot"
               and 0 < g.x - glyph.x <= DOT_WINDOW and g.x < end_x
               and abs(g.y - glyph.y) <= DOT_Y_TOLERANCE
               for g in geo.glyphs)


def duration_for(geo, system, glyph, end_x):
    """분명한 빔/꼬리만 고정한다. 기둥만 있는 타브는 2분·4분을 구별 못 한다."""
    candidates = []
    spacing = (system.tab_ys[-1] - system.tab_ys[0]) / (len(system.tab_ys) - 1)
    for stem in stems_for(geo, system, glyph):
        end_y = max((stem.y0, stem.y1), key=lambda y: abs(y - glyph.y))
        # 기둥 끝에서 안쪽으로 잰 거리. 스택 전체 높이에 상한을 두지 않는다 —
        # 빔 간격이 넓은 악보에서 마지막 빔이 잘려 64분이 32분이 됐다.
        distances = sorted(abs(beam.y_at(stem.x) - end_y) for beam in geo.beams
                           if beam.x0 - 0.5 <= stem.x <= beam.x1 + 0.5
                           and stem.y0 - 2 <= beam.y_at(stem.x) <= stem.y1 + 2)
        levels = []
        for distance in distances:
            if not levels:
                if distance > BEAM_END_RATIO * spacing:
                    break           # 기둥 끝에 닿은 빔이 없다
                levels.append(distance)
            elif distance - levels[-1] > BEAM_GAP_RATIO * spacing:
                break               # 스택이 끊겼다 — 다른 음의 빔이다
            elif distance - levels[-1] > 1.0:
                # 같은 빔을 외곽선과 면으로 중복 수집했어도 한 줄로 센다.
                levels.append(distance)
        flags = [((ord(g.char) - 0xE240) // 2 + 1)
                 for g in geo.glyphs
                 if 0xE240 <= ord(g.char) <= 0xE247
                 and abs(g.x - stem.x) <= 1.5
                 and abs(g.y - end_y) <= 3.0]
        count = max(len(levels), max(flags, default=0))
        if 1 <= count <= 4:
            value = 4 * 2 ** count
            dotted = has_dot(geo, glyph, end_x)
            candidates.append(durations.LegalDuration(
                value, dotted, 4.0 / value * (1.5 if dotted else 1.0)))
    if candidates and all(d == candidates[0] for d in candidates):
        return candidates[0]
    return None


def note_pins(geo, system, beat_xs, glyphs, end_x, tolerance=2.0):
    """각 beat의 확정 음가. 충돌하는 두 성부는 합쳐 추측하지 않는다."""
    pins = {}
    for index, x in enumerate(beat_xs):
        following = beat_xs[index + 1] if index + 1 < len(beat_xs) else end_x
        values = [duration_for(geo, system, g, following) for g in glyphs
                  if abs(g.x - x) <= tolerance]
        known = [value for value in values if value is not None]
        if known and all(value == known[0] for value in known):
            pins[index] = known[0]
    return pins


def _stem_directions(geo, system, notes):
    """음마다 기둥 방향 — 0=위, 1=아래. 기둥 없는 음은 빠진다."""
    directions = {}
    for note in notes:
        stems = stems_for(geo, system, note)
        if stems:
            stem = min(stems, key=lambda s: (
                min(abs(s.y0 - note.y), abs(s.y1 - note.y)),
                abs(s.x - (note.x + note.x_end) / 2)))
            end = max((stem.y0, stem.y1), key=lambda y: abs(y - note.y))
            directions[note] = int(end > note.y)
    return directions


def _x_groups(notes, tolerance):
    groups = []
    for note in sorted(notes, key=lambda g: g.x):
        if groups and note.x - groups[-1][0].x <= tolerance:
            groups[-1].append(note)
        else:
            groups.append([note])
    return groups


def voice_assignments(geo, system, notes, rests, target=4.0, tolerance=2.0):
    """한 마디의 음·쉼표를 두 성부로 나눈다. 단성부면 None.

    다성부 근거는 셋이다 — 같은 시각의 반대 방향 기둥, 음과 같은 x 의 쉼표,
    음이 있는 마디의 온쉼표(한 성부 안에서 온쉼표와 음은 공존할 수 없다 — 단
    4박을 넘는 박자에서는 온쉼표가 마디 전체가 아니라 4박 쉼표다).
    한 성부의 기둥도 위아래로 바뀌므로 방향만으로는 판정하지 않는다.
    """
    directions = _stem_directions(geo, system, notes)
    groups = _x_groups(notes, tolerance)
    whole_rests = ([r for r in rests if smufl.name(r.char) == "restWhole"]
                   if target <= 4.0 + durations.EPSILON else [])
    conflict = any(
        {directions[g] for g in group if g in directions} == {0, 1}
        for group in groups)
    conflict |= any(abs(rest.x - note.x) <= tolerance
                    for rest in rests for note in notes)
    if not conflict and not (whole_rests and notes):
        return None
    assignments = {}
    for group in groups:
        known = [g for g in group if g in directions]
        for note in group:
            if not conflict:
                assignments[note] = 0   # 온쉼표만이 근거다 — 음은 첫 성부에 둔다
                continue
            # 화음의 기둥은 구성음 하나에만 닿을 수 있다.
            reference = min(known, key=lambda g: abs(g.y - note.y)) if known else None
            assignments[note] = directions.get(note, directions.get(reference, 0))
    used = set(assignments.values())
    middle = (system.tab_ys[0] + system.tab_ys[-1]) / 2
    for rest in rests:
        occupied = {assignments[n] for n in notes if abs(n.x - rest.x) <= tolerance}
        if rest in whole_rests and len(used) == 1:
            occupied = used     # 온쉼표는 음이 없는 쪽 성부의 마디 전체 쉼이다
        assignments[rest] = (1 - next(iter(occupied)) if len(occupied) == 1
                             else int(rest.y >= middle))
    return assignments


def _on_beam(geo, system, beam, note) -> bool:
    return any(beam.x0 - 0.5 <= stem.x <= beam.x1 + 0.5
               and stem.y0 - 2 <= beam.y_at(stem.x) <= stem.y1 + 2
               for stem in stems_for(geo, system, note))


def beam_members(geo, system, beam, beat_xs, notes, tolerance=2.0):
    """이 빔에 연결된 기둥의 beat 인덱스."""
    return {index for index, x in enumerate(beat_xs)
            for note in notes if abs(note.x - x) <= tolerance
            and _on_beam(geo, system, beam, note)}


def _beams_near(geo, mark):
    """'3' 표기가 가리키는 빔 후보 — (표기와의 세로 거리, 빔)."""
    return [(abs(beam.y_at(mark.x) - mark.y), beam) for beam in geo.beams
            if beam.x0 - 3 <= mark.x <= beam.x1 + 3
            and abs(beam.y_at(mark.x) - mark.y) <= 12]


def mark_beam_notes(geo, system, mark, notes):
    """'3' 표기에 가장 가까운 빔이 묶은 음. 빔이 없거나 음이 없으면 빈 목록."""
    for _, beam in sorted(_beams_near(geo, mark), key=lambda pair: pair[0]):
        held = [n for n in notes if _on_beam(geo, system, beam, n)]
        if held:
            return held
    return []


def is_triplet_mark(mark, system, notes=(), fret_glyphs=()) -> bool:
    """타브의 셋잇단 표기 — SMuFL '3' 또는 음이 아닌 텍스트 '3'.

    위·아래 기둥 성부의 '3' 은 기둥 끝 너머(타브 대역 밖)에 찍힌다. 위로는 오선
    바로 아래까지만 받는다 — 오선 선율의 잇단음표가 섞이면 안 된다.
    """
    if mark.char == chr(0xE883):
        top = max(system.melody_ys[-1] + bands.SPAN_SLACK,
                  system.tab_ys[0] - STEM_MARGIN)
        return top <= mark.y <= system.tab_ys[-1] + STEM_MARGIN
    return (mark.char == "3" and mark not in notes and mark not in fret_glyphs
            and system.tab_ys[0] - STEM_MARGIN <= mark.y
            <= system.tab_ys[-1] + STEM_MARGIN)


def triplet_groups(geo, system, beat_xs, notes, bounds, fret_glyphs=()):
    """'3'의 위치와 빔으로 셋잇단 범위를 정한다.

    Returns: (확정 묶음 목록, 표기 창 목록). 빔 양 끝 음 사이의 이벤트가 정확히
    셋이면 확정한다 — 가운데 쉼표는 빔에 닿지 않아도 묶음 안이다. 빔이 없거나
    이벤트 수가 다르면(4분+8분 셋잇단 등) 창만 넘긴다. 창 안에서 셋잇단 구간
    하나를 반드시 고르는 것은 durations.fit_with_triplets 의 몫이다.
    """
    forced: list[list[int]] = []
    windows: list[tuple[int, ...]] = []
    if len(beat_xs) < 2:
        return forced, windows
    for mark in geo.glyphs:
        if not bounds[0] <= mark.x < bounds[1]:
            continue
        explicit = mark.char == chr(0xE883)
        if not is_triplet_mark(mark, system, notes, fret_glyphs):
            continue
        spans = []
        for distance, beam in _beams_near(geo, mark):
            members = beam_members(geo, system, beam, beat_xs, notes)
            if members:
                spans.append((distance, list(range(min(members), max(members) + 1))))
        exact = [(d, span) for d, span in spans if len(span) == 3]
        if exact:
            forced.append(min(exact)[1])
        elif explicit and spans:
            windows.append(tuple(min(spans)[1]))
        elif explicit:
            nearest = sorted(range(len(beat_xs)), key=lambda i: abs(beat_xs[i] - mark.x))
            windows.append(tuple(sorted(nearest[:3])))
    taken = {i for group in forced for i in group}
    return forced, [w for w in windows if len(w) >= 2 and not taken & set(w)]


def shared_triplet_group(geo, system, beat_xs, notes, bounds, pins, target):
    """오선에만 적힌 '3'은 타브의 확정 음가·빔·마디 합이 모두 맞아야 쓴다."""
    if len(pins) != len(beat_xs) or any(d.tuplet for d in pins.values()):
        return []
    total = sum(d.quarters for d in pins.values())
    if total <= target + durations.EPSILON:
        return []
    marks = [g for g in geo.glyphs
             if g.char in ("3", chr(0xE883)) and bounds[0] <= g.x < bounds[1]
             and system.melody_ys[0] - 12 <= g.y <= system.melody_ys[-1] + 18]
    candidates = set()
    for beam in geo.beams:
        members = tuple(sorted(beam_members(geo, system, beam, beat_xs, notes)))
        if len(members) != 3 or members[-1] - members[0] != 2:
            continue
        if not any(abs(mark.x - beat_xs[members[1]]) <= 6 for mark in marks):
            continue
        values = [pins[i] for i in members]
        if len(set(values)) != 1:
            continue
        if abs(total - sum(d.quarters for d in values) / 3 - target) < durations.EPSILON:
            candidates.add(members)
    # ponytail: 한 묶음으로 유일하게 설명될 때만 보완한다.
    # 여러 묶음이 생략된 악보는 조합을 추측하지 않고 duration_mismatch로 남긴다.
    return list(next(iter(candidates))) if len(candidates) == 1 else []
