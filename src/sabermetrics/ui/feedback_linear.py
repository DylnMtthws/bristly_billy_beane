"""Small server-only Linear adapter. Provider payloads never enter logs."""

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from sabermetrics.ui.feedback_images import FeedbackImage

CATEGORIES = {"bug": "Bug", "ux": "UX", "suggestion": "Suggestion"}
GRAPHQL_URL = "https://api.linear.app/graphql"


class _HideSignedUploads(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # HTTPX's INFO request log includes the full signed PUT URL.
        return "storage.googleapis.com" not in record.getMessage()


_upload_log_filter = _HideSignedUploads()


class LinearUnavailable(Exception):
    """Only a generic message is safe to return or log."""


@dataclass(frozen=True)
class LinearConfig:
    api_key: str = field(repr=False)
    team_id: str
    project_id: str
    state_id: str
    labels: dict[str, str]

    @classmethod
    def from_env(cls):
        enabled = os.environ.get("LINEAR_FEEDBACK_ENABLED", "false").lower()
        if enabled not in {"true", "false"}:
            raise ValueError("LINEAR_FEEDBACK_ENABLED must be true or false")
        if enabled == "false":
            return None
        names = (
            "LINEAR_API_KEY",
            "LINEAR_TEAM_ID",
            "LINEAR_PROJECT_ID",
            "LINEAR_TRIAGE_STATE_ID",
            "LINEAR_LABEL_IDS",
        )
        values = [os.environ.get(n, "").strip() for n in names]
        if not all(values):
            raise ValueError("Enabled feedback requires " + ", ".join(names))
        try:
            labels = json.loads(values[4])
            if not isinstance(labels, dict) or set(labels) != {
                "user-feedback",
                *CATEGORIES,
            }:
                raise ValueError
            for identifier in [*values[1:4], *labels.values()]:
                uuid.UUID(identifier)
        except (ValueError, TypeError, AttributeError):
            raise ValueError(
                "Linear routing settings must contain valid UUIDs and all four labels"
            ) from None
        if any(c.isspace() for c in values[0]):
            raise ValueError("LINEAR_API_KEY must not contain whitespace")
        return cls(values[0], *values[1:4], labels)


def private_asset(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "uploads.linear.app"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or not re.fullmatch(r"/[A-Za-z0-9/_.-]+", parsed.path)
    ):
        raise LinearUnavailable
    return url


class LinearFeedbackClient:
    def __init__(self, config: LinearConfig):
        self.config = config
        logging.getLogger("httpx").addFilter(_upload_log_filter)

    def graphql(self, query: str, variables: dict) -> dict:
        try:
            with httpx.Client(
                timeout=10, follow_redirects=False, trust_env=False
            ) as client:
                response = client.post(
                    GRAPHQL_URL,
                    headers={"Authorization": self.config.api_key},
                    json={"query": query, "variables": variables},
                )
            response.raise_for_status()
            payload = response.json()
            if (
                not isinstance(payload, dict)
                or payload.get("errors")
                or not isinstance(payload.get("data"), dict)
            ):
                raise LinearUnavailable
            data: dict = payload["data"]
            return data
        except (httpx.HTTPError, ValueError):
            raise LinearUnavailable from None

    def exists(self, issue_id: str) -> bool:
        data = self.graphql(
            "query FeedbackReceipt($id: ID!) { issues(first: 1, includeArchived: true, filter: {id: {eq: $id}}) { nodes { id } } }",
            {"id": issue_id},
        )
        try:
            nodes = data["issues"]["nodes"]
            if not isinstance(nodes, list):
                raise LinearUnavailable
            return any(node["id"] == issue_id for node in nodes)
        except (KeyError, TypeError):
            raise LinearUnavailable from None

    def upload(self, screenshot: FeedbackImage, issue_id: str) -> str:
        data = self.graphql(
            "mutation FeedbackUpload($type: String!, $name: String!, $size: Int!) { fileUpload(contentType: $type, filename: $name, size: $size, makePublic: false) { success uploadFile { uploadUrl assetUrl headers { key value } } } }",
            {
                "type": screenshot.content_type,
                "name": f"feedback-{issue_id}.{screenshot.extension}",
                "size": len(screenshot.content),
            },
        )
        try:
            payload = data["fileUpload"]
            if payload["success"] is not True:
                raise LinearUnavailable
            upload = payload["uploadFile"]
            asset = private_asset(upload["assetUrl"])
            destination = urlsplit(upload["uploadUrl"])
            # Only signed storage URLs returned by Linear; never a browser URL.
            host = destination.hostname or ""
            if (
                destination.scheme != "https"
                or destination.username
                or destination.password
                or destination.port not in (None, 443)
                or not (
                    host == "storage.googleapis.com"
                    or host.endswith(".storage.googleapis.com")
                )
            ):
                raise LinearUnavailable
            headers = {item["key"]: item["value"] for item in upload["headers"]}
            if any(k.lower() in {"authorization", "cookie", "host"} for k in headers):
                raise LinearUnavailable
            headers["Content-Type"] = screenshot.content_type
            with httpx.Client(
                timeout=10, follow_redirects=False, trust_env=False
            ) as client:
                response = client.put(
                    upload["uploadUrl"], headers=headers, content=screenshot.content
                )
                response.raise_for_status()
            return asset
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            raise LinearUnavailable from None

    def create(
        self, issue_id: str, category: str, title: str, description: str
    ) -> None:
        data = self.graphql(
            "mutation FeedbackIssue($input: IssueCreateInput!) { issueCreate(input: $input) { success issue { id } } }",
            {
                "input": {
                    "id": issue_id,
                    "title": title,
                    "description": description,
                    "teamId": self.config.team_id,
                    "projectId": self.config.project_id,
                    "stateId": self.config.state_id,
                    "priority": 0,
                    "labelIds": [
                        self.config.labels["user-feedback"],
                        self.config.labels[category],
                    ],
                }
            },
        )
        try:
            result = data["issueCreate"]
            if result["success"] is not True or result["issue"]["id"] != issue_id:
                raise LinearUnavailable
        except (KeyError, TypeError):
            raise LinearUnavailable from None
