import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit


class RobotsStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class _Rule:
    pattern: str
    allow: bool

    @property
    def specificity(self) -> int:
        return len(self.pattern.replace("*", "").removesuffix("$"))

    def matches(self, path: str) -> bool:
        escaped = re.escape(self.pattern)
        expression = escaped.replace(r"\*", ".*")
        if expression.endswith(r"\$"):
            expression = f"{expression[:-2]}$"
        return re.match(expression, path) is not None


@dataclass(slots=True)
class RobotsPolicy:
    base_url: str
    user_agent: str
    status: RobotsStatus
    _rules: tuple[_Rule, ...] = ()
    sitemaps: tuple[str, ...] = ()

    @classmethod
    def from_text(cls, base_url: str, user_agent: str, text: str) -> "RobotsPolicy":
        groups, sitemaps = _parse_groups(text)
        product_token = user_agent.split("/", 1)[0].casefold()
        matching_groups: list[tuple[int, list[_Rule]]] = []
        for agents, rules in groups:
            matches = [
                len(agent)
                for agent in agents
                if agent == "*" or agent.casefold() in product_token
            ]
            if matches:
                matching_groups.append((max(matches), rules))

        if matching_groups:
            best_specificity = max(specificity for specificity, _rules in matching_groups)
            selected_rules = tuple(
                rule
                for specificity, rules in matching_groups
                if specificity == best_specificity
                for rule in rules
            )
        else:
            selected_rules = ()
        return cls(
            base_url=base_url,
            user_agent=user_agent,
            status=RobotsStatus.AVAILABLE,
            _rules=selected_rules,
            sitemaps=tuple(sitemaps),
        )

    @classmethod
    def unavailable(cls, base_url: str, user_agent: str) -> "RobotsPolicy":
        return cls(base_url, user_agent, RobotsStatus.UNAVAILABLE)

    def can_fetch(self, url: str) -> bool:
        if self.status is not RobotsStatus.AVAILABLE:
            return False
        if urlsplit(url).hostname != urlsplit(self.base_url).hostname:
            return False

        parsed = urlsplit(url)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        matches = [rule for rule in self._rules if rule.matches(path)]
        if not matches:
            return True
        longest = max(rule.specificity for rule in matches)
        strongest = [rule for rule in matches if rule.specificity == longest]
        return any(rule.allow for rule in strongest)


def _parse_groups(text: str) -> tuple[list[tuple[list[str], list[_Rule]]], list[str]]:
    groups: list[tuple[list[str], list[_Rule]]] = []
    sitemaps: list[str] = []
    agents: list[str] = []
    rules: list[_Rule] = []

    def finish_group() -> None:
        nonlocal agents, rules
        if agents:
            groups.append((agents, rules))
        agents = []
        rules = []

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.casefold()
        if key == "sitemap":
            if value:
                sitemaps.append(value)
            continue
        if key == "user-agent":
            if rules:
                finish_group()
            if value:
                agents.append(value.casefold())
            continue
        if key not in {"allow", "disallow"} or not agents:
            continue
        if key == "disallow" and not value:
            continue
        rules.append(_Rule(pattern=value or "/", allow=key == "allow"))
    finish_group()
    return groups, sitemaps
