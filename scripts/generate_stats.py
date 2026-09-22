"""
generate_stats.py
------------------
Builds the "neofetch" terminal stat card AND the dynamic project sections
that sit in aenoshrajora/aenoshrajora's README. Pulls live data from the
GitHub GraphQL API:

  - account age, repos, stars, followers, total commits, lines of code
    -> stamped into assets/terminal_dark.svg and assets/terminal_light.svg
  - most recently pushed-to public repos, top public repos by star count,
    and most recently starred repos
    -> rendered as markdown and spliced into README.md between HTML
       comment markers, so the "Latest Public Work" / "Most Starred" /
       "Recently Exploring" sections never need to be hand-edited again

The stat-card half is based on the well-known LOC-counter approach
popularized by Andrew6rant's github-readme-stats profile repo, generalized
for this account and trimmed of anything repo-specific (no hardcoded user
IDs, no archived-repo patching).

Requires env vars:
    ACCESS_TOKEN  - fine-grained GitHub PAT (read:user, read:org, repo read scopes)
    USER_NAME     - GitHub username to report on (defaults to aenoshrajora)

Run locally:
    ACCESS_TOKEN=ghp_xxx USER_NAME=aenoshrajora python scripts/generate_stats.py
"""
import datetime
import hashlib
import os
import re
import time

import requests
from dateutil import relativedelta
from lxml import etree

HEADERS = {"authorization": "token " + os.environ["ACCESS_TOKEN"]}
USER_NAME = os.environ.get("USER_NAME", "aenoshrajora")
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "cache")
README_PATH = os.path.join(os.path.dirname(__file__), "..", "README.md")
SVG_FILES = [
    os.path.join(os.path.dirname(__file__), "..", "assets", "terminal_dark.svg"),
    os.path.join(os.path.dirname(__file__), "..", "assets", "terminal_light.svg"),
]
QUERY_COUNT = {}


def query_count(name):
    QUERY_COUNT[name] = QUERY_COUNT.get(name, 0) + 1


def gql(name, query, variables):
    query_count(name)
    r = requests.post(
        "https://api.github.com/graphql",
        json={"query": query, "variables": variables},
        headers=HEADERS,
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"{name} failed [{r.status_code}]: {r.text}")
    payload = r.json()
    if "errors" in payload:
        raise RuntimeError(f"{name} returned errors: {payload['errors']}")
    return payload["data"]


