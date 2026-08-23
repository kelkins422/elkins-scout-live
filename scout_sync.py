import copy
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://api.sleeper.app/v1"
LEAGUE_ID = "1353413006104485888"
SLEEPER_USERNAME = "kelkins81"
OUT = Path("snapshot/scout_snapshot.json")
TIMEOUT = 45
FANTASY_POSITIONS = {"QB", "RB", "WR", "TE"}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def get_json(path, retries=3):
    url = path if path.startswith("http") else f"{BASE}{path}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ElkinsScout/1.0 (+github.com/kelkins422/elkins-scout-live)"},
    )
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
                return json.load(response)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"GET failed after {retries} attempts: {url}: {last}")


def player_view(player_id, players):
    if player_id is None:
        return None
    p = players.get(str(player_id), {})
    name = (
        p.get("full_name")
        or " ".join(x for x in [p.get("first_name"), p.get("last_name")] if x)
        or p.get("search_full_name")
        or str(player_id)
    )
    return {
        "player_id": str(player_id),
        "name": name,
        "position": p.get("position"),
        "team": p.get("team"),
        "status": p.get("status"),
        "injury_status": p.get("injury_status"),
    }


def team_label(roster_id, roster_names):
    return roster_names.get(int(roster_id), f"Roster {roster_id}")


def transaction_view(tx, players, roster_names):
    adds = tx.get("adds") or {}
    drops = tx.get("drops") or {}
    roster_ids = set(tx.get("roster_ids") or [])
    roster_ids.update(adds.values())
    roster_ids.update(drops.values())

    created = tx.get("created")
    created_utc = None
    if isinstance(created, (int, float)):
        created_utc = datetime.fromtimestamp(created / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")

    return {
        "transaction_id": tx.get("transaction_id"),
        "type": tx.get("type"),
        "status": tx.get("status"),
        "week": tx.get("leg") or tx.get("week"),
        "created_utc": created_utc,
        "teams": [team_label(rid, roster_names) for rid in sorted(roster_ids)],
        "adds": [
            {"player": player_view(pid, players), "roster_id": rid, "team": team_label(rid, roster_names)}
            for pid, rid in sorted(adds.items(), key=lambda x: (int(x[1]), str(x[0])))
        ],
        "drops": [
            {"player": player_view(pid, players), "roster_id": rid, "team": team_label(rid, roster_names)}
            for pid, rid in sorted(drops.items(), key=lambda x: (int(x[1]), str(x[0])))
        ],
        "draft_picks": [
            {
                "season": str(p.get("season")) if p.get("season") is not None else None,
                "round": p.get("round"),
                "roster_id": p.get("roster_id"),
                "owner_id": p.get("owner_id"),
                "previous_owner_id": p.get("previous_owner_id"),
            }
            for p in (tx.get("draft_picks") or [])
        ],
        "waiver_budget": tx.get("waiver_budget") or [],
        "waiver_bid": (tx.get("settings") or {}).get("waiver_bid"),
    }


def build_rosters(raw_rosters, users, players):
    user_map = {str(u.get("user_id")): u for u in users}
    roster_names = {}

    for r in raw_rosters:
        rid = int(r["roster_id"])
        owner = user_map.get(str(r.get("owner_id")), {})
        manager = owner.get("display_name") or owner.get("username") or str(r.get("owner_id"))
        roster_names[rid] = (owner.get("metadata") or {}).get("team_name") or manager

    result = []
    for r in raw_rosters:
        rid = int(r["roster_id"])
        owner = user_map.get(str(r.get("owner_id")), {})
        manager = owner.get("display_name") or owner.get("username") or str(r.get("owner_id"))
        all_ids = {str(x) for x in (r.get("players") or []) if x is not None}
        starters = [str(x) for x in (r.get("starters") or []) if x is not None and str(x) != "0"]
        taxi = [str(x) for x in (r.get("taxi") or []) if x is not None]
        reserve = [str(x) for x in (r.get("reserve") or []) if x is not None]
        bench = sorted(all_ids - set(starters) - set(taxi) - set(reserve))

        result.append({
            "roster_id": rid,
            "owner_user_id": str(r.get("owner_id")) if r.get("owner_id") is not None else None,
            "manager": manager,
            "team_name": roster_names[rid],
            "co_owners": r.get("co_owners") or [],
            "starters": [player_view(pid, players) for pid in starters],
            "bench": [player_view(pid, players) for pid in bench],
            "taxi": [player_view(pid, players) for pid in taxi],
            "injured_reserve": [player_view(pid, players) for pid in reserve],
            "all_players": [player_view(pid, players) for pid in sorted(all_ids)],
            "settings": r.get("settings") or {},
            "metadata": r.get("metadata"),
        })

    return result, roster_names


def build_future_picks(league, raw_rosters, traded_picks, roster_names):
    current_season = int(league["season"])
    rounds = int((league.get("settings") or {}).get("draft_rounds") or 4)
    roster_ids = sorted(int(r["roster_id"]) for r in raw_rosters)
    ownership = {}

    for season in range(current_season, current_season + 4):
        for round_no in range(1, rounds + 1):
            for original_rid in roster_ids:
                ownership[(str(season), round_no, original_rid)] = {
                    "season": str(season),
                    "round": round_no,
                    "original_roster_id": original_rid,
                    "original_team": team_label(original_rid, roster_names),
                    "current_roster_id": original_rid,
                    "current_team": team_label(original_rid, roster_names),
                    "previous_roster_id": original_rid,
                    "is_traded": False,
                }

    for p in traded_picks:
        key = (str(p.get("season")), int(p.get("round")), int(p.get("roster_id")))
        if key not in ownership:
            continue
        owner_id = int(p.get("owner_id"))
        previous_owner_id = int(p.get("previous_owner_id"))
        ownership[key].update({
            "current_roster_id": owner_id,
            "current_team": team_label(owner_id, roster_names),
            "previous_roster_id": previous_owner_id,
            "is_traded": owner_id != key[2],
        })

    return sorted(ownership.values(), key=lambda x: (int(x["season"]), x["round"], x["original_roster_id"]))


def build_free_agents(players, rostered_ids):
    free_agents = []
    for pid, p in players.items():
        if p.get("position") not in FANTASY_POSITIONS or str(pid) in rostered_ids:
            continue
        status = p.get("status")
        team = p.get("team")
        if p.get("active") is False and not team:
            continue
        if status in {"Inactive", "Retired"} and not team:
            continue

        item = player_view(pid, players)
        item.update({
            "age": p.get("age"),
            "years_exp": p.get("years_exp"),
            "depth_chart_position": p.get("depth_chart_position"),
            "depth_chart_order": p.get("depth_chart_order"),
        })
        free_agents.append(item)

    pos_order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3}
    free_agents.sort(key=lambda x: (pos_order.get(x.get("position"), 9), x.get("team") is None, x.get("name") or ""))
    return free_agents


