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
    result, _ = measure(geo)
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


def test_tie_across_voices_is_reported_not_faked(tmp_path):
    """GP5 타이는 같은 성부의 앞 음을 잇는다 — 성부를 건너면 프렛이 0 으로 바뀐다.

    아랫성부(두 성부 마디) → 단성부(첫 성부) 로 이어지는 호는 타이로 걸지 않고
    경고한다. 다시 치는 편이 엉뚱한 음으로 이어지는 것보다 낫다.
    """
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
    song = round_trip(song_for(measures), tmp_path)
    note = song.tracks[0].measures[1].voices[0].beats[0].notes[0]
    assert (note.value, note.type) == (5, gp.models.NoteType.normal)
    assert [w["kind"] for w in warn.items] == ["tie_across_voices"]


def test_other_voice_triplets_do_not_shorten_plain_eighths(tmp_path):
    """1/4: 윗성부 꼬리 달린 8분 둘, 아랫성부 빔 묶인 16분 셋잇단 여섯."""
    lower = (45., 65., 85., 105., 125., 145.)
    upper = (45., 105.)
    beams = [geometry.Beam(x0 + 2.5, y, x1 + 2.5, y)
             for x0, x1 in ((45., 85.), (105., 145.)) for y in (225., 221.2)]
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 200, "0") for x in lower], *[glyph(x, 160, "3") for x in upper],
                *[glyph(x + 2.5, 135, FLAG_8TH_UP) for x in upper],
                glyph(65, 232, TUPLET), glyph(125, 232, TUPLET)],
        vlines=[*down_stems(lower), *up_stems(upper)], beams=beams)
    result, warn = measure(geo, bounds=(40., 160.), time_sig=(1, 4))
    upper_voice, lower_voice = round_trip(song_for([result]), tmp_path).tracks[0].measures[0].voices
    assert [b.duration.time for b in upper_voice.beats] == [480, 480]
    assert [b.duration.tuplet.enters for b in lower_voice.beats] == [3] * 6
    assert voice_total(lower_voice) == 960
    assert not warn.items


def test_rest_inside_a_triplet_window_is_a_triplet_rest():
    """아랫성부 [8분쉼표, 꼬리 8분, 8분] + '3' — 두 음은 1/3·2/3 박에 시작한다."""
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 160, "3") for x in (60., 150., 240., 330.)],
                glyph(60, 192, chr(0xE4E6)), glyph(90, 200, "0"), glyph(120, 200, "2"),
                glyph(92.5, 225, FLAG_8TH_DOWN), glyph(90, 222, TUPLET)],
        vlines=[*up_stems((60., 150., 240., 330.)), *down_stems((90., 120.))])
    result, warn = measure(geo)
    assert abs(onset_at(result, 1, 90.) - 1 / 3) < 1e-9
    assert abs(onset_at(result, 1, 120.) - 2 / 3) < 1e-9
    assert not warn.items


def test_triplet_filled_measure_is_not_shortened_as_a_pickup():
    xs = [45. + 10 * i for i in range(9)]
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 176, "0") for x in xs],
                *[glyph(x + 2.5, 225, FLAG_8TH_DOWN) for x in xs], glyph(55, 222, TUPLET)],
        vlines=[geometry.VLine(x + 2.5, 179, 225) for x in xs])
    measures, warn = _pickup_run(geo, 100.)
    assert measures[0]["time_sig"] == [4, 4]
    assert not warn.items


def test_unanchored_rest_still_constrains_the_shared_timeline(tmp_path):
    """아랫성부 [8분, 2분쉼표, 8분] — 쉼표 양 끝 시각을 몰라도 2박 제약은 지킨다."""
    upper_xs = (60., 210., 250., 290.)
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 160, "3") for x in upper_xs],
                glyph(60, 200, "0"), glyph(210, 192, chr(0xE4E4)), glyph(290, 200, "2"),
                *[glyph(x + 2.5, 225, FLAG_8TH_DOWN) for x in (60., 290.)]],
        vlines=[*up_stems(upper_xs), *down_stems((60., 290.))])
    result, warn = measure(geo)
    assert onset_at(result, 0, 290.) == onset_at(result, 1, 290.)
    upper, lower = round_trip(song_for([result]), tmp_path).tracks[0].measures[0].voices
    assert voice_total(upper) == voice_total(lower) == BAR
    assert not warn.items


