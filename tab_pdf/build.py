"""IR → pyguitarpro Song. PDF 를 전혀 모른다."""

import guitarpro as gp
from guitarpro.models import (
    Beat, BeatStatus, BeatStrokeDirection, BendEffect, BendPoint, BendType,
    Chord, Duration, GuitarString, LyricLine, Lyrics, Measure, MeasureHeader,
    NaturalHarmonic, Note, NoteType, SlideType, Song, TimeSignature, Track,
    Voice,
)

from . import chords

# GP5 는 8비트 charset — 한글 보존에 필요
GP5_ENCODING = "cp949"
GP5_VERSION = (5, 1, 0)
# GP5 는 마디당 voice 슬롯 2개를 기대한다
GP5_VOICE_SLOTS = 2
DEFAULT_VELOCITY = 95
NYLON_GUITAR_MIDI_PROGRAM = 24
# 스트로크 속도. 0 은 "스트로크 없음" 이라 1 이상을 준다
STROKE_VALUE = 1
GUITAR_STRINGS = 6
# GP5 는 가사 줄 5개를 갖는다. 우리는 첫 줄만 쓴다 (곡 전체가 한 줄에 들어간다)
LYRIC_LINE_COUNT = 5
# 가사를 붙일 트랙 번호. GP5 의 trackChoice 는 "가사를 묶을 트랙을 가리키는 int"
# 이고 트랙은 1부터 센다. 0 을 넣으면 어느 트랙에도 묶이지 않아 GP 가 가사를
# 아예 그리지 않는다 — 실측으로 확인한 버그다.
LYRICS_TRACK = 1
# 가사를 어디에 담을지. 둘의 차이는 "누가 음절을 beat 에 배정하는가" 다.
#   row  : GP5 가사 줄. 텍스트 덩어리를 넘기면 **GP 가 알아서 음표에 분배한다**
#          (GP7 파일에도 `<Lyrics dispatched="true">` 로 남아 있다). 가사 전용
#          자리라 Lyrics 창에서 편집되지만, 분배 규칙이 문서화돼 있지 않다.
#   beat : beat 별 텍스트. PDF x 좌표로 계산한 배정을 **그대로 박아 넣는다**.
#          분배 규칙에 기대지 않으므로 어긋날 수 없다.
LYRIC_MODES = ("row", "beat")
DEFAULT_LYRIC_MODE = "row"
# Chord.strings 에서 안 쓰는 줄을 나타내는 값
UNUSED_STRING = -1

_STROKE_DIRECTIONS = {
    "down": BeatStrokeDirection.down,
    "up": BeatStrokeDirection.up,
}


def _apply_stroke(beat: Beat, stroke: str | None) -> None:
    direction = _STROKE_DIRECTIONS.get(stroke or "")
    if direction is None:
        return
    beat.effect.stroke.direction = direction
    beat.effect.stroke.value = STROKE_VALUE


def _first_lyric_measure(ir: dict) -> int | None:
    return next((measure["index"] for measure in ir["measures"]
                 if any(beat.get("lyric") for beat in measure["beats"])), None)


def _lyrics_for(ir: dict) -> Lyrics | None:
    """beat 에 배정된 음절을 GP5 가사 줄로 조립한다.

    음절이 없는 beat 는 빈 토큰으로 남겨 건너뛰기를 노린다. **이 부분은 검증되지
    않았다** — GP 의 분배 규칙이 문서화돼 있지 않고, 연속 공백을 한 칸으로
    접으면 음절이 앞으로 밀린다 (426 토큰 중 160개가 빈 토큰이다). 어긋나면
    `beat` 모드를 쓴다.
    """
    start = _first_lyric_measure(ir)
    if start is None:
        return None
    tokens = [beat.get("lyric") or ""
              for measure in ir["measures"] if measure["index"] >= start
              for beat in measure["beats"]]
    lines = [LyricLine(startingMeasure=start + 1, lyrics=" ".join(tokens))]
    lines += [LyricLine() for _ in range(LYRIC_LINE_COUNT - 1)]
    return Lyrics(trackChoice=LYRICS_TRACK, lines=lines)


# NoteEffect 의 boolean 플래그로 그대로 떨어지는 연주법.
# GP 는 해머온과 풀오프를 구분하지 않고 한 플래그로 다룬다.
_NOTE_FLAGS = {
    "hammer": "hammer",
    "vibrato": "vibrato",
    "palm_mute": "palmMute",
    "let_ring": "letRing",
    "staccato": "staccato",
    "accent": "accentuatedNote",
    "heavy_accent": "heavyAccentuatedNote",
    "ghost": "ghostNote",
}
_SLIDES = {
    "slide": SlideType.shiftSlideTo,
    "slide_out_down": SlideType.outDownwards,
    "slide_out_up": SlideType.outUpwards,
    "slide_in_below": SlideType.intoFromBelow,
    "slide_in_above": SlideType.intoFromAbove,
}
# pyguitarpro 모델 단위에서 value 1 = 반음이다 (writer 가 파일 스케일로 곱한다).
# 벤드 폭은 악보에 안 적혀 있는 경우가 많아 반음을 기본으로 둔다.
BEND_HALF_STEP = BendEffect.semitoneLength
_BEND_MID = BendEffect.maxPosition // 2

# corrections 가 통과시키는 연주법은 전부 여기서 GP5 로 떨어져야 한다.
# 두 집합이 어긋나면 조용히 무시되므로 테스트로 묶어 둔다.
SUPPORTED_TECHNIQUES = frozenset(_NOTE_FLAGS) | frozenset(_SLIDES) | {
    "bend", "harmonic", "dead"}


