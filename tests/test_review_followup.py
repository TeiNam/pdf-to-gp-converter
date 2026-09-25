"""성부별 연주법 배정과 여러 셋잇단 후보창의 재현 사례."""

import guitarpro as gp
import pymupdf
import pytest

import convert
from tab_pdf import build, durations, geometry

from test_music_fixes import SYSTEM, glyph, measure, round_trip, song_for
from test_polyphony import QUARTER_REST, TUPLET, _flag, down_stems, up_stems


@pytest.mark.parametrize("polyphonic", [False, True])
@pytest.mark.parametrize("mark,frets", [("H", (3, 5)), ("P", (5, 3)), ("S", (3, 5))])
def test_technique_follows_slur_past_an_intervening_bass(mark, frets, polyphonic, tmp_path):
    """1번줄의 H/P/S는 중간에 연주된 6번줄 음에 옮겨 붙으면 안 된다."""
    pdf, output = tmp_path / "technique.pdf", tmp_path / "technique.gp5"
    with pymupdf.open() as doc:
        page = doc.new_page(width=460, height=280)
        for y in SYSTEM.melody_ys + SYSTEM.tab_ys:
            page.draw_line((40, y), (420, y), width=0.4)
        page.draw_line((420, 100), (420, 200), width=0.6)
        for x, fret in zip((60, 240), frets):
            page.insert_text((x, 160), str(fret), fontsize=9.3)
            page.draw_line((x + 2.5, 135), (x + 2.5, 151), width=0.5)
        for x in (60, 150, 240, 330):
            page.insert_text((x, 200), "0", fontsize=9.3)
            top, bottom = (202, 225) if polyphonic else (170, 197)
            page.draw_line((x + 2.5, top), (x + 2.5, bottom), width=0.5)
        page.insert_text((190, 155), mark, fontsize=9.3)
        page.draw_bezier((65, 157), (95, 145), (215, 145), (239, 157))
        doc.save(pdf)

    assert convert.main([str(pdf), "-o", str(output), "--tempo", "80"]) == 0
    song = gp.parse(str(output), encoding=build.GP5_ENCODING)
    affected = [
        (voice, beat.startInMeasure, note.string, note.value)
        for voice, part in enumerate(song.tracks[0].measures[0].voices)
        for beat in part.beats for note in beat.notes
        if note.effect.hammer or note.effect.slides]
    assert affected == [(0, 0, 1, frets[0])]


@pytest.mark.parametrize("groups,unit,flags", [(3, 1., (1, 2, 1)), (8, .5, (2, 3, 2))])
def test_many_triplet_windows_in_both_voices_keep_plain_notes(groups, unit, flags, tmp_path):
    """두 성부의 여러 후보창을 함께 바꾸되 같은 시간축을 중복 탐색하지 않는다."""
    xs = [60. + 90. * unit * (group + q)
          for group in range(groups) for q in (0., 1 / 3, .5)]
    geo = geometry.PageGeometry(
        glyphs=[
            *[glyph(x, 160, "3") for x in xs], *[glyph(x, 200, "0") for x in xs],
            *[_flag(x, flags[i % 3], down=False) for i, x in enumerate(xs)],
            *[_flag(x, flags[i % 3]) for i, x in enumerate(xs)],
            *[glyph(60. + 90 * unit * (group + 1 / 6), y, TUPLET)
              for group in range(groups) for y in (128, 232)]],
        vlines=[*up_stems(xs), *down_stems(xs)])
    if groups * unit == 3:
        geo.glyphs.extend([glyph(330, 152, QUARTER_REST), glyph(330, 192, QUARTER_REST)])
    result, warn = measure(geo, bounds=(40., 420.))
    voices = round_trip(song_for([result]), tmp_path).tracks[0].measures[0].voices
    for voice in voices:
        sounding = [beat for beat in voice.beats if beat.notes]
        assert [beat.duration.time for beat in sounding] == [
            int(ticks * unit) for ticks in [320, 160, 480] * groups]
        assert [beat.startInMeasure for beat in sounding] == [
            int(unit * (960 * group + tick))
            for group in range(groups) for tick in (0, 320, 480)]
        assert sum(beat.duration.time for beat in voice.beats) == 3840
    assert not warn.items


def test_consecutive_techniques_share_a_slur_but_not_the_source_note(tmp_path):
    """하나의 슬러 안의 3 H 5 P 3은 3과 5 각각에 해머온/풀오프를 건다."""
    geo = geometry.PageGeometry(
        glyphs=[glyph(60, 160, "3"), glyph(150, 160, "5"), glyph(240, 160, "3"),
                glyph(100, 155, "H"), glyph(190, 155, "P")],
        curves=[geometry.Curve(65, 157, 239, 157, True)])
    result, warn = measure(geo)
    song = round_trip(song_for([result]), tmp_path)
    notes = [note for beat in song.tracks[0].measures[0].voices[0].beats
             for note in beat.notes]
    assert [(note.value, note.effect.hammer) for note in notes] == [
        (3, True), (5, True), (3, False)]
    assert not warn.items


def test_many_independent_triplet_windows_find_an_exact_reading():
    """창을 중복 제거하는 것만으로는 못 고치는 단성부 6개 후보창."""
    pins = {
        i: durations.LegalDuration(value, False, 4 / value)
        for i, value in enumerate([8, 16, 8] * 6)}
    windows = [tuple(range(start, start + 3)) for start in range(0, 18, 3)]
    fitted, exact, _ = durations.fit_with_triplets(
        [1 / 3] * 18, 6.0, pins, set(), windows)
    assert exact
    assert abs(sum(d.quarters for d in fitted) - 6.0) < durations.EPSILON
    for start in range(0, 18, 3):
        assert sum(d.tuplet is not None for d in fitted[start:start + 3]) == 2
