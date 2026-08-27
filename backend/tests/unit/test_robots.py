from pathlib import Path

from app.scraping.robots import RobotsPolicy

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_wasalt_search_is_disallowed_but_project_is_allowed() -> None:
    policy = RobotsPolicy.from_text(
        "https://wasalt.sa",
        "PropertyIntelligenceBot/0.1",
        (FIXTURES / "wasalt_robots.txt").read_text(),
    )

    assert not policy.can_fetch("https://wasalt.sa/search")
    assert not policy.can_fetch("https://wasalt.sa/search?page=2")
    assert policy.can_fetch("https://wasalt.sa/en/project/demo")
    assert policy.sitemaps == (
        "https://cdn.wasalt.sa/sitemap/product_sitemap_en_sa.xml.gz",
    )


def test_unavailable_policy_denies_everything() -> None:
    policy = RobotsPolicy.unavailable("https://darglobal.co.uk", "PropertyIntelligenceBot/0.1")

    assert not policy.can_fetch("https://darglobal.co.uk/projects")


def test_policy_is_bound_to_exact_host() -> None:
    policy = RobotsPolicy.from_text(
        "https://wasalt.sa", "PropertyIntelligenceBot/0.1", "User-agent: *\nAllow: /"
    )

    assert not policy.can_fetch("https://www.wasalt.sa/en/project/demo")


def test_specific_user_agent_group_overrides_wildcard_group() -> None:
    text = """
    User-agent: ChatGPT-User
    Allow: /search
    User-agent: *
    Allow: /
    Disallow: /search
    """

    generic = RobotsPolicy.from_text("https://wasalt.sa", "PropertyIntelligenceBot/0.1", text)
    specific = RobotsPolicy.from_text("https://wasalt.sa", "ChatGPT-User/1.0", text)

    assert not generic.can_fetch("https://wasalt.sa/search")
    assert specific.can_fetch("https://wasalt.sa/search")
