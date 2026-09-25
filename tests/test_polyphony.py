"""두 성부의 공통 시간축·표기 배정·셋잇단 범위 재현 사례."""

import guitarpro as gp

from tab_pdf import extract, geometry, smufl

from test_music_fixes import SYSTEM, glyph, measure, round_trip, song_for

TUPLET = chr(0xE883)
WHOLE_REST = chr(0xE4E3)
QUARTER_REST = chr(0xE4E5)
FLAG_8TH_DOWN = chr(0xE241)
ACCENT = next(chr(c) for c in range(0xE4A0, 0xE4C0)
              if smufl.name(chr(c)) == "articAccentAbove")
BAR = 3840


def up_stems(xs):
    return [geometry.VLine(x + 2.5, 135, 151) for x in xs]


def down_stems(xs, y0=202):
    return [geometry.VLine(x + 2.5, y0, 225) for x in xs]


def starts(voice):
    """(마디 안 시작 tick, 길이 값, 쉼표 여부, 프렛들)."""
    origin = voice.measure.start
    return [(b.start - origin, b.duration.value, b.status == gp.models.BeatStatus.rest,
             [n.value for n in b.notes]) for b in voice.beats]


def voice_total(voice):
    return sum(b.duration.time for b in voice.beats)


def test_sparse_lower_voice_keeps_its_onsets(tmp_path):
    """쉼표를 생략한 아랫성부 8분 두 개(1박·3박)가 1박·1.5박으로 당겨지면 안 된다."""
    xs = (60., 150., 240., 330.)
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 160, "3") for x in xs],
                glyph(60, 200, "0"), glyph(240, 200, "0"),
                glyph(62.5, 225, FLAG_8TH_DOWN), glyph(242.5, 225, FLAG_8TH_DOWN)],
        vlines=[*up_stems(xs), *down_stems((60., 240.))])
    result, warn = measure(geo)
    upper, lower = round_trip(song_for([result]), tmp_path).tracks[0].measures[0].voices
    sounding = [(start, value) for start, value, rest, _ in starts(lower) if not rest]
    assert sounding == [(0, 8), (1920, 8)]
    assert voice_total(lower) == voice_total(upper) == BAR
    assert not warn.items


def onset_at(result, voice, x):
    """IR 에서 이 성부의 x 음이 시작하는 박 위치."""
    elapsed = 0.0
    for beat in (b for b in result["beats"] if b.get("voice", 0) == voice):
        if beat["x"] == x and beat["notes"]:
            return elapsed
        length = 4 / beat["duration"] * (1.5 if beat["dotted"] else 1)
        elapsed += length * (2 / 3 if beat["tuplet"] else 1)
    raise AssertionError(f"voice {voice} 에 x={x} 음이 없다")


def test_shared_x_is_the_same_instant_in_both_voices(tmp_path):
    """두 성부가 같은 x 에 둔 음은 같은 시각에 시작한다."""
    upper_xs = (60., 170., 280., 330.)
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 160, "3") for x in upper_xs],
                glyph(60, 200, "0"), glyph(280, 200, "2")],
        vlines=[*up_stems(upper_xs), *down_stems((60., 280.))])
    result, warn = measure(geo)
    assert onset_at(result, 0, 280.) == onset_at(result, 1, 280.)
    upper, lower = round_trip(song_for([result]), tmp_path).tracks[0].measures[0].voices
    assert voice_total(lower) == voice_total(upper) == BAR
    assert not warn.items


def _two_measure_system(first_glyphs, first_vlines=()):
    """첫 마디(40..400)는 주어진 표기, 둘째 마디(400..760)는 4분 4개 단성부."""
    return geometry.PageGeometry(
        glyphs=[*first_glyphs, *[glyph(x, 176, "5") for x in (420., 510., 600., 690.)]],
        vlines=[*first_vlines, *[geometry.VLine(x, 100., 200.) for x in (40., 400., 760.)]])


def test_polyphony_in_one_measure_does_not_leak_into_the_next():
    geo = _two_measure_system(
        [*[glyph(x, 160, "3") for x in (60., 150., 240., 330.)], glyph(60, 184, QUARTER_REST)])
    warn = extract._Warnings()
    second = extract._build_measure(geo, SYSTEM, (400., 760.), 1, [], warn, (4, 4),
                                    extract.build_letter_index(geo), [])
    assert {b.get("voice", 0) for b in second["beats"]} == {0}
    assert "_fit" in second and "voices" not in second["_fit"]
    assert not warn.items


