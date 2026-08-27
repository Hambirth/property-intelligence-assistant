import pytest

from app.scraping.cli import build_parser, exit_code_for_summary
from app.scraping.models import IngestionSummary


def test_cli_supports_only_fixed_source_choices() -> None:
    args = build_parser().parse_args(["--source", "wasalt", "--limit", "5", "--dry-run"])

    assert args.source == "wasalt"
    assert args.limit == 5
    assert args.dry_run is True


def test_cli_has_no_arbitrary_url_option() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["--source", "wasalt", "--url", "https://unexpected.example/page"]
        )


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        (IngestionSummary(fetched=2), 0),
        (IngestionSummary(failed=1), 1),
        (IngestionSummary(blocked=1), 2),
    ],
)
def test_cli_exit_status_is_meaningful(summary: IngestionSummary, expected: int) -> None:
    assert exit_code_for_summary(summary) == expected
