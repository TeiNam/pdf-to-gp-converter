"""음악적 정확성 리뷰의 재현 사례. 음가·동시성·성부를 GP5까지 검증한다."""

from pathlib import Path

import guitarpro as gp
import pymupdf
import pytest

from tab_pdf import ai, build, extract, geometry, refine

PDF = Path(__file__).resolve().parents[1] / "pdf" / "나는반딧불.pdf"
SYSTEM = geometry.System(
    (100., 105., 110., 115., 120.),
    (160., 168., 176., 184., 192., 200.),
)


def glyph(x, y, char, size=9.3, width=5.0):
    return geometry.Glyph(x, y, x + width, char, "test", size)


def measure(geo, *, bounds=(40., 400.), index=0, time_sig=(4, 4)):
    warn = extract._Warnings()
    result = extract._build_measure(
        geo, SYSTEM, bounds, index, [], warn, time_sig,
        extract.build_letter_index(geo), [])
    return result, warn


def song_for(measures, **kwargs):
    return {"title": "음악 검증", "artist": "", "tempo": 80,
            "tuning": list(extract.STANDARD_TUNING),
            "measures": measures, "warnings": [], **kwargs}


def round_trip(ir, tmp_path):
    path = tmp_path / "music.gp5"
    build.write_gp5(build.build_song(ir), str(path))
    return gp.parse(str(path), encoding=build.GP5_ENCODING)


@pytest.mark.skipif(not PDF.exists(), reason="실제 입력 PDF 없음")
def test_real_intro_beams_preserve_note_lengths(tmp_path):
    """6마디: 8분 네 개 + 4분 두 개. 꾸밈음 간격이 점음표를 만들면 안 된다."""
    ir = extract.extract_ir(str(PDF), tempo=80)
    song = round_trip(ir, tmp_path)
    beats = song.tracks[0].measures[5].voices[0].beats
    assert [(b.duration.value, b.duration.isDotted) for b in beats] == [
        (8, False), (8, False), (8, False), (8, False), (4, False), (4, False)]