def test_last_rest_length_is_kept_exactly(tmp_path):
    lower_xs = (60., 110., 160., 210., 260., 310., 360.)
    geo = geometry.PageGeometry(
        glyphs=[glyph(60, 160, "3"), *[glyph(x, 200, "0") for x in lower_xs],
                *[glyph(x + 2.5, 225, FLAG_8TH_DOWN) for x in lower_xs],
                glyph(390, 192, chr(0xE4E6))],
        vlines=[*up_stems((60.,)), *down_stems(lower_xs)])
    result, warn = measure(geo)
    _, lower = round_trip(song_for([result]), tmp_path).tracks[0].measures[0].voices
    assert voice_total(lower) == BAR
    assert not warn.items


def test_triplet_mark_of_a_rest_only_voice_stays_there(tmp_path):
    geo = geometry.PageGeometry(
        glyphs=[glyph(60, 160, "3"), *[glyph(x, 192, chr(0xE4E6)) for x in (60., 90., 120.)],
                glyph(90, 206, TUPLET)],
        vlines=up_stems((60.,)))
    result, warn = measure(geo, bounds=(40., 160.), time_sig=(1, 4))
    upper, lower = round_trip(song_for([result]), tmp_path).tracks[0].measures[0].voices
    assert [b.duration.tuplet.enters for b in lower.beats] == [3, 3, 3]
    assert voice_total(lower) == voice_total(upper) == 960
    assert not warn.items


# --- 4라운드 교차 리뷰 재현 사례 ---

def _flag(x, value, down=True):
    """x 기둥 끝의 꼬리 글리프 (8분=1 … 64분=4)."""
    code = 0xE240 + (value - 1) * 2 + (1 if down else 0)
    return glyph(x + 2.5, 225 if down else 135, chr(code))


def test_rest_floor_on_a_triplet_segment_does_not_crash():
    """2/4: 윗 [8분, 점4분쉼표], 아래 빔 묶인 [16·8·점8] 셋잇단 + 생략 쉼표."""
    lower = (60., 80., 120.)
    geo = geometry.PageGeometry(
        glyphs=[glyph(60, 160, "3"), glyph(120, 152, chr(0xE4E5)),
                glyph(130, 150, chr(0xE1E7)),
                *[glyph(x, 200, "0") for x in lower], glyph(80, 232, TUPLET),
                _flag(60, 1, down=False)],
        vlines=[*up_stems((60.,)), *down_stems(lower)],
        beams=[geometry.Beam(62.5, 225, 122.5, 225)])
    result, _ = measure(geo, bounds=(40., 280.), time_sig=(2, 4))
    assert result["beats"]


def test_whole_rest_in_six_four_is_a_four_beat_rest_not_a_voice():
    xs = (300., 330., 360., 390.)
    geo = geometry.PageGeometry(
        glyphs=[glyph(60, 184, WHOLE_REST), *[glyph(x, 176, "0") for x in xs]],
        vlines=[geometry.VLine(x + 2.5, 179, 225) for x in xs],
        beams=[geometry.Beam(302.5, 225, 392.5, 225)])
    result, warn = measure(geo, bounds=(40., 420.), time_sig=(6, 4))
    assert {b.get("voice", 0) for b in result["beats"]} == {0}
    assert [b["duration"] for b in result["beats"]] == [1, 8, 8, 8, 8]
    assert not warn.items


def test_overlapping_rest_spans_are_solved_together(tmp_path):
    """2/4: 윗 [4분쉼표@60, 음@160], 아래 [8분@60, 4분쉼표@140, 8분@240]."""
    geo = geometry.PageGeometry(
        glyphs=[glyph(60, 152, chr(0xE4E5)), glyph(160, 160, "3"),
                glyph(60, 200, "0"), glyph(140, 192, chr(0xE4E5)), glyph(240, 200, "2"),
                _flag(60, 1), _flag(240, 1)],
        vlines=[*up_stems((160.,)), *down_stems((60., 240.))])
    result, warn = measure(geo, bounds=(40., 300.), time_sig=(2, 4))
    upper, lower = round_trip(song_for([result]), tmp_path).tracks[0].measures[0].voices
    assert voice_total(upper) == voice_total(lower) == 1920
    assert not warn.items


