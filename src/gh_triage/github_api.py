"""Fetches issues from the GitHub GraphQL API."""

import dataclasses
import json
import logging
import os
import pathlib
import urllib.error
import urllib.request
from typing import Any

from gh_triage import repo_ref

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://api.github.com/graphql"
REQUEST_TIMEOUT_S = 30
# GraphQL caps any connection at 100 nodes per request.
MAX_PAGE_SIZE = 100

_ISSUE_FIELDS = """
        id
        number
        title
        body
        url
        state
        stateReason
        createdAt
        updatedAt
        closedAt
        author { login }
        authorAssociation
        labels(first: 20) { nodes { name } }
        reactions { totalCount }
        thumbsUp: reactions(content: THUMBS_UP) { totalCount }
        comments(last: $comments) {
          totalCount
          nodes {
            id
            author { login }
            authorAssociation
            createdAt
            body
          }
        }
"""

ISSUES_QUERY = (
    """
query($owner: String!, $name: String!, $limit: Int!, $comments: Int!) {
  rateLimit { cost remaining resetAt }
  repository(owner: $owner, name: $name) {
    id
    nameWithOwner
    url
    issues(first: $limit, states: OPEN,
           orderBy: {field: UPDATED_AT, direction: DESC}) {
      totalCount
      nodes {"""
    + _ISSUE_FIELDS
    + """      }
    }
  }
}
"""
)

# Open and closed issues changed since a time, oldest change first, so a
# page that is cut short can be continued from its last issue.
CHANGED_ISSUES_QUERY = (
    """
query($owner: String!, $name: String!, $since: DateTime!, $comments: Int!,
      $pageSize: Int!, $after: String) {
  rateLimit { cost remaining resetAt }
  repository(owner: $owner, name: $name) {
    id
    nameWithOwner
    url
    openIssues: issues(states: OPEN) { totalCount }
    issues(first: $pageSize, after: $after, filterBy: {since: $since},
           orderBy: {field: UPDATED_AT, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {"""
    + _ISSUE_FIELDS
    + """      }
    }
  }
}
"""
)


VIEWER_QUERY = """
query {
  viewer { login }
  rateLimit { limit remaining }
}
"""


class GitHubError(RuntimeError):
    """Raised when GitHub can't return the requested data."""


@dataclasses.dataclass(frozen=True)
class Comment:
    """One issue comment, as fetched.

    Attributes:
        id: GitHub's global node ID for the comment.
        author: Login of the author, or None for a deleted account.
        author_association: The author's role, e.g. OWNER or NONE.
        created_at: ISO 8601 timestamp.
        body: Markdown text of the comment.
    """

    id: str
    author: str | None
    author_association: str
    created_at: str
    body: str


@dataclasses.dataclass(frozen=True)
class Issue:
    """One issue with the fields the tool stores.

    Attributes:
        id: GitHub's global node ID for the issue.
        number: The issue number within its repository.
        title: Issue title.
        body: Markdown text of the issue.
        url: Link to the issue on github.com.
        state: OPEN or CLOSED.
        state_reason: Why it was closed or reopened, if GitHub says.
        created_at: ISO 8601 timestamp.
        updated_at: ISO 8601 timestamp.
        closed_at: ISO 8601 timestamp, or None while open.
        author: Login of the author, or None for a deleted account.
        author_association: The author's role, e.g. OWNER or NONE.
        labels: Names of the repo labels on the issue.
        comment_count: Total comments on the issue.
        reaction_count: Total reactions of every kind.
        thumbs_up: Number of thumbs-up reactions.
        recent_comments: The last few comments, oldest first.
        raw: The issue exactly as GitHub returned it.
    """

    id: str
    number: int
    title: str
    body: str
    url: str
    state: str
    state_reason: str | None
    created_at: str
    updated_at: str
    closed_at: str | None
    author: str | None
    author_association: str
    labels: list[str]
    comment_count: int
    reaction_count: int
    thumbs_up: int
    recent_comments: list[Comment]
    raw: dict[str, Any]


@dataclasses.dataclass(frozen=True)
class Repository:
    """The repository as GitHub identifies it.

    Attributes:
        id: GitHub's global node ID, stable across renames and transfers.
        name_with_owner: Canonical ``owner/name`` with GitHub's casing.
        url: Link to the repository on github.com.
    """

    id: str
    name_with_owner: str
    url: str


@dataclasses.dataclass(frozen=True)
class FetchResult:
    """Everything one fetch returned.

    Attributes:
        repository: The repository the issues belong to.
        issues: The fetched issues, most recently updated first.
        total_open: Open issues in the repository, not just those fetched.
        rate_limit_cost: GraphQL points this request used.
        rate_limit_remaining: GraphQL points left in the current hour.
    """

    repository: Repository
    issues: list[Issue]
    total_open: int
    rate_limit_cost: int
    rate_limit_remaining: int


@dataclasses.dataclass(frozen=True)
class ChangedPage:
    """One page of issues changed since a time.

    Attributes:
        fetch: The page's repository and issues.
        end_cursor: Pass as ``after`` to get the next page.
        has_next_page: Whether GitHub has more changed issues.
    """

    fetch: FetchResult
    end_cursor: str | None
    has_next_page: bool


