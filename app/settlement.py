"""MolTrust Sports — Phase 2: Outcome Settlement via API-Football."""

import os
import json
import logging
import datetime
import difflib
import httpx

logger = logging.getLogger("moltrust.sports")

APIFOOTBALL_KEY = os.getenv("APIFOOTBALL_KEY", "")
APIFOOTBALL_BASE = "https://v3.football.api-sports.io"

# League slug → API-Football league ID
LEAGUE_MAP = {
    "epl": 39, "premierleague": 39, "premier-league": 39,
    "bundesliga": 78,
    "laliga": 140, "la-liga": 140,
    "seriea": 135, "serie-a": 135,
    "ligue1": 61, "ligue-1": 61,
    "uefa-cl": 2, "champions-league": 2, "ucl": 2,
    "uefa-el": 3, "europa-league": 3, "uel": 3,
    "conference-league": 848, "uecl": 848,
    "eredivisie": 88,
    "liga-portugal": 94, "primeira-liga": 94,
    "super-lig": 203,
    "world-cup": 1, "wc": 1,
    "euros": 4, "euro": 4,
    "copa-america": 9,
    "nba": 12,
    "mls": 253,
    "a-league": 188,
}


def _parse_event_id(event_id: str) -> dict | None:
    """Parse event_id format: {sport}:{competition}:{YYYYMMDD}:{home}-{away}"""
    parts = event_id.split(":")
    if len(parts) < 4:
        return None
    sport = parts[0]
    competition = parts[1]
    date_str = parts[2]
    teams_str = ":".join(parts[3:])  # rejoin in case teams have colons

    # Parse date (support YYYYMMDD or YYYY-MM-DD)
    try:
        if "-" in date_str and len(date_str) == 10:
            date = date_str  # already YYYY-MM-DD
        elif len(date_str) == 8:
            date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        else:
            return None
    except (ValueError, IndexError):
        return None

    # Parse teams
    team_parts = teams_str.split("-")
    if len(team_parts) < 2:
        return None
    home = team_parts[0].strip()
    away = "-".join(team_parts[1:]).strip()

    league_id = LEAGUE_MAP.get(competition)

    return {
        "sport": sport,
        "competition": competition,
        "date": date,
        "home": home,
        "away": away,
        "league_id": league_id,
    }


def _fuzzy_match(name: str, candidates: list[str], threshold: float = 0.55) -> str | None:
    """Fuzzy match a team name against candidates."""
    name_lower = name.lower().replace("-", " ").replace("_", " ")
    best_match = None
    best_ratio = 0.0
    for candidate in candidates:
        cand_lower = candidate.lower()
        # Direct substring match
        if name_lower in cand_lower or cand_lower in name_lower:
            return candidate
        ratio = difflib.SequenceMatcher(None, name_lower, cand_lower).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = candidate
    return best_match if best_ratio >= threshold else None