def test_shared_gap_may_be_a_sum_of_two_lengths(tmp_path):
    """1/4: 윗 [점8, 16], 아래 [32, 점8, 32] — 공통 간격 0.625박이 생긴다."""
    beat = 160.
    def at(q):
        return 60. + beat * q
    upper = (at(0), at(0.75))
    lower = (at(0), at(0.125), at(0.875))
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 160, "3") for x in upper], *[glyph(x, 200, "0") for x in lower],
                _flag(upper[0], 1, down=False), _flag(upper[1], 2, down=False),
                glyph(upper[0] + 6, 160, chr(0xE1E7)),
                _flag(lower[0], 3), _flag(lower[1], 1), _flag(lower[2], 3),
                glyph(lower[1] + 6, 200, chr(0xE1E7))],
        vlines=[*up_stems(upper), *down_stems(lower)])
    result, warn = measure(geo, bounds=(40., 60. + beat), time_sig=(1, 4))
    upper_voice, lower_voice = round_trip(song_for([result]), tmp_path).tracks[0].measures[0].voices
    assert [b.duration.time for b in upper_voice.beats] == [720, 240]
    assert [b.duration.time for b in lower_voice.beats] == [120, 720, 120]
    assert not warn.items


def test_many_triplet_windows_find_the_exact_reading():
    """4/4: 꼬리 16분 24개, 세 음마다 '3' — 창 조합이 폭발해도 정답을 찾는다."""
    xs = [45. + 14 * i for i in range(24)]
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 176, "0") for x in xs], *[_flag(x, 2) for x in xs],
                *[glyph(xs[i + 1], 232, TUPLET) for i in range(0, 24, 3)]],
        vlines=[geometry.VLine(x + 2.5, 179, 225) for x in xs])
    result, warn = measure(geo, bounds=(40., 45. + 14 * 24))
    assert all(b["tuplet"] == [3, 2] for b in result["beats"])
    assert not warn.items


def test_tie_after_a_two_voice_pickup_refit():
    """두 성부 픽업(8분 둘씩)의 윗성부 같은 프렛 타이.

    타이는 박자표 재맞춤 전에 걸린다 — 그때 4/4 로 채운 쉼표가 두 음 사이에 끼면
    인접하지 않은 것으로 보여 타이가 사라졌다.
    """
    xs = (45., 70.)
    first = geometry.PageGeometry(
        glyphs=[*[glyph(x, 160, "3") for x in xs], *[glyph(x, 200, "0") for x in xs]],
        vlines=[*up_stems(xs), *down_stems(xs)],
        beams=[geometry.Beam(47.5, 135, 72.5, 135), geometry.Beam(47.5, 225, 72.5, 225)],
        curves=[geometry.Curve(48., 157., 69., 157., True)])
    measures, warnings, left = [], [], 40.
    for index, width in enumerate((50., 200., 200.)):
        geo = first if index == 0 else geometry.PageGeometry(
            glyphs=[glyph(left + x, 176, "0") for x in (5, 55, 105, 155)])
        result, warn = measure(geo, bounds=(left, left + width), index=index)
        if index == 0:
            extract._apply_tie_curves(geo, SYSTEM, [result], warn)
        measures.append(result)
        warnings.extend(warn.items)
        left += width
    extract._adjust_boundary_measures(measures, extract._Warnings(warnings))
    upper = [b for b in measures[0]["beats"] if b["voice"] == 0 and b["notes"]]
    assert measures[0]["time_sig"] == [1, 4]
    assert upper[1]["notes"][0].get("tie") is True


def test_accent_is_not_given_to_the_rest_voice():
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 160, "3") for x in (60., 150., 240., 330.)],
                glyph(60, 184, QUARTER_REST), glyph(61, 175, chr(0xE4A1))])
    result, _ = measure(geo)
    accented = [b for b in result["beats"]
                if any(t["kind"] == "accent" for t in b["techniques"])]
    assert [(b["x"], b["voice"], bool(b["notes"])) for b in accented] == [(60.0, 0, True)]


# --- 5라운드 교차 리뷰 재현 사례 ---