@dataclasses.dataclass(frozen=True)
class TokenStatus:
    """What GitHub reports about a working token.

    Attributes:
        login: The account the token belongs to.
        limit: GraphQL points allowed per hour.
        remaining: GraphQL points left in the current hour.
    """

    login: str
    limit: int
    remaining: int


def load_token(env_file: pathlib.Path | None = None) -> str:
    """Returns the GitHub token from the environment or a ``.env`` file.

    The ``GITHUB_TOKEN`` environment variable wins; otherwise the
    ``GITHUB_TOKEN=`` line in ``env_file`` is used.

    Args:
        env_file: Dotenv file to read as a fallback; defaults to ``.env``
            in the current folder.

    Returns:
        The token.

    Raises:
        GitHubError: No token was found.
    """
    env_file = env_file or pathlib.Path(".env")
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token and env_file.is_file():
        token = _read_env_value(env_file, "GITHUB_TOKEN")
    if not token:
        raise GitHubError(
            "No GitHub token found. Run 'triage setup' to get started, or "
            "set GITHUB_TOKEN in a .env file in this folder (see the README)."
        )
    return token


def _read_env_value(env_file: pathlib.Path, key: str) -> str:
    """Returns one value from a simple ``KEY=value`` dotenv file."""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.strip().partition("=")
        if separator and name.strip().removeprefix("export ") == key:
            return value.strip().strip("'\"")
    return ""


def fetch_open_issues(
    repo: repo_ref.RepoRef, limit: int, comments: int, token: str
) -> FetchResult:
    """Fetches the most recently updated open issues of a repository.

    Args:
        repo: The repository to read.
        limit: How many issues to fetch, 1 to 100.
        comments: How many of each issue's latest comments to include.
        token: A GitHub token that can read the repository.

    Returns:
        The repository, its issues and the request's rate-limit cost.

    Raises:
        ValueError: ``limit`` or ``comments`` is out of range.
        GitHubError: The request failed or the repository wasn't found.
    """
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be 1 to {MAX_PAGE_SIZE}, got {limit}")
    if not 0 <= comments <= MAX_PAGE_SIZE:
        raise ValueError(
            f"comments must be 0 to {MAX_PAGE_SIZE}, got {comments}"
        )
    variables = {
        "owner": repo.owner,
        "name": repo.name,
        "limit": limit,
        "comments": comments,
    }
    logger.info("Fetching up to %d open issues from %s", limit, repo.full_name)
    payload = _post_graphql(ISSUES_QUERY, variables, token)
    return parse_issues_response(payload, repo)


def fetch_changed_issues(
    repo: repo_ref.RepoRef, since: str, comments: int, token: str
) -> FetchResult:
    """Fetches every issue created, changed or closed since a time.

    Follows GitHub's pages until none are left, so nothing is skipped.

    Args:
        repo: The repository to read.
        since: ISO 8601 time; issues updated at or after it are returned.
        comments: How many of each issue's latest comments to include.
        token: A GitHub token that can read the repository.

    Returns:
        The changed issues, oldest change first, with the rate-limit cost
        summed over every request.

    Raises:
        ValueError: ``comments`` is out of range.
        GitHubError: A request failed or the repository wasn't found.
    """
    if not 0 <= comments <= MAX_PAGE_SIZE:
        raise ValueError(
            f"comments must be 0 to {MAX_PAGE_SIZE}, got {comments}"
        )
    logger.info("Fetching issues of %s changed since %s", repo.full_name, since)
    issues: dict[str, Issue] = {}
    cost = 0
    after: str | None = None
    while True:
        variables = {
            "owner": repo.owner,
            "name": repo.name,
            "since": since,
            "comments": comments,
            "pageSize": MAX_PAGE_SIZE,
            "after": after,
        }
        page = parse_changed_response(
            _post_graphql(CHANGED_ISSUES_QUERY, variables, token), repo
        )
        cost += page.fetch.rate_limit_cost
        # An issue edited while we page moves to the end; keep its newest copy.
        for issue in page.fetch.issues:
            issues[issue.id] = issue
        if not page.has_next_page:
            return dataclasses.replace(
                page.fetch,
                issues=list(issues.values()),
                rate_limit_cost=cost,
            )
        after = page.end_cursor


