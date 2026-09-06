"""Resolve Linear routing with a hidden API-key prompt; never save the key.

Read-only by default. --create-missing-labels permits only the four feedback
labels to be created in the chosen team. Does not create issues or deploy.
"""

import argparse
import getpass
import json
import logging
import re
import sys
from urllib.parse import urlsplit

from sabermetrics.ui.feedback_linear import (
    LinearConfig,
    LinearFeedbackClient,
    LinearUnavailable,
)

PROJECT_URL = (
    "https://linear.app/dylnmtthws/project/cedh-deck-dashboard-f33a94f793f7/overview"
)
LABEL_NAMES = ("user-feedback", "bug", "ux", "suggestion")


def routing(client, project_ref, team_id=None, create_labels=False):
    project = client.graphql(
        "query FeedbackProject($id: String!) { project(id: $id) { id name teams { nodes { id name } } } }",
        {"id": project_ref},
    )["project"]
    teams = project["teams"]["nodes"]
    if team_id:
        teams = [team for team in teams if team["id"] == team_id]
    if len(teams) != 1:
        raise ValueError(
            "Choose exactly one project team with --team-id. Project teams: "
            + ", ".join(
                f"{team['name']} ({team['id']})" for team in project["teams"]["nodes"]
            )
        )
    team = teams[0]
    states = client.graphql(
        "query FeedbackTeam($id: String!) { team(id: $id) { states { nodes { id name type } } } }",
        {"id": team["id"]},
    )["team"]["states"]["nodes"]
    triage = [state for state in states if state["type"] == "triage"]
    if len(triage) != 1:
        raise ValueError(
            "Enable Triage in this team's Linear workflow, then rerun setup. No fallback state was selected."
        )
    all_labels = []
    cursor = None
    while True:
        connection = client.graphql(
            "query FeedbackLabels($after: String) { issueLabels(first: 100, after: $after) { nodes { id name isGroup team { id } } pageInfo { hasNextPage endCursor } } }",
            {"after": cursor},
        )["issueLabels"]
        all_labels.extend(connection["nodes"])
        if not connection["pageInfo"]["hasNextPage"]:
            break
        next_cursor = connection["pageInfo"]["endCursor"]
        if next_cursor == cursor or len(all_labels) >= 5000:
            raise ValueError(
                "Label discovery exceeded its bounded scan; configure label IDs explicitly."
            )
        cursor = next_cursor
    labels = {}
    missing = []
    for name in LABEL_NAMES:
        matches = [
            label
            for label in all_labels
            if label["name"].lower() == name
            and not label["isGroup"]
            and (label["team"] is None or label["team"]["id"] == team["id"])
        ]
        specific = [label for label in matches if label["team"] is not None]
        matches = specific or matches
        if len(matches) > 1:
            raise ValueError(f"Ambiguous label {name}; configure its ID explicitly.")
        if matches:
            labels[name] = matches[0]["id"]
        else:
            missing.append(name)
    if missing and not create_labels:
        raise ValueError(
            "Missing labels: "
            + ", ".join(missing)
            + ". Create them in Linear or rerun with --create-missing-labels using a key with Write permission."
        )
    for name in missing:
        payload = client.graphql(
            "mutation FeedbackLabel($input: IssueLabelCreateInput!) { issueLabelCreate(input: $input) { success issueLabel { id } } }",
            {"input": {"name": name, "teamId": team["id"]}},
        )["issueLabelCreate"]
        if payload["success"] is not True:
            raise LinearUnavailable
        labels[name] = payload["issueLabel"]["id"]
    return {
        "LINEAR_FEEDBACK_ENABLED": "true",
        "LINEAR_TEAM_ID": team["id"],
        "LINEAR_PROJECT_ID": project["id"],
        "LINEAR_TRIAGE_STATE_ID": triage[0]["id"],
        "LINEAR_LABEL_IDS": json.dumps(labels, sort_keys=True),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-url", default=PROJECT_URL)
    parser.add_argument("--team-id")
    parser.add_argument("--create-missing-labels", action="store_true")
    args = parser.parse_args()
    parsed = urlsplit(args.project_url)
    match = re.fullmatch(
        r"/[^/]+/project/[^/]+-([a-f0-9]{12})(?:/overview)?/?", parsed.path
    )
    if (
        parsed.scheme != "https"
        or parsed.netloc != "linear.app"
        or not match
        or parsed.query
        or parsed.fragment
    ):
        parser.error("Provide a Linear project overview URL.")
    if not sys.stdin.isatty():
        parser.error("Run in an interactive terminal for the hidden API-key prompt.")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    key = getpass.getpass("Linear personal API key (hidden, not saved): ").strip()
    if not key or any(c.isspace() for c in key):
        parser.error("An API key without whitespace is required.")
    client = LinearFeedbackClient(LinearConfig(key, "", "", "", {}))
    try:
        settings = routing(client, match[1], args.team_id, args.create_missing_labels)
    except ValueError as error:
        parser.exit(1, str(error) + "\n")
    except (LinearUnavailable, KeyError, TypeError):
        parser.exit(
            1,
            "Linear setup could not be confirmed. Check key permissions and project access. Provider details and credentials were not printed.\n",
        )
    print("Non-secret routing settings (safe to share):")
    print(json.dumps(settings, indent=2))


if __name__ == "__main__":
    main()
