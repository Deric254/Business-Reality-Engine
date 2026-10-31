"""Capability card: a small public description of this system for the other systems of the same owner.

Contract "dericbi.ecosystem/1". Public on purpose and secret-free: a name, a version and what this system offers or accepts, as plain
strings. It reads no business data and touches no database, so it works even when the database is down. Other systems read it to discover
what is here; nothing about them is known to this code.
"""
from fastapi import APIRouter

router = APIRouter()

CONTRACT = "dericbi.ecosystem/1"


@router.get("/card")
def card():
    return {
        "contract": CONTRACT,
        "id": "bre",
        "name": "Business Reality Engine",
        "role": "watches a business's sales and spending, spots what matters and says why",
        "offers": ["alerts", "business.health", "forecast"],
        "accepts": ["events.webhook", "events.batch", "files.upload"],
        "health": "/api/health/",
        # Ready-made links other systems may offer their owner: the page to open, with {name} replaced. Relative to this system's address.
        "handoffs": {"licence.issue": "/master?issue_to={name}&ref={ref}"},
        # Where a system may read from this one, and under what rules (a key the owner holds; only businesses that opted in). Paths on this system.
        "feeds": {"advisor.alerts": "/api/advisor/alerts"},
    }
