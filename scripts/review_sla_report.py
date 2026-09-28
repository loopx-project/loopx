#!/usr/bin/env python3
"""Measure pull-request review latency and cross-author review activity.

Reproduces the "Observed" column of the Review Service Levels section and the
cross-author review counts used for code-owner eligibility in
.github/GOVERNANCE.md. Reads public pull-request metadata through `gh api
graphql`; nothing is written.

Two definitions keep the report aligned with the published contract:

* the response clock starts when the pull request is opened, or when it first
  leaves draft, whichever is later. The type of the first draft-transition
  event is what distinguishes the two: a pull request created as a draft has a
  ready-for-review event first, one created ready has a conversion first.
* a "response" is a review, comment or foreign merge by an account on the
  published maintainer roster (``--responder``). Comments from anyone else are
  recorded, but they are not maintainer responses, because the published
  target covers the people who can satisfy a review requirement.

Readiness, human/bot identity and the roster all come from GitHub typed data
(``__typename``), not from login prefixes.

Business time counts wall-clock hours on Monday to Friday in UTC+8. Public
holidays are not modelled, so the figures are slightly pessimistic around them.
Pull requests whose review or comment connection is larger than one page are
reported as truncated rather than silently treated as complete.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# Appointed reviewers from the "Maintainer And Review Roster" table in
# .github/GOVERNANCE.md. Only these accounts can satisfy the published
# first-response target; tests/test_review_sla_report.py checks this list
# against that table.
DEFAULT_RESPONDERS = ("huangruiteng", "steven-kid", "maxliux5")
# Fallback only for inputs that carry no actor type (an old --input file).
# GitHub's typed actors are authoritative: a Bot is never a responder even
# when its login looks human, and a human whose login starts with "copilot"
# still counts.
NON_HUMAN_LOGIN_FALLBACK = re.compile(r"(\[bot\]$|^dependabot|^github-actions)", re.I)
NON_HUMAN_TYPENAMES = {"Bot", "Mannequin"}
PAGE_SIZE = {"files": 100, "reviews": 30, "comments": 30, "timeline": 5}
UTC8 = timezone(timedelta(hours=8))
BUSINESS_DAY_HOURS = 24.0

QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(first: 40, after: $cursor, orderBy: {field: CREATED_AT, direction: DESC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number state createdAt mergedAt isDraft
        author { login __typename }
        mergedBy { login __typename }
        files(first: 100) { totalCount nodes { path } }
        reviews(first: 30) { totalCount nodes { author { login __typename } submittedAt } }
        comments(first: 30) { totalCount nodes { author { login __typename } createdAt } }
        timelineItems(itemTypes: [READY_FOR_REVIEW_EVENT, CONVERT_TO_DRAFT_EVENT], first: 5) {
          pageInfo { hasNextPage }
          nodes {
            __typename
            ... on ReadyForReviewEvent { createdAt }
            ... on ConvertToDraftEvent { createdAt }
          }
        }
      }
    }
  }
}
"""


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _login(node: dict[str, Any] | None) -> str | None:
    return (node or {}).get("login")


def is_human(actor: dict[str, Any] | str | None) -> bool:
    """Human identity from GitHub's typed actor, with a login fallback."""
    if isinstance(actor, str):
        actor = {"login": actor}
    login = _login(actor)
    if not login:
        return False
    typename = (actor or {}).get("__typename")
    if typename:
        return typename not in NON_HUMAN_TYPENAMES
    return not NON_HUMAN_LOGIN_FALLBACK.search(login)


def review_clock_start(pr: dict[str, Any]) -> tuple[datetime, str]:
    """When the review clock starts: opening, or the first exit from draft.

    A pull request created as a draft emits a ready-for-review event before any
    conversion; one created ready emits a conversion first. The first event's
    type therefore tells which clock the published target means.
    """
    opened = _ts(pr["createdAt"])
    nodes = ((pr.get("timelineItems") or {}).get("nodes")) or []
    first = nodes[0] if nodes else None
    if first is None:
        return opened, "opened"
    if first.get("__typename") == "ReadyForReviewEvent" and first.get("createdAt"):
        return _ts(first["createdAt"]), "left_draft"
    return opened, "opened"


