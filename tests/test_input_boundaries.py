"""입력 경계의 재현 사례 — 경로 별칭·손상 PDF·빈 AI 응답·조성 표기."""

import types
import unicodedata

import pytest

import convert
from tab_pdf import ai, build, extract, header


def test_nfd_alias_of_input_pdf_is_refused_as_output(tmp_path):
    """macOS 는 NFC/NFD 경로를 같은 파일로 연다 — 문자열 비교로는 원본을 덮어쓴다."""
    pdf = tmp_path / unicodedata.normalize("NFC", "반딧불.pdf")
    pdf.write_bytes(b"%PDF-1.4")
    alias = str(tmp_path / unicodedata.normalize("NFD", "반딧불.pdf"))
    args = convert.build_parser().parse_args([str(pdf), "-o", alias])
    assert convert._validate(args) is not None
    assert pdf.read_bytes() == b"%PDF-1.4"


def test_nfd_alias_between_output_and_ir_is_refused(tmp_path):
    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    name = "결과.gp5.ir.json"
    args = convert.build_parser().parse_args([
        str(pdf), "-o", str(tmp_path / unicodedata.normalize("NFC", name)),
        "--ir", str(tmp_path / unicodedata.normalize("NFD", name))])
    assert convert._validate(args) is not None


def test_corrupt_pdf_is_reported_not_raised(tmp_path, capsys):
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"this is not a pdf")
    with pytest.raises(extract.NotATabPdf):
        extract.extract_ir(str(pdf))
    assert convert.main([str(pdf), "-o", str(tmp_path / "out.gp5")]) == 2
    assert "오류" in capsys.readouterr().err


def test_empty_choices_fail_only_that_batch(monkeypatch):
    """OpenAI 호환 서버가 choices=[] 를 주면 배치 실패로 기록돼야 한다."""
    empty = types.SimpleNamespace(choices=[])
    client = types.SimpleNamespace(chat=types.SimpleNamespace(
        completions=types.SimpleNamespace(create=lambda **_: empty)))
    monkeypatch.setattr(ai, "_openai_client", lambda *_: client)
    config = ai.Config(backend="openai", model="m", base_url="http://x", api_key="k")
    with pytest.raises(ai.AiUnavailable, match="choices"):
        ai._complete_openai(config, "s", "u")


@pytest.mark.parametrize("text,expected", [
    ("C", "CMajor"), ("Am", "AMinor"), ("F#m", "FMinorSharp"),
    ("C major", "CMajor"), ("A minor", "AMinor"), ("Bb Major", "BMajorFlat"),
    ("F# minor", "FMinorSharp"), ("Amin", "AMinor"), ("Cmaj", "CMajor"),
    ("E♭ major", "EMajorFlat"), ("Gbm", "FMinorSharp"), ("Dbm", "CMinorSharp"),
])
def test_key_names_cover_common_spellings(text, expected):
    assert build._key_signature(text).name == expected


@pytest.mark.parametrize("text", ["", "H", "Cm7", "A dorian"])
def test_unreadable_key_is_none(text):
    assert header.parse_key(text) is None


def test_unreadable_key_is_warned(tmp_path, monkeypatch):
    """조성 표기를 못 읽으면 조용히 C장조로 두지 않고 경고한다."""
    monkeypatch.setattr(extract, "_header_fields",
                        lambda *_: {"key": "A dorian"})
    ir = extract.extract_ir(str(_one_measure_pdf(tmp_path)), tempo=80)
    assert any(w["kind"] == "key_unreadable" for w in ir["warnings"])


def _one_measure_pdf(tmp_path):
    import pymupdf
    path = tmp_path / "one.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for y in (100., 105., 110., 115., 120., 160., 168., 176., 184., 192., 200.):
        page.draw_line((40, y), (400, y), width=0.4)
    page.draw_line((400, 100), (400, 200), width=0.6)
    page.insert_text((70, 176), "0", fontsize=9.3)
    doc.save(path)
    doc.close()
    return path


def test_dotted_sixty_fourth_pins_cannot_fake_an_exact_measure():
    """점64분(0.09375박)이 단위 반올림으로 줄어 넘친 마디가 '정확'으로 통과하면 안 된다."""
    from tab_pdf import durations
    dotted64 = durations.LegalDuration(64, True, 0.09375)
    pins = dict.fromkeys(range(6), dotted64)
    fitted, exact = durations.fit_durations(
        [0.09375] * 6 + [3.0, 0.5], 4.0, pinned=pins)
    assert not exact or abs(sum(d.quarters for d in fitted) - 4.0) < durations.EPSILON


PDF = __import__("pathlib").Path(__file__).resolve().parents[1] / "pdf" / "나는반딧불.pdf"