async def fetch_result(event_id: str) -> dict | None:
    """Fetch match result from API-Football for a given event_id."""
    # Check for polymarket prefix — no auto-settlement
    if event_id.startswith("polymarket:"):
        return None

    if not APIFOOTBALL_KEY:
        logger.warning("APIFOOTBALL_KEY not set, skipping fetch_result")
        return None

    parsed = _parse_event_id(event_id)
    if not parsed:
        logger.warning(f"Cannot parse event_id: {event_id}")
        return None

    if not parsed["league_id"]:
        logger.warning(f"Unknown league: {parsed['competition']}")
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{APIFOOTBALL_BASE}/fixtures",
                headers={"x-apisports-key": APIFOOTBALL_KEY},
                params={
                    "league": parsed["league_id"],
                    "date": parsed["date"],
                    "season": int(parsed["date"][:4]),
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.error(f"API-Football request failed: {e}")
        return None

    fixtures = data.get("response", [])
    if not fixtures:
        logger.info(f"No fixtures found for {event_id}")
        return None

    # Build team name map and find the matching fixture
    for fixture in fixtures:
        home_name = fixture["teams"]["home"]["name"]
        away_name = fixture["teams"]["away"]["name"]

        home_match = _fuzzy_match(parsed["home"], [home_name.lower()])
        away_match = _fuzzy_match(parsed["away"], [away_name.lower()])

        if home_match is not None and away_match is not None:
            status = fixture["fixture"]["status"]["short"]
            goals_home = fixture["goals"]["home"]
            goals_away = fixture["goals"]["away"]

            finished = status in ("FT", "AET", "PEN")

            if goals_home is None or goals_away is None:
                return {"result": None, "score": None, "finished": False, "fixture_id": fixture["fixture"]["id"]}

            if goals_home > goals_away:
                result = "home_win"
            elif goals_away > goals_home:
                result = "away_win"
            else:
                result = "draw"

            return {
                "result": result,
                "score": f"{goals_home}:{goals_away}",
                "finished": finished,
                "fixture_id": fixture["fixture"]["id"],
                "home_team": home_name,
                "away_team": away_name,
                "status": status,
            }

    # Try broader fuzzy match across all fixtures
    all_home_names = [f["teams"]["home"]["name"] for f in fixtures]
    all_away_names = [f["teams"]["away"]["name"] for f in fixtures]

    home_match = _fuzzy_match(parsed["home"], all_home_names)
    away_match = _fuzzy_match(parsed["away"], all_away_names)

    if home_match and away_match:
        for fixture in fixtures:
            if fixture["teams"]["home"]["name"] == home_match and fixture["teams"]["away"]["name"] == away_match:
                status = fixture["fixture"]["status"]["short"]
                goals_home = fixture["goals"]["home"]
                goals_away = fixture["goals"]["away"]
                finished = status in ("FT", "AET", "PEN")

                if goals_home is None or goals_away is None:
                    return {"result": None, "score": None, "finished": False, "fixture_id": fixture["fixture"]["id"]}

                if goals_home > goals_away:
                    result = "home_win"
                elif goals_away > goals_home:
                    result = "away_win"
                else:
                    result = "draw"

                return {
                    "result": result,
                    "score": f"{goals_home}:{goals_away}",
                    "finished": finished,
                    "fixture_id": fixture["fixture"]["id"],
                    "home_team": home_match,
                    "away_team": away_match,
                    "status": status,
                }

    logger.info(f"No matching fixture for {parsed['home']} vs {parsed['away']} on {parsed['date']}")
    return None


async def settle_prediction(conn, commitment_hash: str, result: dict) -> bool:
    """Settle a single prediction given an outcome result."""
    row = await conn.fetchrow(
        "SELECT id, prediction, settled_at FROM sports_predictions WHERE commitment_hash = $1",
        commitment_hash,
    )
    if not row:
        return False
    if row["settled_at"] is not None:
        return False  # already settled

    prediction = row["prediction"]
    if isinstance(prediction, str):
        prediction = json.loads(prediction)

    # Determine correctness: compare prediction.outcome with result.result
    predicted_outcome = prediction.get("outcome", prediction.get("result", ""))
    actual_outcome = result.get("result", "")
    correct = predicted_outcome.lower().strip() == actual_outcome.lower().strip() if predicted_outcome and actual_outcome else None

    outcome_data = json.dumps(result)
    now = datetime.datetime.now(datetime.timezone.utc)

    await conn.execute(
        """
        UPDATE sports_predictions
        SET outcome = $1::jsonb, correct = $2, settled_at = $3
        WHERE commitment_hash = $4
        """,
        outcome_data, correct, now, commitment_hash,
    )
    return True


async def run_settlement_cycle(db_pool) -> dict:
    """Run one settlement cycle: check all unsettled predictions past their event_start."""
    summary = {"checked": 0, "settled": 0, "skipped": 0, "errors": []}

    if not db_pool:
        return summary

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT commitment_hash, event_id, event_start
            FROM sports_predictions
            WHERE settled_at IS NULL AND event_start < $1
            ORDER BY event_start ASC
            LIMIT 50
            """,
            cutoff,
        )

    for row in rows:
        summary["checked"] += 1
        event_id = row["event_id"]
        commitment_hash = row["commitment_hash"]

        # Skip polymarket events (manual settlement only)
        if event_id.startswith("polymarket:"):
            summary["skipped"] += 1
            continue

        try:
            result = await fetch_result(event_id)
            if result is None:
                continue
            if not result.get("finished"):
                continue

            async with db_pool.acquire() as conn:
                ok = await settle_prediction(conn, commitment_hash, result)
                if ok:
                    summary["settled"] += 1
                    logger.info(f"Settled {commitment_hash[:16]}... → {result['result']} ({result['score']})")
        except Exception as e:
            logger.error(f"Settlement error for {commitment_hash[:16]}...: {e}")
            summary["errors"].append({"hash": commitment_hash[:16], "error": str(e)})

    logger.info(f"Settlement cycle: {summary['checked']} checked, {summary['settled']} settled, {summary['skipped']} skipped")
    return summary