def _pinned_voice(events, y, down):
    """(x, 박 길이) 목록을 꼬리·점으로 고정한 음으로 그린다."""
    glyphs, stems = [], []
    flags = {0.125: (3, False), 0.25: (2, False), 0.5: (1, False),
             0.75: (1, True), 0.375: (2, True), 0.1875: (3, True),
             0.0625: (4, False), 0.09375: (4, True)}
    for x, length in events:
        count, dotted = flags[length]
        glyphs.append(glyph(x, y, "0"))
        glyphs.append(_flag(x, count, down=down))
        if dotted:
            glyphs.append(glyph(x + 6, y, chr(0xE1E7)))
    stems = down_stems([x for x, _ in events]) if down else up_stems([x for x, _ in events])
    return glyphs, stems


def test_multi_segment_note_pins_are_lower_bounds_on_the_timeline(tmp_path):
    upper_g, upper_s = _pinned_voice([(60, .75), (160, .125), (200, .125)], 160, False)
    lower_g, lower_s = _pinned_voice([(60, .125), (80, .75), (200, .125)], 200, True)
    geo = geometry.PageGeometry(glyphs=upper_g + lower_g, vlines=upper_s + lower_s)
    result, warn = measure(geo, bounds=(40., 220.), time_sig=(1, 4))
    assert onset_at(result, 0, 200.) == onset_at(result, 1, 200.) == 0.875
    upper, lower = round_trip(song_for([result]), tmp_path).tracks[0].measures[0].voices
    assert voice_total(upper) == voice_total(lower) == 960
    assert not warn.items


def test_shared_gap_of_fifteen_sixteenths(tmp_path):
    """2/4: 윗 [4분쉼표@60, 음@220], 아래 [64분@60, 4분쉼표@70, 점8분@230, 점32분@350]."""
    lower_g, lower_s = _pinned_voice([(230, .75), (350, .1875)], 200, True)
    geo = geometry.PageGeometry(
        glyphs=[glyph(60, 152, QUARTER_REST), glyph(220, 160, "3"),
                glyph(60, 200, "0"), _flag(60, 4), glyph(70, 192, QUARTER_REST),
                *lower_g],
        vlines=[*up_stems((220.,)), *down_stems((60.,)), *lower_s])
    result, warn = measure(geo, bounds=(40., 380.), time_sig=(2, 4))
    upper, lower = round_trip(song_for([result]), tmp_path).tracks[0].measures[0].voices
    assert voice_total(upper) == voice_total(lower) == 1920
    assert not warn.items


def test_rest_constraint_far_from_x_spacing_is_kept(tmp_path):
    """4/4: 두 성부 모두 2분쉼표@60 → 음@90 — x 비례는 1/3 박이지만 쉼표가 2박이다."""
    geo = geometry.PageGeometry(
        glyphs=[glyph(60, 152, chr(0xE4E4)), glyph(90, 160, "3"),
                glyph(60, 192, chr(0xE4E4)), glyph(90, 200, "0")],
        vlines=[*up_stems((90.,)), *down_stems((90.,))])
    result, warn = measure(geo, bounds=(40., 420.))
    upper, lower = round_trip(song_for([result]), tmp_path).tracks[0].measures[0].voices
    assert voice_total(upper) == voice_total(lower) == BAR
    assert not warn.items


def test_triplet_windows_that_must_change_together():
    from tab_pdf import durations
    plain = {8: durations.LegalDuration(8, False, .5), 16: durations.LegalDuration(16, False, .25)}
    pins = {i: plain[v] for i, v in enumerate([8, 16, 8, 8, 16, 8])}
    fitted, exact, _ = durations.fit_with_triplets(
        [1 / 3] * 6, 2.0, pins, set(), [(0, 1, 2), (3, 4, 5)])
    assert exact
    assert abs(sum(d.quarters for d in fitted) - 2.0) < durations.EPSILON


# --- 6라운드 교차 리뷰 재현 사례 ---

def test_explicit_triplets_survive_in_both_voices(tmp_path):
    """두 성부 모두 [8분 셋잇단 셋 + 8분 여섯], 각 성부에 '3' — 셋잇단을 버리지 않는다."""
    beat = 90.
    xs = [60., 90., 120.] + [150. + beat / 2 * i for i in range(6)]
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 160, "3") for x in xs], *[glyph(x, 200, "0") for x in xs],
                *[_flag(x, 1, down=False) for x in xs], *[_flag(x, 1) for x in xs],
                glyph(90, 128, TUPLET), glyph(90, 232, TUPLET)],
        vlines=[*up_stems(xs), *down_stems(xs)])
    result, warn = measure(geo, bounds=(40., 60. + beat * 4))
    upper, lower = round_trip(song_for([result]), tmp_path).tracks[0].measures[0].voices
    assert [b.duration.time for b in upper.beats][:3] == [320] * 3
    assert [b.duration.time for b in lower.beats][:3] == [320] * 3
    assert voice_total(upper) == voice_total(lower) == BAR
    assert not warn.items


