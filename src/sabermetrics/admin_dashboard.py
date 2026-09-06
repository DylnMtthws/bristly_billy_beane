"""Deck Lab admin overview and responsive user read models."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sabermetrics import db


class DeckLabAdminRepo:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.isoformat(timespec="seconds")

    def overview(self, admin_id: str) -> dict[str, Any]:
        now = datetime.now()
        with db.connect(self.db_path) as conn:
            visit = conn.execute(
                "SELECT last_overview_at FROM admin_visits WHERE user_id=?", (admin_id,)
            ).fetchone()
            first_visit = visit is None
            since = (
                datetime.fromisoformat(visit["last_overview_at"])
                if visit
                else now - timedelta(days=7)
            )
            span = max(now - since, timedelta(minutes=1))
            prior = since - span

            def event_rows(start: datetime, end: datetime) -> list[dict[str, Any]]:
                rows = conn.execute(
                    "SELECT actor_id,action,metadata_json,created_at,subject_id "
                    "FROM activity_events WHERE created_at>=? AND created_at<?",
                    (self._iso(start), self._iso(end)),
                ).fetchall()
                return [dict(row) for row in rows]

            current_events = event_rows(since, now + timedelta(seconds=1))
            prior_events = event_rows(prior, since)

            def event_counts(events: list[dict[str, Any]]) -> dict[str, int]:
                return {
                    "decks": sum(e["action"] == "deck.created" for e in events),
                    "cards": sum(
                        int(
                            json.loads(e["metadata_json"] or "{}").get("cards_added", 0)
                        )
                        for e in events
                    ),
                    "builders": len(
                        {
                            e["actor_id"]
                            for e in events
                            if e["actor_id"]
                            and e["action"] in {"deck.created", "deck.edited"}
                        }
                    ),
                }

            current = event_counts(current_events)
            previous = event_counts(prior_events)
            current_verdicts = int(
                conn.execute(
                    "SELECT COUNT(*) FROM deck_feedback WHERE updated_at>=? AND updated_at<?",
                    (self._iso(since), self._iso(now + timedelta(seconds=1))),
                ).fetchone()[0]
            )
            previous_verdicts = int(
                conn.execute(
                    "SELECT COUNT(*) FROM deck_feedback WHERE updated_at>=? AND updated_at<?",
                    (self._iso(prior), self._iso(since)),
                ).fetchone()[0]
            )
            current["verdicts"] = current_verdicts
            previous["verdicts"] = previous_verdicts

            pending = conn.execute("""SELECT u.id,u.email,MAX(t.created_at) AS sent_at,
                          MAX(t.expires_at) AS expires_at
                   FROM users u LEFT JOIN invite_tokens t ON t.user_id=u.id
                   WHERE u.status='invited' GROUP BY u.id
                   ORDER BY sent_at LIMIT 5""").fetchall()
            negative = conn.execute("""SELECT card_name,
                       SUM(CASE WHEN vote='up' THEN 1 WHEN vote='down' THEN -1 ELSE 0 END) net,
                       COUNT(*) votes
                   FROM card_feedback GROUP BY card_name HAVING net<0
                   ORDER BY net ASC,votes DESC LIMIT 3""").fetchall()
            unsupported = conn.execute(
                """SELECT candidate_id,commander_name,simulation_status,created_at
                   FROM cedh_candidates
                   WHERE COALESCE(simulation_status,'') NOT IN ('simulated')
                   ORDER BY created_at DESC LIMIT 3"""
            ).fetchall()
            recent = conn.execute(
                """SELECT a.action,a.created_at,a.metadata_json,u.display_name,
                          d.title AS deck_title
                   FROM activity_events a LEFT JOIN users u ON u.id=a.actor_id
                   LEFT JOIN deck_documents d ON d.id=a.subject_id
                   ORDER BY a.created_at DESC LIMIT 12"""
            ).fetchall()
            conn.execute(
                "INSERT INTO admin_visits(user_id,last_overview_at) VALUES(?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET last_overview_at=excluded.last_overview_at",
                (admin_id, self._iso(now)),
            )
            conn.commit()

        keys = (
            ("decks", "Decks built"),
            ("verdicts", "Verdicts left"),
            ("cards", "Cards added"),
            ("builders", "Active builders"),
        )
        tiles = [
            {
                "key": key,
                "label": label,
                "value": current[key],
                "delta": None if first_visit else current[key] - previous[key],
            }
            for key, label in keys
        ]
        attention: list[dict[str, Any]] = []
        if pending:
            attention.append(
                {
                    "label": f"{len(pending)} invitation{'s' if len(pending) != 1 else ''} pending",
                    "detail": ", ".join(
                        str(row["email"] or "unknown") for row in pending
                    ),
                    "href": "/admin/users?status=invited",
                }
            )
        for row in unsupported:
            attention.append(
                {
                    "label": f"{row['commander_name']} has no current simulation",
                    "detail": row["simulation_status"] or "not simulated",
                    "href": f"/cedh/candidate/{row['candidate_id']}",
                }
            )
        for row in negative:
            attention.append(
                {
                    "label": f"{row['card_name']} rated {row['net']:+d} net",
                    "detail": f"{row['votes']} ratings",
                    "href": f"/admin/feedback/card/{row['card_name']}",
                }
            )
        return {
            "first_visit": first_visit,
            "period_days": max(1, round(span.total_seconds() / 86400)),
            "tiles": tiles,
            "attention": attention[:6],
            "recent": [dict(row) for row in recent],
            "active_users": db.AdminAnalyticsRepo(self.db_path)
            .overview()["users_by_status"]
            .get("active", 0),
        }

    def users(
        self, *, query: str = "", status: str = ""
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        where = ["1=1"]
        params: list[Any] = []
        if query:
            where.append("(u.email LIKE ? OR u.display_name LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
        if status in {"active", "invited", "disabled"}:
            where.append("u.status=?")
            params.append(status)
        with db.connect(self.db_path) as conn:
            counts = {
                row["status"]: int(row["n"])
                for row in conn.execute(
                    "SELECT status,COUNT(*) n FROM users GROUP BY status"
                ).fetchall()
            }
            rows = conn.execute(
                f"""SELECT u.id,u.email,u.display_name,u.role,u.status,
                           u.monthly_deck_quota,u.last_login_at,u.created_at,
                       (SELECT COUNT(*) FROM deck_documents d WHERE d.owner_id=u.id) decks,
                       (SELECT COUNT(*) FROM deck_zones z JOIN deck_documents d ON d.id=z.deck_id WHERE d.owner_id=u.id) zones,
                       (SELECT COUNT(*) FROM card_feedback c WHERE c.user_id=u.id) card_fb,
                       (SELECT COUNT(*) FROM deck_feedback f WHERE f.user_id=u.id) deck_fb,
                       (SELECT MAX(expires_at) FROM invite_tokens t WHERE t.user_id=u.id AND t.used_at IS NULL) invite_expires_at
                   FROM users u WHERE {' AND '.join(where)}
                   ORDER BY CASE u.status WHEN 'invited' THEN 0 WHEN 'active' THEN 1 ELSE 2 END,u.created_at DESC""",
                params,
            ).fetchall()
        return [dict(row) for row in rows], counts