def user_getter(username):
    data = gql(
        "user_getter",
        """
        query($login: String!) {
            user(login: $login) { id createdAt }
        }""",
        {"login": username},
    )
    created_at = datetime.datetime.strptime(
        data["user"]["createdAt"], "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=datetime.timezone.utc)
    return data["user"]["id"], created_at


def follower_count(username):
    data = gql(
        "follower_getter",
        """
        query($login: String!) {
            user(login: $login) { followers { totalCount } }
        }""",
        {"login": username},
    )
    return data["user"]["followers"]["totalCount"]


def total_commits(username, created_at):
    """contributionsCollection only accepts <=1yr windows, so walk year by year."""
    total = 0
    start = created_at
    now = datetime.datetime.now(datetime.timezone.utc)
    while start < now:
        end = min(start + relativedelta.relativedelta(years=1), now)
        data = gql(
            "graph_commits",
            """
            query($login: String!, $from: DateTime!, $to: DateTime!) {
                user(login: $login) {
                    contributionsCollection(from: $from, to: $to) {
                        contributionCalendar { totalContributions }
                    }
                }
            }""",
            {
                "login": username,
                "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )
        total += data["user"]["contributionsCollection"]["contributionCalendar"][
            "totalContributions"
        ]
        start = end
    return total


def repos_and_stars(username, affiliations, cursor=None, repos=None):
    repos = repos if repos is not None else []
    data = gql(
        "graph_repos_stars",
        """
        query($login: String!, $cursor: String, $aff: [RepositoryAffiliation]) {
            user(login: $login) {
                repositories(first: 100, after: $cursor, ownerAffiliations: $aff) {
                    totalCount
                    edges {
                        node { nameWithOwner stargazers { totalCount } }
                    }
                    pageInfo { endCursor hasNextPage }
                }
            }
        }""",
        {"login": username, "cursor": cursor, "aff": affiliations},
    )
    block = data["user"]["repositories"]
    repos += block["edges"]
    if block["pageInfo"]["hasNextPage"]:
        return repos_and_stars(username, affiliations, block["pageInfo"]["endCursor"], repos)
    stars = sum(r["node"]["stargazers"]["totalCount"] for r in repos)
    return block["totalCount"], stars, repos


def recursive_loc(owner, repo, owner_id, cursor=None, add=0, delete=0, mine=0):
    data = gql(
        "recursive_loc",
        """
        query($owner: String!, $repo: String!, $cursor: String) {
            repository(owner: $owner, name: $repo) {
                defaultBranchRef {
                    target {
                        ... on Commit {
                            history(first: 100, after: $cursor) {
                                edges {
                                    node { additions deletions author { user { id } } }
                                }
                                pageInfo { endCursor hasNextPage }
                            }
                        }
                    }
                }
            }
        }""",
        {"owner": owner, "repo": repo, "cursor": cursor},
    )
    ref = data["repository"]["defaultBranchRef"]
    if ref is None:
        return add, delete, mine
    history = ref["target"]["history"]
    for edge in history["edges"]:
        node = edge["node"]
        if node["author"]["user"] and node["author"]["user"]["id"] == owner_id:
            mine += 1
            add += node["additions"]
            delete += node["deletions"]
    if history["pageInfo"]["hasNextPage"]:
        return recursive_loc(owner, repo, owner_id, history["pageInfo"]["endCursor"], add, delete, mine)
    return add, delete, mine


def loc_totals(username, owner_id, repo_edges):
    """Cached per-repo LOC totals, keyed by nameWithOwner hash + commit count."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, hashlib.sha256(username.encode()).hexdigest() + ".txt")
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            for line in f:
                parts = line.split()
                if len(parts) == 5:
                    cache[parts[0]] = parts[1:]

    add_total, del_total = 0, 0
    new_cache_lines = []
    for edge in repo_edges:
        name = edge["node"]["nameWithOwner"]
        key = hashlib.sha256(name.encode()).hexdigest()
        owner, repo = name.split("/")
        add, delete, mine = recursive_loc(owner, repo, owner_id)
        add_total += add
        del_total += delete
        new_cache_lines.append(f"{key} {add} {delete} {mine}\n")

    with open(cache_path, "w") as f:
        f.writelines(new_cache_lines)
    return add_total, del_total


def fmt(n):
    return f"{n:,}"


def fmt_uptime(created_at):
    d = relativedelta.relativedelta(datetime.datetime.now(datetime.timezone.utc), created_at)
    parts = []
    if d.years:
        parts.append(f"{d.years}y")
    if d.months:
        parts.append(f"{d.months}m")
    parts.append(f"{d.days}d")
    return " ".join(parts)


def stamp(root, element_id, value):
    el = root.find(f".//*[@id='{element_id}']")
    if el is not None:
        el.text = str(value)


# ---------------------------------------------------------------------------
# Terminal card: build the whole "typed" body fresh every run, one <animate>
# per character, each timed to begin right after the previous one finishes.
# Because this runs AFTER the stats are known, there's no guessing about how
# long "1,946" or "+198,010" will end up being - the timeline is exact.
# ---------------------------------------------------------------------------

CHAR_SPEED = 0.022   # seconds between each character appearing
LINE_GAP = 0.12      # extra pause when a new line starts (like pressing Enter)
START_DELAY = 0.3    # pause before typing begins at all
CURSOR_GAP = 0.15    # pause after the last character before the cursor starts blinking
BLINK_PERIOD = 1.1   # seconds per cursor blink cycle

_XML_ESCAPE = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})


def _char_tspan(ch, cls, begin, x=None, font_size=None):
    attrs = f' class="{cls}" opacity="0"'
    if x is not None:
        attrs += f' x="{x}"'
    if font_size is not None:
        attrs += f' font-size="{font_size}"'
    safe_ch = ch.translate(_XML_ESCAPE)
    return (
        f'<tspan{attrs}>{safe_ch}'
        f'<animate attributeName="opacity" from="0" to="1" '
        f'begin="{begin:.3f}s" dur="0.01s" fill="freeze"/></tspan>'
    )


def _typed_line(y, runs, t, font_size=15):
    """runs: list of (text, css_class, x_or_None, font_size_or_None)."""
    parts = [f'<text x="24" y="{y}" font-size="{font_size}">']
    for text, cls, x, fs in runs:
        for i, ch in enumerate(text):
            parts.append(_char_tspan(ch, cls, t, x=x if i == 0 else None, font_size=fs))
            t += CHAR_SPEED
    parts.append("</text>")
    return "".join(parts), t


def build_typed_lines(stats):
    """Returns (svg_fragment_string, ) for the whole card body, stats already resolved."""
    lines = [
        (64, [("guest@aenosh", "prompt", None, None), (" ~ % ", "txt", None, None), ("whoami", "accent", None, None)], None),
        (88, [("Aenosh Rajora — Founder @ The Cyber Ledger", "title", None, None)], 16),
        (108, [("Offensive Security Engineer · Red Teamer", "txt", None, None)], None),
        (140, [("guest@aenosh", "prompt", None, None), (" ~ % ", "txt", None, None), ("neofetch --github", "accent", None, None)], None),
        (166, [("OS", "label", None, None), ("Red Team / Blue Team Hybrid", "txt", 140, None)], None),
        (188, [("Uptime", "label", None, None), (stats["stat_uptime"], "txt", 140, None)], None),
        (210, [("Repos", "label", None, None), (stats["stat_repos"], "txt", 140, None)], None),
        (232, [("Stars", "label", None, None), (stats["stat_stars"], "txt", 140, None)], None),
        (254, [("Commits", "label", None, None), (stats["stat_commits"], "txt", 140, None)], None),
        (276, [
            ("Lines of Code", "label", None, None),
            (stats["stat_loc"], "txt", 140, None),
            ("  (", "dim", None, 12),
            (stats["stat_loc_add"], "dim", None, 12),
            (" / ", "dim", None, 12),
            (stats["stat_loc_del"], "dim", None, 12),
            (")", "dim", None, 12),
        ], None),
        (298, [("Followers", "label", None, None), (stats["stat_followers"], "txt", 140, None)], None),
        (336, [("guest@aenosh", "prompt", None, None), (" ~ % ", "txt", None, None)], None),
    ]

    t = START_DELAY
    fragments = []
    for y, runs, fs in lines:
        frag, t = _typed_line(y, runs, t, font_size=fs or 15)
        fragments.append(frag)
        t += LINE_GAP

    cursor_begin = t + CURSOR_GAP
    cursor = (
        '<tspan class="cursor" opacity="0">'
        '&#9608;'
        f'<animate attributeName="opacity" begin="{cursor_begin:.3f}s" dur="0.001s" to="1" fill="freeze"/>'
        f'<animate attributeName="opacity" begin="{cursor_begin:.3f}s" dur="{BLINK_PERIOD}s" '
        'values="1;1;0;0;1" keyTimes="0;0.001;0.5;0.501;1" repeatCount="indefinite"/>'
        '</tspan>'
    )
    # append cursor as a continuation of the final prompt line
    fragments[-1] = fragments[-1].replace("</text>", cursor + "</text>")

    return "".join(fragments)


def write_svg(path, stats):
    tree = etree.parse(path)
    root = tree.getroot()
    placeholder = root.find(".//*[@id='typed-lines']")
    if placeholder is None:
        return
    fragment = build_typed_lines(stats)
    ns = root.nsmap.get(None, "http://www.w3.org/2000/svg")
    wrapper = etree.fromstring(
        f'<g xmlns="{ns}" id="typed-lines" xml:space="preserve" '
        f'font-size="{placeholder.get("font-size", "15")}">{fragment}</g>'.encode("utf-8")
    )
    placeholder.getparent().replace(placeholder, wrapper)
    tree.write(path, encoding="utf-8", xml_declaration=True)


# ---------------------------------------------------------------------------
# Dynamic README sections: latest work, top repos, recently starred
# ---------------------------------------------------------------------------

REPO_FIELDS = """
    name
    nameWithOwner
    description
    url
    stargazerCount
    forkCount
    pushedAt
    primaryLanguage { name }
"""


def latest_public_repos(username, n=4):
    data = gql(
        "latest_repos",
        f"""
        query($login: String!, $n: Int!) {{
            user(login: $login) {{
                repositories(
                    first: $n, privacy: PUBLIC, isFork: false,
                    orderBy: {{field: PUSHED_AT, direction: DESC}}
                ) {{ nodes {{ {REPO_FIELDS} }} }}
            }}
        }}""",
        {"login": username, "n": n},
    )
    return data["user"]["repositories"]["nodes"]


def top_starred_repos(username, n=4):
    data = gql(
        "top_repos",
        f"""
        query($login: String!, $n: Int!) {{
            user(login: $login) {{
                repositories(
                    first: $n, privacy: PUBLIC, isFork: false,
                    orderBy: {{field: STARGAZERS, direction: DESC}}
                ) {{ nodes {{ {REPO_FIELDS} }} }}
            }}
        }}""",
        {"login": username, "n": n},
    )
    return data["user"]["repositories"]["nodes"]


def recently_starred_repos(username, n=6):
    data = gql(
        "starred_repos",
        f"""
        query($login: String!, $n: Int!) {{
            user(login: $login) {{
                starredRepositories(
                    first: $n, orderBy: {{field: STARRED_AT, direction: DESC}}
                ) {{ nodes {{ {REPO_FIELDS} owner {{ login }} }} }}
            }}
        }}""",
        {"login": username, "n": n},
    )
    return data["user"]["starredRepositories"]["nodes"]


def days_ago(iso_ts):
    then = datetime.datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    delta = (datetime.datetime.now(datetime.timezone.utc) - then).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "yesterday"
    if delta < 30:
        return f"{delta}d ago"
    if delta < 365:
        return f"{delta // 30}mo ago"
    return f"{delta // 365}y ago"


def repo_table(repos, own=True, empty_msg="Nothing public here yet."):
    """Markdown table: repo name (linked), description, language, stars, updated."""
    if not repos:
        return f"_{empty_msg}_"
    header = "| Repo | Description | Language | ⭐ | Updated |\n|---|---|---|---|---|"
    rows = [header]
    for r in repos:
        label = r["name"] if own else r["nameWithOwner"]
        desc = (r["description"] or "").replace("|", "-").strip()
        if len(desc) > 90:
            desc = desc[:87] + "..."
        lang = r["primaryLanguage"]["name"] if r["primaryLanguage"] else "-"
        rows.append(
            f"| [{label}]({r['url']}) | {desc} | {lang} | {r['stargazerCount']} | {days_ago(r['pushedAt'])} |"
        )
    return "\n".join(rows)


def splice_section(readme_text, marker, content):
    start_tag = f"<!-- {marker}:START -->"
    end_tag = f"<!-- {marker}:END -->"
    pattern = re.compile(re.escape(start_tag) + r".*?" + re.escape(end_tag), re.DOTALL)
    replacement = f"{start_tag}\n{content}\n{end_tag}"
    if not pattern.search(readme_text):
        return readme_text  # marker not present, leave README untouched
    return pattern.sub(replacement, readme_text)


def update_readme_projects(username):
    with open(README_PATH, "r", encoding="utf-8") as f:
        readme = f.read()

    latest_md = repo_table(latest_public_repos(username), own=True)
    top_md = repo_table(top_starred_repos(username), own=True)
    starred_md = repo_table(
        recently_starred_repos(username), own=False,
        empty_msg="Nothing starred publicly yet.",
    )

    readme = splice_section(readme, "LATEST-PROJECTS", latest_md)
    readme = splice_section(readme, "TOP-REPOS", top_md)
    readme = splice_section(readme, "STARRED-REPOS", starred_md)

    with open(README_PATH, "w", encoding="utf-8") as f:
        f.write(readme)


def main():
    start = time.perf_counter()
    owner_id, created_at = user_getter(USER_NAME)
    followers = follower_count(USER_NAME)
    commits = total_commits(USER_NAME, created_at)
    repo_count, stars, own_repos = repos_and_stars(USER_NAME, ["OWNER"])
    _, _, all_repos = repos_and_stars(USER_NAME, ["OWNER", "COLLABORATOR", "ORGANIZATION_MEMBER"])
    loc_add, loc_del = loc_totals(USER_NAME, owner_id, all_repos)

    stats = {
        "stat_uptime": fmt_uptime(created_at),
        "stat_repos": fmt(repo_count),
        "stat_stars": fmt(stars),
        "stat_commits": fmt(commits),
        "stat_loc": fmt(loc_add - loc_del),
        "stat_loc_add": f"+{fmt(loc_add)}",
        "stat_loc_del": f"-{fmt(loc_del)}",
        "stat_followers": fmt(followers),
    }

    for svg_path in SVG_FILES:
        write_svg(svg_path, stats)

    update_readme_projects(USER_NAME)

    print(f"Updated {len(SVG_FILES)} SVGs + README project sections in {time.perf_counter() - start:.2f}s")
    print("GraphQL calls:", QUERY_COUNT)


if __name__ == "__main__":
    main()