def test_marks_belong_to_one_voice_only():
    """코드명·악센트는 가까운 음의 성부 한 곳에만 붙는다."""
    xs = (60., 150., 240., 330.)
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 160, "3") for x in xs],
                glyph(60, 192, QUARTER_REST), glyph(150, 200, "0"),
                glyph(330, 192, QUARTER_REST), glyph(152, 155, ACCENT)],
        vlines=[*up_stems(xs), *down_stems((150.,))])
    warn = extract._Warnings()
    result = extract._build_measure(geo, SYSTEM, (40., 400.), 0, [(145., "G")], warn,
                                    (4, 4), extract.build_letter_index(geo), [])
    chorded = [b for b in result["beats"] if b["chord"]]
    accented = [b for b in result["beats"]
                if any(t["kind"] == "accent" for t in b["techniques"])]
    assert [(b["voice"], b["chord"]) for b in chorded] == [(0, "G")]
    assert [b["voice"] for b in accented] == [0]


def test_triplet_mark_of_one_voice_does_not_bend_the_other(tmp_path):
    upper_xs = (60., 150., 240., 330.)
    lower_xs = (60., 90., 120.)
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 160, "3") for x in upper_xs],
                *[glyph(x, 200, "0") for x in lower_xs],
                glyph(90, 222, TUPLET)],
        vlines=[*up_stems(upper_xs), *down_stems(lower_xs)])
    result, warn = measure(geo)
    upper, lower = round_trip(song_for([result]), tmp_path).tracks[0].measures[0].voices
    assert [(b.duration.value, b.duration.tuplet.enters) for b in upper.beats] == [(4, 1)] * 4
    assert [b.duration.tuplet.enters for b in lower.beats if b.notes] == [3, 3, 3]
    assert voice_total(lower) == BAR
    assert not warn.items


def test_two_event_triplet_does_not_swallow_the_next_note(tmp_path):
    """3/4: [4분 셋잇단 + 8분 셋잇단] 4분 4분. '3' 표기 옆 세 이벤트를 강제로 묶지 않는다."""
    beat = 80.
    xs = (60., 60. + beat * 2 / 3, 60. + beat, 60. + beat * 2)
    geo = geometry.PageGeometry(glyphs=[*[glyph(x, 176, "0") for x in xs],
                                        glyph(60 + beat / 2, 200, TUPLET)])
    result, warn = measure(geo, bounds=(40., 60. + beat * 3), time_sig=(3, 4))
    assert [(b["duration"], b["tuplet"]) for b in result["beats"]] == [
        (4, [3, 2]), (8, [3, 2]), (4, None), (4, None)]
    assert not warn.items


def test_two_voice_pickup_is_shortened_not_padded(tmp_path):
    """두 성부 모두 8분 두 개인 1박짜리 첫 마디는 1/4 로 줄어야 한다."""
    measures, warnings = [], []
    width = 200.
    for index in range(4):
        left = 40. + index * width
        if index == 0:
            right = left + width / 4
            xs = (left + 5, left + 5 + width / 8)
            geo = geometry.PageGeometry(
                glyphs=[*[glyph(x, 160, "3") for x in xs], *[glyph(x, 200, "0") for x in xs]],
                vlines=[*[geometry.VLine(x + 2.5, 135, 151) for x in xs],
                        *[geometry.VLine(x + 2.5, 202, 225) for x in xs]],
                beams=[geometry.Beam(xs[0] + 2.5, 135, xs[1] + 2.5, 135),
                       geometry.Beam(xs[0] + 2.5, 225, xs[1] + 2.5, 225)])
        else:
            right = left + width
            geo = geometry.PageGeometry(
                glyphs=[glyph(left + x, 176, "0") for x in (5, 55, 105, 155)])
        result, warn = measure(geo, bounds=(left, right), index=index)
        measures.append(result)
        warnings.extend(warn.items)
    warn = extract._Warnings(warnings)
    extract._adjust_boundary_measures(measures, warn)
    assert measures[0]["time_sig"] == [1, 4]
    assert [b["duration"] for b in measures[0]["beats"]] == [8, 8, 8, 8]
    assert not any(b["rest"] for b in measures[0]["beats"] if "rest" in b)
    assert [w["kind"] for w in warn.items] == ["pickup_measure"]


def test_centered_whole_rest_is_its_own_voice(tmp_path):
    """다른 x 의 온쉼표는 연주 성부 중간의 쉼표가 아니라 다른 성부의 마디 전체 쉼이다."""
    xs = (60., 150., 240., 330.)
    geo = geometry.PageGeometry(glyphs=[*[glyph(x, 160, "3") for x in xs],
                                        glyph(200, 192, WHOLE_REST)])
    result, warn = measure(geo)
    upper, lower = round_trip(song_for([result]), tmp_path).tracks[0].measures[0].voices
    assert [b.duration.value for b in upper.beats] == [4] * 4
    assert all(b.status == gp.models.BeatStatus.rest for b in lower.beats)
    assert voice_total(lower) == BAR
    assert not warn.items


