"""Workspace routing discovery must not silently choose a different team/state."""

import pytest

from scripts.configure_linear_feedback import routing


class SetupClient:
    def __init__(self):
        self.teams = [{"id": "team-1", "name": "Deck Lab"}]
        self.states = [{"id": "state-1", "name": "Triage", "type": "triage"}]
        self.labels = [
            {"id": "bug-existing", "name": "Bug", "isGroup": False, "team": None}
        ]
        self.created = []

    def graphql(self, query, variables):
        if "FeedbackProject" in query:
            return {
                "project": {
                    "id": "project-1",
                    "name": "Deck Dashboard",
                    "teams": {"nodes": self.teams},
                }
            }
        if "FeedbackTeam" in query:
            return {"team": {"states": {"nodes": self.states}}}
        if "FeedbackLabels" in query:
            return {
                "issueLabels": {
                    "nodes": self.labels,
                    "pageInfo": {"hasNextPage": False},
                }
            }
        self.created.append(variables["input"])
        return {
            "issueLabelCreate": {
                "success": True,
                "issueLabel": {"id": "created-" + variables["input"]["name"]},
            }
        }


def test_setup_read_only_by_default():
    client = SetupClient()
    with pytest.raises(ValueError, match="Missing labels"):
        routing(client, "project-short-id")
    assert not client.created


def test_setup_reuses_existing_labels_and_creates_only_missing():
    client = SetupClient()
    settings = routing(client, "project-short-id", create_labels=True)
    assert [label["name"] for label in client.created] == [
        "user-feedback",
        "ux",
        "suggestion",
    ]
    assert all(label["teamId"] == "team-1" for label in client.created)
    assert settings["LINEAR_TRIAGE_STATE_ID"] == "state-1"
    assert "bug-existing" in settings["LINEAR_LABEL_IDS"]
    assert "LINEAR_API_KEY" not in settings


def test_setup_requires_team_choice_for_multi_team_project():
    client = SetupClient()
    client.teams.append({"id": "team-2", "name": "Simulator"})
    with pytest.raises(ValueError, match="Choose exactly one"):
        routing(client, "project-short-id", create_labels=True)
    assert not client.created
    settings = routing(client, "project-short-id", "team-2", create_labels=True)
    assert settings["LINEAR_TEAM_ID"] == "team-2"


def test_setup_does_not_fall_back_to_backlog():
    client = SetupClient()
    client.states = [{"id": "backlog", "name": "Backlog", "type": "backlog"}]
    with pytest.raises(ValueError, match="Enable Triage"):
        routing(client, "project-short-id", create_labels=True)
    assert not client.created
