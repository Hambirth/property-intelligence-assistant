from app.repositories.chunks import _lexical_score, _lexical_terms


def test_lexical_fallback_matches_distinctive_property_terms() -> None:
    terms = _lexical_terms("DarGlobal The Astera interiors by Aston Martin")

    matched, score = _lexical_score(
        terms,
        "The Astera, Interiors by Aston Martin - Official DarGlobal Brochure",
    )

    assert matched == 4
    assert score == 0.99


def test_lexical_fallback_keeps_unsupported_query_below_evidence_threshold() -> None:
    terms = _lexical_terms(
        "Which property has a private helipad in London and costs 50 million pounds?"
    )

    _matched, score = _lexical_score(
        terms,
        "A luxury property investment brochure with prices in millions.",
    )

    assert score < 0.55


def test_lexical_fallback_recognizes_w_residences_question() -> None:
    terms = _lexical_terms("What information is available about W Residences Dubai?")

    matched, score = _lexical_score(
        terms,
        "W Residences Dubai Downtown is located near Burj Khalifa and Dubai Mall.",
    )

    assert terms == frozenset({"w", "dubai"})
    assert matched == 2
    assert score == 0.99
