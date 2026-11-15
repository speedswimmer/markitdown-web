"""Tests for the text coverage check (digital PDFs only)."""
from collections import Counter

import pytest

from app.coverage import Coverage, PageCoverage, page_coverage, summarize, tokens


def test_tokens_lowercase_and_count() -> None:
    assert tokens("Umsatz umsatz UMSATZ") == Counter({"umsatz": 3})


def test_short_words_are_ignored_but_digits_count() -> None:
    assert tokens("Es ist 7 Uhr am 12. Mai") == Counter({"ist": 1, "uhr": 1, "mai": 1, "7": 1, "12": 1})


def test_markdown_syntax_is_not_a_token() -> None:
    assert tokens("| **Betrag** | _netto_ |\n# Titel\n- Punkt") == Counter(
        {"betrag": 1, "netto": 1, "titel": 1, "punkt": 1}
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ver-\nlust", "verlust"),            # hyphenation at a line break
        ("Ver­lust", "verlust"),          # soft hyphen
        ("ﬁnden", "finden"),              # ligature fi, NFKC
        ("Straße", "straße"),                  # ß stays a letter
    ],
)
def test_tokens_normalize(raw: str, expected: str) -> None:
    assert tokens(raw) == Counter({expected: 1})


def test_real_hyphen_between_words_splits() -> None:
    # "Nord-Süd" is two words, only a hyphen followed by a line break joins.
    assert tokens("Nord-Süd") == Counter({"nord": 1, "süd": 1})


def test_empty_text_gives_empty_counter() -> None:
    assert tokens("") == Counter()
    assert tokens("  \n\t ") == Counter()


def test_page_coverage_counts_recovered_tokens() -> None:
    assert page_coverage("Umsatz 2025 netto", "| Umsatz | netto |") == PageCoverage(found=2, total=3)


def test_page_coverage_caps_at_reference_count() -> None:
    assert page_coverage("Umsatz Umsatz Umsatz", "Umsatz") == PageCoverage(found=1, total=3)
    assert page_coverage("Umsatz", "Umsatz Umsatz Umsatz") == PageCoverage(found=1, total=1)


def test_page_coverage_without_reference_text() -> None:
    assert page_coverage("", "# Irgendwas") == PageCoverage(found=0, total=0)


def test_summarize_weights_by_token_count() -> None:
    # 100 of 100 and 0 of 20: weighted 83 %, not the page average of 50 %.
    assert summarize([PageCoverage(100, 100), PageCoverage(0, 20)]) == Coverage(percent=83, weak_pages=[2])


def test_summarize_reports_weak_pages_one_based() -> None:
    pages = [PageCoverage(20, 20), PageCoverage(17, 20), PageCoverage(18, 20)]
    assert summarize(pages) == Coverage(percent=92, weak_pages=[2])


def test_summarize_ignores_short_pages_for_weakness() -> None:
    # 5 of 10 is 50 %, but under MIN_WORDS the page is not judged.
    assert summarize([PageCoverage(30, 30), PageCoverage(5, 10)]) == Coverage(percent=88, weak_pages=[])


def test_summarize_rounds_to_whole_percent() -> None:
    assert summarize([PageCoverage(199, 200)]).percent == 100
    assert summarize([PageCoverage(189, 200)]).percent == 95
    assert summarize([PageCoverage(188, 200)]).percent == 94


def test_summarize_without_enough_text_gives_none() -> None:
    assert summarize([PageCoverage(10, 10), PageCoverage(9, 9)]) is None
    assert summarize([]) is None
    assert summarize([PageCoverage(0, 0)]) is None
