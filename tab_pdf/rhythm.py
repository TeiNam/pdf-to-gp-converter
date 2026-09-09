"""타브의 기둥·빔·꼬리·붙임점에서 확정할 수 있는 음가를 읽는다."""

from . import bands, durations, smufl

# 프렛 숫자는 선을 가리지 않게 기둥에서 조금 떨어져 조판된다.
STEM_GAP = 11.0
STEM_MARGIN = 35.0
DOT_WINDOW = 10.0
DOT_Y_TOLERANCE = 4.0


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
    for stem in stems_for(geo, system, glyph):
        end_y = max((stem.y0, stem.y1), key=lambda y: abs(y - glyph.y))
        ys = sorted(beam.y_at(stem.x) for beam in geo.beams
                    if beam.x0 - 0.5 <= stem.x <= beam.x1 + 0.5
                    and stem.y0 - 2 <= beam.y_at(stem.x) <= stem.y1 + 2
                    and abs(beam.y_at(stem.x) - end_y) <= 14.0)
        # 같은 빔을 외곽선과 면으로 중복 수집했어도 한 줄로 센다.
        levels = []
        for y in ys:
            if not levels or y - levels[-1] > 1.0:
                levels.append(y)
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


def voice_assignments(geo, system, notes, rests, tolerance=2.0):
    """동시 음/쉼표 또는 반대 방향 기둥이 있으면 두 성부로 나눈다.

    한 성부의 기둥도 위아래로 바뀐다. 방향만으로 다성부라고 판정하지 않고,
    같은 시각의 충돌이 있을 때만 위 기둥=0, 아래 기둥=1을 사용한다.
    """
    directions = {}
    for note in notes:
        stems = stems_for(geo, system, note)
        if stems:
            stem = min(stems, key=lambda s: (
                min(abs(s.y0 - note.y), abs(s.y1 - note.y)),
                abs(s.x - (note.x + note.x_end) / 2)))
            end = max((stem.y0, stem.y1), key=lambda y: abs(y - note.y))
            directions[note] = int(end > note.y)
    groups = []
    for note in sorted(notes, key=lambda g: g.x):
        if groups and note.x - groups[-1][0].x <= tolerance:
            groups[-1].append(note)
        else:
            groups.append([note])
    polyphonic = any(
        {directions[g] for g in group if g in directions} == {0, 1}
        for group in groups)
    polyphonic |= any(abs(rest.x - note.x) <= tolerance
                      for rest in rests for note in notes)
    if not polyphonic:
        return None
    assignments = {}
    for group in groups:
        known = [g for g in group if g in directions]
        for note in group:
            # 화음의 기둥은 구성음 하나에만 닿을 수 있다.
            reference = min(known, key=lambda g: abs(g.y - note.y)) if known else None
            assignments[note] = directions.get(note, directions.get(reference, 0))
    for rest in rests:
        occupied = {assignments[n] for n in notes if abs(n.x - rest.x) <= tolerance}
        assignments[rest] = (1 - next(iter(occupied)) if len(occupied) == 1
                             else int(rest.y >= (system.tab_ys[0] + system.tab_ys[-1]) / 2))
    return assignments


def beam_members(geo, system, beam, beat_xs, notes, tolerance=2.0):
    """이 빔에 연결된 기둥의 beat 인덱스."""
    return {index for index, x in enumerate(beat_xs)
            for note in notes if abs(note.x - x) <= tolerance
            for stem in stems_for(geo, system, note)
            if beam.x0 - 0.5 <= stem.x <= beam.x1 + 0.5
            and stem.y0 - 2 <= beam.y_at(stem.x) <= stem.y1 + 2}


def triplet_groups(geo, system, beat_xs, notes, bounds, fret_glyphs=()):
    """'3'의 위치와 빔으로 묶음을 정한다. 마디 전체에 셋잇단을 허용하지 않는다."""
    if len(beat_xs) < 3:
        return []
    result = []
    for mark in geo.glyphs:
        if not bounds[0] <= mark.x < bounds[1]:
            continue
        explicit = mark.char == chr(0xE883) and bands.in_tab_band(mark, system)
        text = (mark.char == "3" and mark not in notes and mark not in fret_glyphs
                and system.tab_ys[0] - STEM_MARGIN <= mark.y
                <= system.tab_ys[-1] + STEM_MARGIN)
        if not explicit and not text:
            continue
        candidates = []
        for beam in geo.beams:
            if not (beam.x0 - 3 <= mark.x <= beam.x1 + 3
                    and abs(beam.y_at(mark.x) - mark.y) <= 12):
                continue
            members = beam_members(geo, system, beam, beat_xs, notes)
            if len(members) == 3 and max(members) - min(members) == 2:
                candidates.append((abs(beam.y_at(mark.x) - mark.y), sorted(members)))
        if candidates:
            result.append(min(candidates)[1])
        elif explicit:
            # 쉼표가 끼면 빔이 끊긴다. 표기 숫자가 가운데 놓인 세 이벤트를 묶는다.
            start = min(range(len(beat_xs) - 2),
                        key=lambda i: abs((beat_xs[i] + beat_xs[i + 2]) / 2 - mark.x))
            result.append(list(range(start, start + 3)))
    return result


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