def test_whole_bar_rest_fills_seven_eight_exactly(tmp_path):
    result, warn = measure(geometry.PageGeometry(glyphs=[glyph(200, 184, WHOLE_REST)]),
                           time_sig=(7, 8))
    song = round_trip(song_for([result]), tmp_path)
    beats = song.tracks[0].measures[0].voices[0].beats
    assert all(b.status == gp.models.BeatStatus.rest for b in beats)
    assert sum(b.duration.time for b in beats) == BAR * 7 // 8
    assert not warn.items


# --- 2라운드 교차 리뷰 재현 사례 ---

FLAG_8TH_UP = chr(0xE240)


def test_flagged_triplet_pins_become_triplets_in_the_shared_timeline(tmp_path):
    """윗성부 4분+점2분, 아랫성부 꼬리 달린 8분 셋잇단 셋 + 생략된 쉼표."""
    geo = geometry.PageGeometry(
        glyphs=[glyph(60, 160, "3"), glyph(150, 160, "5"),
                *[glyph(x, 200, "0") for x in (60., 90., 120.)],
                *[glyph(x + 2.5, 225, FLAG_8TH_DOWN) for x in (60., 90., 120.)],
                glyph(90, 222, TUPLET)],
        vlines=[*up_stems((60., 150.)), *down_stems((60., 90., 120.))])
    result, warn = measure(geo)
    upper, lower = round_trip(song_for([result]), tmp_path).tracks[0].measures[0].voices
    assert [b.duration.tuplet.enters for b in lower.beats if b.notes] == [3, 3, 3]
    assert voice_total(lower) == voice_total(upper) == BAR
    assert not warn.items


def test_last_pinned_note_does_not_pin_the_final_shared_segment(tmp_path):
    """아랫성부 마지막 8분 뒤의 생략 쉼표를 윗성부 리듬 변경으로 메우면 안 된다."""
    upper_xs = (60., 150., 240., 330.)
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 160, "3") for x in upper_xs],
                *[glyph(x, 200, "0") for x in (60., 330.)],
                *[glyph(x + 2.5, 225, FLAG_8TH_DOWN) for x in (60., 330.)]],
        vlines=[*up_stems(upper_xs), *down_stems((60., 330.))])
    result, warn = measure(geo, bounds=(40., 420.))
    assert [b["duration"] for b in result["beats"]
            if b["voice"] == 0 and b["notes"]] == [4, 4, 4, 4]
    assert onset_at(result, 1, 330.) == 3.0
    assert not warn.items


def _pickup_run(first_geo, first_width):
    measures, warnings = [], []
    left = 40.
    for index, width in enumerate((first_width, 200., 200.)):
        geo = first_geo if index == 0 else geometry.PageGeometry(
            glyphs=[glyph(left + x, 176, "0") for x in (5, 55, 105, 155)])
        result, warn = measure(geo, bounds=(left, left + width), index=index)
        measures.append(result)
        warnings.extend(warn.items)
        left += width
    warn = extract._Warnings(warnings)
    extract._adjust_boundary_measures(measures, warn)
    return measures, warn


def test_voice_completed_by_multi_segment_rest_pins_is_not_a_pickup():
    """아랫성부 2분쉼표 둘이 4박을 채운 마디는 폭이 좁아도 줄이지 않는다."""
    xs = (45., 65., 85., 105.)
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 160, "3") for x in xs],
                glyph(45, 192, chr(0xE4E4)), glyph(85, 192, chr(0xE4E4))],
        vlines=up_stems(xs))
    measures, warn = _pickup_run(geo, 100.)
    assert measures[0]["time_sig"] == [4, 4]
    assert not warn.items


def test_beamed_triplet_with_a_middle_rest_stays_a_triplet(tmp_path):
    """8분-8분쉼표-8분 셋잇단(빔은 양 끝 음만 잇는다) 뒤 4분 셋."""
    xs = (60., 85., 110.)
    geo = geometry.PageGeometry(
        glyphs=[glyph(60, 176, "0"), glyph(85, 184, chr(0xE4E6)), glyph(110, 176, "0"),
                *[glyph(x, 176, "0") for x in (150., 240., 330.)], glyph(85, 222, TUPLET)],
        vlines=[geometry.VLine(x + 2.5, 179, 225) for x in (60., 110.)],
        beams=[geometry.Beam(62.5, 225, 112.5, 225)])
    result, warn = measure(geo)
    assert [(b["duration"], b["tuplet"]) for b in result["beats"]] == [
        (8, [3, 2]), (8, [3, 2]), (8, [3, 2]), (4, None), (4, None), (4, None)]
    assert not warn.items