def get_drafts(players, roster_names):
    drafts = []
    for d in get_json(f"/league/{LEAGUE_ID}/drafts") or []:
        did = d.get("draft_id")
        picks = get_json(f"/draft/{did}/picks") if did else []
        drafts.append({
            "draft_id": did,
            "season": d.get("season"),
            "status": d.get("status"),
            "type": d.get("type"),
            "settings": d.get("settings") or {},
            "picks": sorted([
                {
                    "pick_no": p.get("pick_no"),
                    "round": p.get("round"),
                    "roster_id": p.get("roster_id"),
                    "team": team_label(p.get("roster_id"), roster_names) if p.get("roster_id") is not None else None,
                    "player": player_view(p.get("player_id"), players),
                }
                for p in (picks or [])
            ], key=lambda x: x.get("pick_no") or 99999),
        })
    return drafts


def load_old():
    if not OUT.exists():
        return None
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return None


def stable_view(snapshot):
    if snapshot is None:
        return None
    x = copy.deepcopy(snapshot)
    x.pop("generated_at_utc", None)
    return x


def main():
    old = load_old()

    league = get_json(f"/league/{LEAGUE_ID}")
    users = get_json(f"/league/{LEAGUE_ID}/users")
    raw_rosters = get_json(f"/league/{LEAGUE_ID}/rosters")
    traded_picks = get_json(f"/league/{LEAGUE_ID}/traded_picks") or []
    nfl_state = get_json("/state/nfl")
    players = get_json("/players/nfl")
    sleeper_user = get_json(f"/user/{SLEEPER_USERNAME}")

    rosters, roster_names = build_rosters(raw_rosters, users, players)
    rostered_ids = {
        p["player_id"]
        for r in rosters
        for p in r["all_players"]
        if p is not None
    }

    tx_by_id = {
        tx["transaction_id"]: tx
        for tx in ((old or {}).get("recent_transactions") or [])
        if tx.get("transaction_id")
    }

    current_week = int(nfl_state.get("week") or 1)
    max_round = min(18, max(1, current_week) + 1)
    for week in range(1, max_round + 1):
        try:
            raw_txs = get_json(f"/league/{LEAGUE_ID}/transactions/{week}") or []
        except RuntimeError:
            raw_txs = []
        for tx in raw_txs:
            if tx.get("transaction_id"):
                tx_by_id[tx["transaction_id"]] = transaction_view(tx, players, roster_names)

    recent_transactions = sorted(
        tx_by_id.values(),
        key=lambda x: x.get("created_utc") or "",
        reverse=True,
    )

    traded_pick_views = sorted([
        {
            "round": p.get("round"),
            "season": str(p.get("season")) if p.get("season") is not None else None,
            "roster_id": p.get("roster_id"),
            "owner_id": p.get("owner_id"),
            "previous_owner_id": p.get("previous_owner_id"),
        }
        for p in traded_picks
    ], key=lambda x: (int(x["season"]), x["round"], x["roster_id"]))

    free_agents = build_free_agents(players, rostered_ids)

    snapshot = {
        "schema_version": 2,
        "generated_at_utc": utc_now(),
        "source": "Sleeper public read-only API",
        "user": {
            "username": sleeper_user.get("username") or SLEEPER_USERNAME,
            "user_id": str(sleeper_user.get("user_id")) if sleeper_user.get("user_id") is not None else None,
        },
        "league": league,
        "nfl_state": nfl_state,
        "rosters": rosters,
        "future_picks": build_future_picks(league, raw_rosters, traded_picks, roster_names),
        "traded_picks": traded_pick_views,
        "recent_transactions": recent_transactions,
        "drafts": get_drafts(players, roster_names),
        "free_agent_summary": {
            "total": len(free_agents),
            "by_position": {
                pos: sum(1 for p in free_agents if p.get("position") == pos)
                for pos in ["QB", "RB", "WR", "TE"]
            },
            "note": "Unrostered QB/RB/WR/TE players from Sleeper's current NFL player database.",
        },
        "free_agents": free_agents,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    if old is not None and stable_view(old) == stable_view(snapshot):
        print("No substantive league changes; snapshot unchanged.")
        return

    OUT.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"Wrote {OUT}: {len(rosters)} rosters, "
        f"{len(rostered_ids)} rostered players, "
        f"{len(free_agents)} free agents, "
        f"{len(recent_transactions)} transactions."
    )


if __name__ == "__main__":
    main()