def _rescaled(tmp_path, factor):
    import pymupdf
    source = pymupdf.open(PDF)
    target = pymupdf.open()
    for page in source:
        new = target.new_page(width=page.rect.width * factor,
                              height=page.rect.height * factor)
        new.show_pdf_page(new.rect, source, page.number)
    path = tmp_path / f"scaled_{factor}.pdf"
    target.save(path)
    return path


def _music(ir):
    return [[(b["duration"], b["dotted"], b["tuplet"], b.get("rest"),
              sorted((n["string"], n["fret"]) for n in b["notes"]))
             for b in m["beats"]] for m in ir["measures"]]


@pytest.mark.skipif(not PDF.exists(), reason="실제 입력 PDF 없음")
@pytest.mark.parametrize("factor", [0.8, 1.25, 1.6])
def test_same_score_at_another_page_scale_reads_the_same(factor, tmp_path):
    """같은 악보를 확대·축소해 내보낸 PDF 도 같은 음악으로 읽어야 한다."""
    base = extract.extract_ir(str(PDF), tempo=80)
    scaled = extract.extract_ir(str(_rescaled(tmp_path, factor)), tempo=80)
    assert _music(scaled) == _music(base)
    assert ([w["kind"] for w in scaled["warnings"]]
            == [w["kind"] for w in base["warnings"]])


def test_narrow_tab_staff_is_not_mistaken_for_a_shrunken_page(tmp_path):
    """오선 5pt·타브 6pt 간격에 9.3pt 프렛 — 조판이 다를 뿐 배율은 1 이다."""
    import pymupdf
    path = tmp_path / "narrow.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    melody = [100. + 5 * i for i in range(5)]
    tab = [150. + 6 * i for i in range(6)]
    for y in melody + tab:
        page.draw_line((40, y), (400, y), width=0.4)
    page.draw_line((400, melody[0]), (400, tab[-1]), width=0.6)
    for x in (70., 150., 230., 310.):
        page.insert_text((x, tab[2] + 3.3), "5", fontsize=9.3)
    doc.save(path)
    doc.close()
    ir = extract.extract_ir(str(path), tempo=80)
    assert [[n["fret"] for n in b["notes"]] for b in ir["measures"][0]["beats"]] == [[5]] * 4


def test_page_without_digits_is_not_rescaled():
    """숫자가 없으면 선 간격만으로는 배율인지 조판인지 가를 수 없다 — 건드리지 않는다."""
    from tab_pdf import geometry
    ys = [100. + 5 * i for i in range(5)] + [150. + 6 * i for i in range(6)]
    hlines = [geometry.HLine(y, 40., 400.) for y in ys]
    assert geometry.page_scale(hlines, []) == 1.0


def test_header_digits_do_not_decide_the_page_scale(tmp_path):
    """좁은 타브 간격 악보에 7.2pt 날짜 — 머리글 숫자가 배율을 정하면 프렛이 사라진다."""
    import pymupdf
    path = tmp_path / "dated.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    melody = [100. + 5 * i for i in range(5)]
    tab = [150. + 6 * i for i in range(6)]
    for y in melody + tab:
        page.draw_line((40, y), (400, y), width=0.4)
    page.draw_line((400, melody[0]), (400, tab[-1]), width=0.6)
    for x in (70., 150., 230., 310.):
        page.insert_text((x, tab[2] + 3.3), "5", fontsize=9.3)
    page.insert_text((40, 40), "2026-09-25", fontsize=7.2)
    doc.save(path)
    doc.close()
    ir = extract.extract_ir(str(path), tempo=80)
    assert [[n["fret"] for n in b["notes"]] for b in ir["measures"][0]["beats"]] == [[5]] * 4


def test_melody_fingering_digits_do_not_decide_the_page_scale(tmp_path):
    """오선 위 7.2pt 운지 숫자 다섯 — 타브 6선 그룹의 숫자만 배율 근거다."""
    import pymupdf
    path = tmp_path / "fingered.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    melody = [100. + 5 * i for i in range(5)]
    tab = [150. + 6 * i for i in range(6)]
    for y in melody + tab:
        page.draw_line((40, y), (400, y), width=0.4)
    page.draw_line((400, melody[0]), (400, tab[-1]), width=0.6)
    for x in (70., 150., 230., 310.):
        page.insert_text((x, tab[2] + 3.3), "5", fontsize=9.3)
    for x in (60., 120., 180., 240., 300.):
        page.insert_text((x, 97), "1", fontsize=7.2)
    doc.save(path)
    doc.close()
    ir = extract.extract_ir(str(path), tempo=80)
    assert [[n["fret"] for n in b["notes"]] for b in ir["measures"][0]["beats"]] == [[5]] * 4