def test_sloped_beams_and_dot_override_horizontal_spacing(tmp_path):
    """합성 PDF의 기울어진 빔도 읽고, 다른 staff의 빔은 섞지 않는다."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    xs = [60., 100., 150., 180., 220., 260., 300., 340.]
    for x in xs:
        page.insert_text((x, 176), "0", fontsize=9.3)
        page.draw_line((x + 2.5, 179), (x + 2.5, 220 + x / 100), width=0.5)
    # 두꺼운 비스듬한 선과 채운 평행사변형을 각각 검증한다.
    page.draw_line((62.5, 220.6), (342.5, 223.4), width=2.6)
    shape = page.new_shape()
    shape.draw_polyline([(102.5, 217), (109, 217.065),
                         (109, 219.665), (102.5, 219.6), (102.5, 217)])
    shape.finish(fill=(0, 0, 0), color=None)
    shape.commit()
    page.draw_line((60, 130), (342.5, 130), width=2.6)
    # 지우기용 흰 선은 기둥과 겹쳐도 빔이 아니다.
    page.draw_line((62.5, 214.6), (342.5, 217.4), width=2.6, color=(1, 1, 1))
    geo = geometry.load_page_geometry(page)
    doc.close()
    geo.glyphs.append(glyph(68, 176, chr(0xE1E7)))
    result, warn = measure(geo)
    song = round_trip(song_for([result]), tmp_path)
    beats = song.tracks[0].measures[0].voices[0].beats
    assert [(b.duration.value, b.duration.isDotted) for b in beats] == [
        (8, True), (16, False), *[(8, False)] * 6]
    assert not warn.items


def test_centered_two_digit_frets_remain_simultaneous(tmp_path):
    """중심이 같은 10프렛·7프렛 화음 네 개가 8개 이벤트로 갈리면 안 된다."""
    path = tmp_path / "centered.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for y in SYSTEM.melody_ys + SYSTEM.tab_ys:
        page.draw_line((40, y), (400, y), width=0.4)
    page.draw_line((400, 100), (400, 200), width=0.6)
    for x in (70., 150., 230., 310.):
        for string, text in ((2, "10"), (3, "7")):
            start = x - pymupdf.get_text_length(text, fontsize=9.3) / 2
            page.insert_text((start, SYSTEM.tab_ys[string - 1]), text, fontsize=9.3)
    doc.save(path)
    doc.close()
    ir = extract.extract_ir(str(path), tempo=80)
    song = round_trip(ir, tmp_path)
    beats = song.tracks[0].measures[0].voices[0].beats
    assert len(beats) == 4
    assert [b.duration.value for b in beats] == [4] * 4
    assert [[(n.string, n.value) for n in b.notes] for b in beats] == [
        [(2, 10), (3, 7)]] * 4
    assert not ir["warnings"]


@pytest.mark.parametrize("rest_index", [0, 2])
def test_centered_whole_bar_rest_is_not_a_pickup(rest_index, tmp_path):
    """첫/끝 마디 중앙의 온쉼표는 주변 마디와 똑같이 4박을 차지한다."""
    measures, warnings = [], []
    for index in range(3):
        left, right = 40. + index * 200, 240. + index * 200
        glyphs = ([glyph((left + right) / 2, 184, chr(0xE4E3))]
                  if index == rest_index else
                  [glyph(left + x, 176, "0") for x in (10, 60, 110, 160)])
        result, warn = measure(
            geometry.PageGeometry(glyphs=glyphs), bounds=(left, right), index=index)
        measures.append(result)
        warnings.extend(warn.items)
    warn = extract._Warnings(warnings)
    extract._adjust_boundary_measures(measures, warn)
    song = round_trip(song_for(measures), tmp_path)
    assert [h.length for h in song.measureHeaders] == [3840] * 3
    assert all(m["time_sig"] == [4, 4] for m in measures)
    assert not warn.items


@pytest.mark.parametrize("strings,y,above,target", [
    ((1, 6), 162., None, 1),
    ((1, 2), 162., True, 2),    # 위로 부푼 호의 음은 끝점보다 아래에 있다
    ((1, 2), 174., False, 2),
])
def test_tie_only_sustains_the_string_it_connects(strings, y, above, target, tmp_path):
    geo = geometry.PageGeometry(
        glyphs=[glyph(x, SYSTEM.tab_ys[s - 1], "3")
                for x in (60., 240.) for s in strings],
        curves=[geometry.Curve(63., y, 239., y, above)],
    )
    result, warn = measure(geo)
    extract._apply_tie_curves(geo, SYSTEM, [result], warn)
    song = round_trip(song_for([result]), tmp_path)
    notes = song.tracks[0].measures[0].voices[0].beats[1].notes
    assert {n.string for n in notes if n.type == gp.models.NoteType.tie} == {target}
    assert {n.string for n in notes if n.type == gp.models.NoteType.normal} == (
        set(strings) - {target})


@pytest.mark.parametrize("fret", ["3", "10"])
def test_other_voice_rest_does_not_erase_sounding_notes(fret, tmp_path):
    geo = geometry.PageGeometry(glyphs=[
        *[glyph(x + (i - (len(fret) - 1) / 2) * 5, 160, char)
          for x in (60., 150., 240., 330.) for i, char in enumerate(fret)],
        glyph(60, 184, chr(0xE4E5)),
    ])
    result, warn = measure(geo)
    song = round_trip(song_for([result]), tmp_path)
    upper, lower = song.tracks[0].measures[0].voices
    assert [[(n.string, n.value) for n in b.notes] for b in upper.beats] == [
        [(1, int(fret))]] * 4
    assert all(b.status == gp.models.BeatStatus.rest for b in lower.beats)
    assert [b.duration.time for b in lower.beats] == [960, 2880]
    assert not warn.items


def test_two_voices_keep_independent_durations_and_ties(tmp_path):
    xs = (60., 150., 240., 330.)
    geo = geometry.PageGeometry(
        glyphs=[*[glyph(x, 160, "3") for x in xs],
                glyph(60, 192, chr(0xE4E5)), glyph(150, 200, "0"),
                glyph(330, 192, chr(0xE4E5))],
        vlines=[*[geometry.VLine(x + 2.5, 135, 151) for x in xs],
                geometry.VLine(152.5, 202, 225)],
        curves=[geometry.Curve(63, 162, 149, 162)],
    )
    result, warn = measure(geo)
    extract._apply_tie_curves(geo, SYSTEM, [result], warn)
    song = round_trip(song_for([result]), tmp_path)
    upper, lower = song.tracks[0].measures[0].voices
    assert [b.duration.value for b in upper.beats] == [4] * 4
    assert [b.duration.value for b in lower.beats] == [4, 2, 4]
    assert lower.beats[1].startInMeasure == 960
    assert [(n.string, n.value) for n in lower.beats[1].notes] == [(6, 0)]
    assert upper.beats[1].notes[0].type == gp.models.NoteType.tie
    assert all(sum(b.duration.time for b in v.beats) == 3840 for v in (upper, lower))
    assert not warn.items


def test_triplet_rest_keeps_ratio_without_changing_later_beats(tmp_path):
    xs = (60., 90., 120., 150., 240., 330.)
    geo = geometry.PageGeometry(glyphs=[
        *[glyph(x, 176, chr(0xE4E6) if x == 90 else "0") for x in xs],
        glyph(90, 200, chr(0xE883)),
    ])
    result, warn = measure(geo)
    song = round_trip(song_for([result]), tmp_path)
    beats = song.tracks[0].measures[0].voices[0].beats
    assert [b.duration.time for b in beats] == [320, 320, 320, 960, 960, 960]
    assert beats[1].status == gp.models.BeatStatus.rest
    assert [b.duration.tuplet.enters for b in beats] == [3, 3, 3, 1, 1, 1]
    assert not warn.items


@pytest.mark.skipif(not PDF.exists(), reason="실제 입력 PDF 없음")
def test_real_triplet_uses_matching_tab_beam_and_paired_staff_mark(tmp_path):
    """8마디 마지막 16분 셋잇단은 오선의 '3'과 타브 빔이 함께 증명한다."""
    ir = extract.extract_ir(str(PDF), tempo=80)
    beats = round_trip(ir, tmp_path).tracks[0].measures[7].voices[0].beats
    assert [b.duration.value for b in beats] == [
        8, 8, 16, 16, 8, 16, 16, 8, 16, 16, 16, 8]
    assert [b.duration.tuplet.enters for b in beats] == [1] * 8 + [3] * 3 + [1]
    assert sum(b.duration.time for b in beats) == 3840
    assert not any(w["kind"] == "duration_mismatch" for w in ir["warnings"])


@pytest.mark.skipif(not PDF.exists(), reason="실제 입력 PDF 없음")
def test_real_grace_transitions_do_not_move_to_previous_regular_note(tmp_path):
    """6마디 꾸밈음 3→5 슬라이드, 7마디 꾸밈음 1→3 해머온을 GP5로 보존한다."""
    ir = extract.extract_ir(str(PDF), tempo=80)
    song = round_trip(ir, tmp_path)
    for index, fret, principal, transition in (
            (5, 3, 5, gp.models.GraceEffectTransition.slide),
            (6, 1, 3, gp.models.GraceEffectTransition.hammer)):
        beats = song.tracks[0].measures[index].voices[0].beats
        position, note = next(
            (i, n) for i, beat in enumerate(beats) for n in beat.notes
            if n.string == 2 and n.value == principal
            and n.effect.grace and n.effect.grace.fret == fret)
        assert note.effect.grace.transition == transition
        previous = next(n for n in beats[position - 1].notes if n.string == 2)
        assert not previous.effect.slides
        assert not previous.effect.hammer


@pytest.mark.parametrize("rest", [False, True])
def test_dotted_whole_fills_twelve_eight_for_notes_and_bar_rests(rest, tmp_path):
    char = chr(0xE4E3) if rest else "0"
    result, warn = measure(
        geometry.PageGeometry(glyphs=[glyph(220, 176, char)]), time_sig=(12, 8))
    extract._adjust_boundary_measures([result], warn)
    song = round_trip(song_for([result]), tmp_path)
    output = song.tracks[0].measures[0]
    assert output.header.timeSignature.numerator == 12
    assert output.header.timeSignature.denominator.value == 8
    beat, = output.voices[0].beats
    assert (beat.duration.value, beat.duration.isDotted, beat.duration.time) == (1, True, 5760)
    assert beat.status == (gp.models.BeatStatus.rest if rest else gp.models.BeatStatus.normal)
    assert not warn.items


@pytest.mark.parametrize("name, expected", [
    ("Am", gp.models.KeySignature.AMinor),
    ("Ebm", gp.models.KeySignature.EMinorFlat),
    ("A#m", gp.models.KeySignature.AMinorSharp),
    ("G♯m", gp.models.KeySignature.GMinorSharp),
    ("B♭m", gp.models.KeySignature.BMinorFlat),
])
def test_minor_key_mode_and_accidentals_survive_gp5(name, expected, tmp_path):
    result, _ = measure(geometry.PageGeometry(glyphs=[glyph(60, 176, "0")]))
    song = round_trip(song_for([result], key=name), tmp_path)
    assert song.measureHeaders[0].keySignature == expected
    assert song.measureHeaders[0].keySignature.value[1] == 1


def test_beamed_pickup_does_not_keep_the_original_duration_warning(tmp_path):
    geo = geometry.PageGeometry(
        glyphs=[glyph(x, 176, "0") for x in [60, 100, *range(160, 460, 40), *range(500, 800, 40)]],
        vlines=[geometry.VLine(x + 2.5, 179, 218)
                for x in [60, 100, *range(160, 460, 40), *range(500, 800, 40)]],
        beams=[geometry.Beam(left + 2.5, 218, right + 2.5, 218)
               for left, right in [(60, 100), (160, 440), (500, 780)]],
    )
    measures, warn = [], extract._Warnings()
    for index, bounds in enumerate([(40, 140), (140, 480), (480, 820)]):
        result, current = measure(geo, bounds=bounds, index=index)
        measures.append(result)
        warn.items.extend(current.items)
    assert any(w["kind"] == "duration_mismatch" for w in warn.items)
    extract._adjust_boundary_measures(measures, warn)
    beats = round_trip(song_for(measures), tmp_path).tracks[0].measures[0].voices[0].beats
    assert measures[0]["time_sig"] == [1, 4]
    assert [b.duration.time for b in beats] == [480, 480]
    assert not any(w["kind"] == "duration_mismatch" for w in warn.items)


def unknown_chord_ir(*names):
    """실제 추출 경로에서 경고를 만든다. AI 보이싱으로 해결할 슬래시 마디."""
    xs = [60. + i * 80 for i in range(len(names))]
    geo = geometry.PageGeometry(glyphs=[glyph(x, 176, chr(0xE100)) for x in xs])
    warn = extract._Warnings()
    result = extract._build_measure(
        geo, SYSTEM, (40., 400.), 0, list(zip(xs, names)), warn, (4, 4),
        extract.build_letter_index(geo), [])
    return song_for([result], warnings=warn.items)


def test_ai_voicing_clears_resolved_warning_and_cli_exits_successfully(tmp_path, monkeypatch):
    import convert

    ir = unknown_chord_ir("Am7")
    assert [w["kind"] for w in ir["warnings"]] == ["unknown_chord"]
    assert not ir["measures"][0]["beats"][0]["notes"]
    run_refinement = refine.refine_ir
    config = ai.Config(backend=ai.BACKEND_OPENAI, model="stub")

    def refine_stub(source, **kwargs):
        return run_refinement(
            source, config=config,
            ask=lambda *_: {"corrections": [
                {"op": "voicing", "name": "Am7", "frets": [0, 1, 0, 2, 0, -1]}]},
            **kwargs)

    monkeypatch.setattr(convert.extract, "extract_ir", lambda *_, **__: ir)
    monkeypatch.setattr(convert.refine, "refine_ir", refine_stub)
    source = tmp_path / "input.pdf"
    source.touch()
    out = tmp_path / "resolved.gp5"
    assert convert.main([str(source), "-o", str(out), "--ai"]) == 0
    beat = gp.parse(str(out), encoding=build.GP5_ENCODING).tracks[0].measures[0].voices[0].beats[0]
    assert beat.status == gp.models.BeatStatus.normal
    assert [(n.string, n.value) for n in beat.notes] == [(1, 0), (2, 1), (3, 0), (4, 2), (5, 0)]
    assert beat.effect.chord.name == "Am7"
    assert [w["kind"] for w in ir["warnings"]] == ["unknown_chord"], "원본 IR은 보존한다"


def test_ai_keeps_only_unresolved_chords_and_preserves_other_warnings():
    ir = unknown_chord_ir("Am7", "Bm7")
    unrelated = {"measure": 0, "kind": "duration_mismatch", "detail": "기존 길이 오류"}
    ir["warnings"].append(unrelated)
    config = ai.Config(backend=ai.BACKEND_OPENAI, model="stub")
    result, outcome = refine.refine_ir(
        ir, config=config,
        ask=lambda *_: {"corrections": [
            {"op": "voicing", "name": "Am7", "frets": [0, 1, 0, 2, 0, -1]}]})
    assert outcome.realized == 1
    remaining = [w for w in result["warnings"] if w["kind"] == "unknown_chord"]
    assert len(remaining) == 1 and remaining[0]["beat"] == 1
    assert "Bm7" in remaining[0]["detail"]
    assert unrelated in result["warnings"]

    def failed(*_):
        raise ai.AiUnavailable("연결 실패")

    failed_result, _ = refine.refine_ir(ir, config=config, ask=failed)
    assert len([w for w in failed_result["warnings"] if w["kind"] == "unknown_chord"]) == 2
    assert any(w["kind"] == "ai_batch_failed" for w in failed_result["warnings"])


def test_widely_spaced_four_beams_still_make_sixty_fourths(tmp_path):
    """빔 4개가 5pt 간격이면 스택이 15pt — 절대 14pt 창은 마지막 빔을 놓친다."""
    xs = [60. + i * 20 for i in range(16)]
    geo = geometry.PageGeometry(
        glyphs=[glyph(x, 176, "0") for x in xs],
        vlines=[geometry.VLine(x + 2.5, 179, 225) for x in xs],
        beams=[geometry.Beam(62.5, 225 - level * 5, 362.5, 225 - level * 5)
               for level in range(4)])
    result, warn = measure(geo, bounds=(40., 380.), time_sig=(1, 4))
    assert [b["duration"] for b in result["beats"]] == [64] * 16
    assert not warn.items