def test_explicit_triplet_mark_must_produce_a_triplet(tmp_path):
    """꼬리 달린 8분 셋과 '3' — 균등 간격이라도 셋잇단 표기를 버리면 안 된다."""
    xs = (60., 120., 180., 240., 300., 360.)
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 176, "0") for x in xs],
                *[glyph(x + 2.5, 225, FLAG_8TH_DOWN) for x in xs[:3]],
                glyph(120, 222, TUPLET)],
        vlines=[geometry.VLine(x + 2.5, 179, 225) for x in xs[:3]])
    result, warn = measure(geo)
    assert [b["tuplet"] for b in result["beats"][:3]] == [[3, 2]] * 3
    total = sum(4 / b["duration"] * (1.5 if b["dotted"] else 1) * (2 / 3 if b["tuplet"] else 1)
                for b in result["beats"])
    assert abs(total - 4.0) < 1e-9 or warn.items


def test_triplet_mark_follows_its_beam_not_the_nearest_note(tmp_path):
    lower_xs = (60., 90., 120.)
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 200, "0") for x in lower_xs],
                glyph(60, 160, "3"), glyph(150, 160, "3"), glyph(90, 159, TUPLET)],
        vlines=[*[geometry.VLine(x + 2.5, 167, 197) for x in lower_xs],
                *[geometry.VLine(x + 2.5, 163, 180) for x in (60., 150.)]],
        beams=[geometry.Beam(62.5, 167, 122.5, 167)])
    result, warn = measure(geo)
    lower_voice = next(b["voice"] for b in result["beats"] if b["notes"]
                       and b["notes"][0]["string"] == 6)
    triplets = [b for b in result["beats"] if b["tuplet"]]
    assert {b["voice"] for b in triplets} == {lower_voice}
    assert len(triplets) == 3


def test_chord_lands_on_the_voice_that_plays_there():
    """윗성부가 쉬는 2박에 놓인 G 는 아랫성부 음에 붙는다."""
    geo = geometry.PageGeometry(
        glyphs=[glyph(60, 160, "3"), glyph(150, 160, QUARTER_REST), glyph(240, 160, "3"),
                glyph(330, 160, "3"), glyph(60, 200, "0"), glyph(150, 200, "2")],
        vlines=[*up_stems((60., 240., 330.)), *down_stems((60., 150.))])
    warn = extract._Warnings()
    result = extract._build_measure(geo, SYSTEM, (40., 400.), 0, [(145., "G")], warn,
                                    (4, 4), extract.build_letter_index(geo), [])
    chorded = [b for b in result["beats"] if b["chord"]]
    assert [(b["x"], b["voice"]) for b in chorded] == [(150.0, 1)]


def test_grace_before_barline_reaches_the_next_measures_lower_voice():
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 160, "3") for x in (60., 150., 240., 330.)],
                glyph(200, 192, WHOLE_REST), glyph(390, 200, "2", size=7.0),
                glyph(420, 160, "3"), glyph(420, 200, "3")],
        vlines=[*up_stems((60., 150., 240., 330., 420.)), *down_stems((420.,)),
                *[geometry.VLine(x, 100., 200.) for x in (40., 400., 760.)]])
    warn = extract._Warnings()
    letters = extract.build_letter_index(geo)
    first = extract._build_measure(geo, SYSTEM, (40., 400.), 0, [], warn, (4, 4), letters, [])
    second = extract._build_measure(geo, SYSTEM, (400., 760.), 1, [], warn, (4, 4), letters,
                                    [], pending_graces=first["pending_graces"])
    low = next(n for b in second["beats"] for n in b["notes"] if n["string"] == 6)
    assert low.get("grace_fret") == 2
    assert not [w for w in warn.items if w["kind"] == "grace_dropped"]


def test_tie_survives_a_polyphonic_to_monophonic_barline(tmp_path):
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 160, "3") for x in (60., 150., 240., 330.)],
                glyph(60, 200, "5"), glyph(420, 200, "5")],
        vlines=[*up_stems((60., 150., 240., 330.)), *down_stems((60.,)),
                *[geometry.VLine(x, 100., 200.) for x in (40., 400., 760.)]],
        curves=[geometry.Curve(63., 203., 419., 203., False)])
    warn = extract._Warnings()
    letters = extract.build_letter_index(geo)
    measures = [extract._build_measure(geo, SYSTEM, bounds, i, [], warn, (4, 4), letters, [])
                for i, bounds in enumerate(((40., 400.), (400., 760.)))]
    extract._apply_tie_curves(geo, SYSTEM, measures, warn)
    assert measures[1]["beats"][0]["notes"][0].get("tie") is True