def _post_graphql(
    query: str, variables: dict[str, Any], token: str
) -> dict[str, Any]:
    """Sends one GraphQL request and returns the decoded JSON body."""
    request = urllib.request.Request(
        GRAPHQL_URL,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "gh-issues-organizer",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(
            request, timeout=REQUEST_TIMEOUT_S
        ) as response:
            body: dict[str, Any] = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise GitHubError(
                "GitHub rejected the token (HTTP 401). Check GITHUB_TOKEN "
                "in .env and that the token hasn't expired."
            ) from error
        raise GitHubError(
            f"GitHub returned HTTP {error.code} {error.reason}"
        ) from error
    except urllib.error.URLError as error:
        raise GitHubError(f"Couldn't reach GitHub: {error.reason}") from error
    return body


def parse_issues_response(
    payload: dict[str, Any], repo: repo_ref.RepoRef
) -> FetchResult:
    """Converts a GraphQL response body into a FetchResult.

    Args:
        payload: The decoded JSON body of an ``ISSUES_QUERY`` response.
        repo: The repository that was requested, for error messages.

    Returns:
        The parsed result.

    Raises:
        GitHubError: The response holds errors or no repository.
    """
    data, repository = _checked_repository(payload, repo)
    issues_connection = repository["issues"]
    return _fetch_result(
        data,
        repository,
        issues_connection["nodes"],
        issues_connection["totalCount"],
    )


def parse_changed_response(
    payload: dict[str, Any], repo: repo_ref.RepoRef
) -> ChangedPage:
    """Converts a ``CHANGED_ISSUES_QUERY`` response body into a page.

    Args:
        payload: The decoded JSON body.
        repo: The repository that was requested, for error messages.

    Returns:
        The parsed page.

    Raises:
        GitHubError: The response holds errors or no repository.
    """
    data, repository = _checked_repository(payload, repo)
    issues_connection = repository["issues"]
    page_info = issues_connection["pageInfo"]
    return ChangedPage(
        fetch=_fetch_result(
            data,
            repository,
            issues_connection["nodes"],
            repository["openIssues"]["totalCount"],
        ),
        end_cursor=page_info["endCursor"],
        has_next_page=page_info["hasNextPage"],
    )


def _checked_repository(
    payload: dict[str, Any], repo: repo_ref.RepoRef
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns a response's data and repository, raising on any error."""
    errors = payload.get("errors") or []
    if any(error.get("type") == "NOT_FOUND" for error in errors):
        raise GitHubError(
            f"Repository {repo.full_name} not found, or the token can't "
            "read it."
        )
    if errors:
        messages = "; ".join(str(error.get("message")) for error in errors)
        raise GitHubError(f"GitHub returned errors: {messages}")

    data = payload.get("data") or {}
    repository = data.get("repository")
    if repository is None:
        raise GitHubError(f"GitHub returned no data for {repo.full_name}")
    return data, repository


def _fetch_result(
    data: dict[str, Any],
    repository: dict[str, Any],
    nodes: list[dict[str, Any]],
    total_open: int,
) -> FetchResult:
    """Builds a FetchResult from the parts both queries share."""
    rate_limit = data["rateLimit"]
    return FetchResult(
        repository=Repository(
            id=repository["id"],
            name_with_owner=repository["nameWithOwner"],
            url=repository["url"],
        ),
        issues=[_parse_issue(node) for node in nodes],
        total_open=total_open,
        rate_limit_cost=rate_limit["cost"],
        rate_limit_remaining=rate_limit["remaining"],
    )


def _parse_issue(node: dict[str, Any]) -> Issue:
    """Converts one issue node from the GraphQL response."""
    comments = node["comments"]
    return Issue(
        id=node["id"],
        number=node["number"],
        title=node["title"],
        body=node["body"],
        url=node["url"],
        state=node["state"],
        state_reason=node["stateReason"],
        created_at=node["createdAt"],
        updated_at=node["updatedAt"],
        closed_at=node["closedAt"],
        author=_login(node["author"]),
        author_association=node["authorAssociation"],
        labels=[label["name"] for label in node["labels"]["nodes"]],
        comment_count=comments["totalCount"],
        reaction_count=node["reactions"]["totalCount"],
        thumbs_up=node["thumbsUp"]["totalCount"],
        recent_comments=[
            Comment(
                id=comment["id"],
                author=_login(comment["author"]),
                author_association=comment["authorAssociation"],
                created_at=comment["createdAt"],
                body=comment["body"],
            )
            for comment in comments["nodes"]
        ],
        raw=node,
    )


def _login(author: dict[str, Any] | None) -> str | None:
    """Returns an author's login; GitHub sends null for deleted accounts."""
    return author["login"] if author else None


def check_token(token: str) -> TokenStatus:
    """Confirms a token works with one cheap GraphQL request.

    Args:
        token: The GitHub token to check.

    Returns:
        The token's account and rate limit.

    Raises:
        GitHubError: GitHub rejected the token or couldn't be reached.
    """
    return parse_viewer_response(_post_graphql(VIEWER_QUERY, {}, token))


def parse_viewer_response(payload: dict[str, Any]) -> TokenStatus:
    """Converts a ``VIEWER_QUERY`` response body into a TokenStatus.

    Args:
        payload: The decoded JSON body.

    Returns:
        The parsed status.

    Raises:
        GitHubError: The response holds errors.
    """
    errors = payload.get("errors") or []
    if errors:
        messages = "; ".join(str(error.get("message")) for error in errors)
        raise GitHubError(f"GitHub returned errors: {messages}")
    data = payload["data"]
    return TokenStatus(
        login=data["viewer"]["login"],
        limit=data["rateLimit"]["limit"],
        remaining=data["rateLimit"]["remaining"],
    )