def _half_step_bend() -> BendEffect:
    """올렸다가 유지하는 반음 벤드. position 은 0..maxPosition 의 상대 시간이다."""
    return BendEffect(
        type=BendType.bend, value=BEND_HALF_STEP,
        points=[BendPoint(position=0, value=0),
                BendPoint(position=_BEND_MID, value=BEND_HALF_STEP),
                BendPoint(position=BendEffect.maxPosition, value=BEND_HALF_STEP)],
    )


def _apply_technique(note: Note, kind: str) -> bool:
    """연주법 하나를 노트에 붙인다. GP5 로 옮길 수 없으면 False."""
    flag = _NOTE_FLAGS.get(kind)
    if flag is not None:
        setattr(note.effect, flag, True)
        return True
    slide = _SLIDES.get(kind)
    if slide is not None:
        if slide not in note.effect.slides:
            note.effect.slides.append(slide)
        return True
    if kind == "bend":
        note.effect.bend = _half_step_bend()
        return True
    if kind == "harmonic":
        note.effect.harmonic = NaturalHarmonic()
        return True
    if kind == "dead":
        note.type = NoteType.dead
        return True
    return False


def _apply_techniques(beat: Beat, techniques: list[dict]) -> None:
    """연주법을 노트에 붙인다. `string` 이 None 이면 그 beat 의 모든 음에 붙인다.

    악센트처럼 악보 위·아래에 그려지는 표기는 한 줄이 아니라 그 박 전체에 걸린다.
    """
    for technique in techniques:
        string = technique.get("string")
        for note in beat.notes:
            if string is None or note.string == string:
                _apply_technique(note, technique["kind"])


def _make_chord_diagram(name: str,
                        voicing: tuple[tuple[int, int], ...] | None) -> Chord | None:
    """코드명에 다이어그램을 만든다. 보이싱을 모르면 None.

    GP5 는 다이어그램을 beat 에 붙인다. 트랙 단위 코드 목록(첫 페이지 상단에 모아
    보여주는 것)은 GP7/8 의 DiagramCollection 기능이라 GP5 포맷에는 자리가 없다.

    `Chord.strings` 는 인덱스 0 = 1번줄(고음 E) … 5 = 6번줄이고 미사용은 -1 이다.
    """
    if not voicing:
        return None
    by_string = dict(voicing)
    strings = [by_string.get(i + 1, UNUSED_STRING) for i in range(GUITAR_STRINGS)]
    fretted = [fret for fret in strings if fret > 0]
    return Chord(
        length=GUITAR_STRINGS, name=name, firstFret=min(fretted, default=1),
        strings=strings, show=True, newFormat=True,
    )


def _make_header(measure_ir: dict) -> MeasureHeader:
    header = MeasureHeader(number=measure_ir["index"] + 1)
    numerator, denominator = measure_ir["time_sig"]
    signature = TimeSignature()
    signature.numerator = numerator
    signature.denominator.value = denominator
    header.timeSignature = signature
    return header


def build_song(ir: dict, *, lyric_mode: str = DEFAULT_LYRIC_MODE) -> Song:
    """IR 을 pyguitarpro Song 으로 만든다.

    `lyric_mode` 는 가사를 어디에 담을지 고른다 — LYRIC_MODES 주석 참고.
    """
    if lyric_mode not in LYRIC_MODES:
        raise ValueError(f"lyric_mode 는 {' | '.join(LYRIC_MODES)} 중 하나여야 합니다: "
                         f"{lyric_mode!r}")
    song = Song(title=ir.get("title", ""), artist=ir.get("artist", ""),
                tempo=ir.get("tempo", 80))
    song.tracks.clear()
    song.measureHeaders.clear()

    track = Track(song, name="Guitar")
    track.channel.instrument = NYLON_GUITAR_MIDI_PROGRAM
    track.strings = [GuitarString(i + 1, value)
                     for i, value in enumerate(ir["tuning"])]
    track.measures.clear()

    previous_chord = None
    for measure_ir in ir["measures"]:
        header = _make_header(measure_ir)
        song.measureHeaders.append(header)

        measure = Measure(track, header)
        measure.voices.clear()
        voice = Voice(measure)
        for beat_ir in measure_ir["beats"]:
            beat = Beat(
                voice,
                duration=Duration(value=beat_ir["duration"],
                                  isDotted=beat_ir["dotted"]),
                status=BeatStatus.normal,
            )
            for note_ir in beat_ir["notes"]:
                beat.notes.append(Note(
                    beat, value=note_ir["fret"], string=note_ir["string"],
                    velocity=DEFAULT_VELOCITY, type=NoteType.normal,
                ))
            _apply_stroke(beat, beat_ir.get("stroke"))
            _apply_techniques(beat, beat_ir.get("techniques", ()))
            if lyric_mode == "beat":
                # x 좌표로 계산한 배정을 그대로 박는다 — GP 의 분배 규칙을 안 탄다
                beat.text = beat_ir.get("lyric") or None
            chord_name = beat_ir.get("chord")
            if chord_name and chord_name != previous_chord:
                diagram = _make_chord_diagram(
                    chord_name, chords.voicing_in(ir, chord_name))
                if diagram is not None:
                    beat.effect.chord = diagram
                previous_chord = chord_name
            voice.beats.append(beat)
        measure.voices.append(voice)
        while len(measure.voices) < GP5_VOICE_SLOTS:
            measure.voices.append(Voice(measure))
        track.measures.append(measure)

    song.tracks.append(track)
    if lyric_mode == "row":
        lyrics = _lyrics_for(ir)
        if lyrics is not None:
            song.lyrics = lyrics
    return song


def write_gp5(song: Song, file_path: str) -> None:
    """.gp5 로 쓴다. 인코딩은 고정 — 한글 제목이 깨지면 안 된다."""
    gp.write(song, file_path, version=GP5_VERSION, encoding=GP5_ENCODING)