def truncated_records(prs: Iterable[dict[str, Any]]) -> int:
    """Pull requests whose review or comment connection exceeded one page."""
    count = 0
    for pr in prs:
        for key in ("reviews", "comments"):
            connection = pr.get(key) or {}
            total = connection.get("totalCount")
            if isinstance(total, int) and total > len(connection.get("nodes") or []):
                count += 1
                break
    return count


def business_hours(start: datetime, end: datetime) -> float:
    """Wall-clock hours between two instants that fall on UTC+8 weekdays."""
    cur, stop = start.astimezone(UTC8), end.astimezone(UTC8)
    hours = 0.0
    while cur < stop:
        midnight = (cur + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        nxt = min(stop, midnight)
        if cur.weekday() < 5:
            hours += (nxt - cur).total_seconds() / 3600
        cur = nxt
    return hours


def first_response(pr: dict[str, Any], responders: Iterable[str]) -> datetime | None:
    """Earliest review, comment or foreign merge by an appointed responder."""
    roster = {name.casefold() for name in responders}
    author = _login(pr.get("author"))

    def eligible(actor: dict[str, Any] | None) -> bool:
        login = _login(actor)
        return (
            bool(login)
            and is_human(actor)
            and (login or "").casefold() in roster
            and (login or "").casefold() != (author or "").casefold()
        )

    events = [
        _ts(node["submittedAt"])
        for node in (pr.get("reviews") or {}).get("nodes", [])
        if node.get("submittedAt") and eligible(node.get("author"))
    ]
    events += [
        _ts(node["createdAt"])
        for node in (pr.get("comments") or {}).get("nodes", [])
        if node.get("createdAt") and eligible(node.get("author"))
    ]
    if pr.get("mergedAt") and eligible(pr.get("mergedBy")):
        events.append(_ts(pr["mergedAt"]))
    return min(events) if events else None


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def latency_summary(
    prs: Iterable[dict[str, Any]],
    *,
    exclude_authors: set[str],
    responders: Iterable[str] = DEFAULT_RESPONDERS,
) -> dict[str, Any]:
    rows = [
        pr for pr in prs
        if not pr.get("isDraft")
        and is_human(pr.get("author"))
        and (_login(pr.get("author")) or "").casefold()
        not in {author.casefold() for author in exclude_authors}
    ]
    response, merge = [], []
    clocks = {"opened": 0, "left_draft": 0}
    for pr in rows:
        start, basis = review_clock_start(pr)
        clocks[basis] += 1
        responded = first_response(pr, responders)
        if responded:
            response.append(business_hours(start, responded))
        if pr.get("mergedAt"):
            merge.append(business_hours(start, _ts(pr["mergedAt"])))
    two_days = 2 * BUSINESS_DAY_HOURS
    return {
        "pull_requests": len(rows),
        "responders": sorted(responders),
        "without_maintainer_response": len(rows) - len(response),
        "first_maintainer_response_within_2_business_days": (
            sum(1 for h in response if h <= two_days) / len(rows) if rows else None
        ),
        "first_maintainer_response_hours": {f"p{int(q * 100)}": percentile(response, q) for q in (0.5, 0.75, 0.9)},
        "merge_hours": {f"p{int(q * 100)}": percentile(merge, q) for q in (0.5, 0.9)},
        "clock_start": clocks,
        "truncated_records": truncated_records(rows),
    }


def cross_author_reviews(prs: Iterable[dict[str, Any]], *, path_prefixes: tuple[str, ...] = ()) -> Counter[str]:
    """Distinct pull requests each human reviewed or commented on as a non-author."""
    counts: Counter[str] = Counter()
    for pr in prs:
        paths = [(node or {}).get("path") for node in (pr.get("files") or {}).get("nodes", [])]
        if path_prefixes and not any(
            isinstance(path, str) and path.startswith(path_prefixes) for path in paths
        ):
            continue
        author = (_login(pr.get("author")) or "").casefold()
        actors = [
            node.get("author")
            for node in (pr.get("reviews") or {}).get("nodes", [])
            + (pr.get("comments") or {}).get("nodes", [])
        ]
        reviewers = {_login(actor) for actor in actors if is_human(actor)}
        counts.update(
            reviewer
            for reviewer in reviewers
            if reviewer and reviewer.casefold() != author
        )
    return counts


def fetch(repo: str, since: str) -> list[dict[str, Any]]:
    owner, name = repo.split("/", 1)
    cursor, out = None, []
    while True:
        args = ["gh", "api", "graphql", "-f", f"query={QUERY}", "-f", f"owner={owner}", "-f", f"name={name}"]
        if cursor:
            args += ["-f", f"cursor={cursor}"]
        page = json.loads(subprocess.run(args, capture_output=True, text=True, check=True).stdout)
        data = page["data"]["repository"]["pullRequests"]
        for pr in data["nodes"]:
            if pr["createdAt"][:10] < since:
                return out
            out.append(pr)
        if not data["pageInfo"]["hasNextPage"]:
            return out
        cursor = data["pageInfo"]["endCursor"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default="loopx-project/loopx")
    parser.add_argument("--since", required=True, help="Count pull requests opened on or after this date (YYYY-MM-DD).")
    parser.add_argument("--exclude-author", action="append", default=["huangruiteng"],
                        help="Author whose own pull requests are not contributor PRs (repeatable).")
    parser.add_argument("--responder", action="append",
                        help="Account whose review or comment satisfies the maintainer target "
                             "(repeatable; defaults to the appointed roster in .github/GOVERNANCE.md).")
    parser.add_argument("--path", action="append", default=[],
                        help="Limit cross-author review counts to PRs touching this path prefix (repeatable).")
    parser.add_argument("--input", help="Read pull requests from a JSON Lines file instead of the API.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    if not REPO_PATTERN.match(args.repo):
        parser.error("--repo must look like owner/name")

    if args.input:
        with open(args.input, encoding="utf-8") as handle:
            prs = [json.loads(line) for line in handle if line.strip()]
        prs = [pr for pr in prs if pr["createdAt"][:10] >= args.since]
    else:
        prs = fetch(args.repo, args.since)
    responders = tuple(dict.fromkeys(args.responder or DEFAULT_RESPONDERS))
    report = {
        "repo": args.repo,
        "since": args.since,
        "contributor_latency": latency_summary(
            prs, exclude_authors=set(args.exclude_author), responders=responders
        ),
        "cross_author_reviews": dict(cross_author_reviews(prs, path_prefixes=tuple(args.path)).most_common(20)),
    }
    if args.format == "json":
        json.dump(report, sys.stdout, indent=2)
        print()
        return 0
    lat = report["contributor_latency"]
    share = lat["first_maintainer_response_within_2_business_days"]
    print(f"{args.repo} pull requests opened since {args.since}")
    print(f"responders: {', '.join(lat['responders'])}")
    print(
        f"contributor PRs: {lat['pull_requests']} "
        f"(no maintainer response: {lat['without_maintainer_response']}, "
        f"truncated lists: {lat['truncated_records']})"
    )
    print(
        f"first maintainer response within 2 business days: {share:.0%}"
        if share is not None
        else "no contributor PRs"
    )
    def fmt(hours: dict[str, float | None]) -> str:
        return ", ".join(f"{key}={value:.1f}h" for key, value in hours.items() if value is not None)

    print(f"first maintainer response (business hours): {fmt(lat['first_maintainer_response_hours'])}")
    print(f"merge from the review clock (business hours): {fmt(lat['merge_hours'])}")
    clocks = lat["clock_start"]
    print(f"review clock started at opening: {clocks['opened']}, after leaving draft: {clocks['left_draft']}")
    scope = f" touching {', '.join(args.path)}" if args.path else ""
    print(f"cross-author reviews or comments, distinct PRs{scope}:")
    for login, count in report["cross_author_reviews"].items():
        print(f"  {login}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