def test_dotted_sixty_fourth_is_a_shared_timeline_value(tmp_path):
    upper_g, upper_s = _pinned_voice([(60, .09375), (72, .09375), (84, .0625), (92, .75)],
                                     160, False)
    lower_g, lower_s = _pinned_voice([(60, .25), (92, .75)], 200, True)
    geo = geometry.PageGeometry(glyphs=upper_g + lower_g, vlines=upper_s + lower_s)
    result, warn = measure(geo, bounds=(40., 188.), time_sig=(1, 4))
    assert onset_at(result, 0, 92.) == onset_at(result, 1, 92.) == 0.25
    upper, lower = round_trip(song_for([result]), tmp_path).tracks[0].measures[0].voices
    assert voice_total(upper) == voice_total(lower) == 960
    assert not warn.items


def test_dotted_eighth_triplet_is_half_a_beat(tmp_path):
    """1/4: [점8분, 16분, 8분] 셋잇단 — 480·160·320 tick."""
    xs = (60., 140., 166.6667)
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 176, "0") for x in xs],
                _flag(xs[0], 1), glyph(xs[0] + 6, 176, chr(0xE1E7)), _flag(xs[1], 2),
                _flag(xs[2], 1), glyph(140, 232, TUPLET)],
        vlines=[geometry.VLine(x + 2.5, 179, 225) for x in xs])
    result, warn = measure(geo, bounds=(40., 220.), time_sig=(1, 4))
    song = round_trip(song_for([result]), tmp_path)
    assert [b.duration.time for b in song.tracks[0].measures[0].voices[0].beats] == [480, 160, 320]
    assert not warn.items


def test_rest_span_needing_a_far_composite_gap(tmp_path):
    """4/4: 윗 [점2분쉼표@60, 음@90], 아래 [64분@60, 점2분쉼표@70, 점8분@100, 점32분@350]."""
    lower_g, lower_s = _pinned_voice([(100, .75), (350, .1875)], 200, True)
    geo = geometry.PageGeometry(
        glyphs=[glyph(60, 152, chr(0xE4E4)), glyph(66, 152, chr(0xE1E7)), glyph(90, 160, "3"),
                glyph(60, 200, "0"), _flag(60, 4),
                glyph(70, 192, chr(0xE4E4)), glyph(76, 192, chr(0xE1E7)), *lower_g],
        vlines=[*up_stems((90.,)), *down_stems((60.,)), *lower_s])
    result, warn = measure(geo, bounds=(40., 420.))
    upper, lower = round_trip(song_for([result]), tmp_path).tracks[0].measures[0].voices
    assert voice_total(upper) == voice_total(lower) == BAR
    assert not warn.items


def test_dense_twelve_eight_two_voices_is_fast(tmp_path):
    """12/8: 윗 8분 12개, 아래 16분 + 8분 11개 + 16분 — x 는 시간에 비례."""
    import time
    per_beat = 40.
    upper = [(60. + per_beat * 0.5 * i, .5) for i in range(12)]
    lower = ([(60., .25)] + [(60. + per_beat * (0.25 + 0.5 * i), .5) for i in range(11)]
             + [(60. + per_beat * 5.75, .25)])
    upper_g, upper_s = _pinned_voice(upper, 160, False)
    lower_g, lower_s = _pinned_voice(lower, 200, True)
    geo = geometry.PageGeometry(glyphs=upper_g + lower_g, vlines=upper_s + lower_s)
    started = time.perf_counter()
    result, warn = measure(geo, bounds=(40., 60. + per_beat * 6), time_sig=(12, 8))
    assert time.perf_counter() - started < 1.0
    up, low = round_trip(song_for([result]), tmp_path).tracks[0].measures[0].voices
    assert voice_total(up) == voice_total(low) == BAR * 3 // 2
    assert not warn.items
