# Elkins Scout Live

Cloud bridge for the Elkins Dynasty project.

This repo refreshes Sleeper league **The Boys** (`1353413006104485888`) every 15 minutes using Sleeper's public read-only API.

The generated source of truth is:

`https://raw.githubusercontent.com/kelkins422/elkins-scout-live/main/snapshot/scout_snapshot.json`

The snapshot includes:
- all 10 rosters
- starters, bench, taxi and IR
- future and traded draft picks
- recent transactions
- completed startup draft results
- league/scoring settings
- current NFL state
- a computed `free_agents` pool of unrostered QB/RB/WR/TE players

The workflow only commits when substantive league data changes, so scheduled runs do not create pointless commits just because the timestamp changed.
