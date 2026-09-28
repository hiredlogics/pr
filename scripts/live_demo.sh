#!/usr/bin/env bash
# Walks one case through every API endpoint against a throwaway server.
#   ./scripts/live_demo.sh
# Set ANTHROPIC_API_KEY first to use real extraction instead of the demo
# label-matcher (see DemoLLM in pcn_appeal/llm.py).
set -euo pipefail
cd "$(dirname "$0")/.."

PORT=${PORT:-8077}
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

$PY -m uvicorn pcn_appeal.api:app --port "$PORT" > /tmp/pcn_api.log 2>&1 &
trap 'kill $! 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do
  curl -sf -m 1 "localhost:$PORT/health" >/dev/null 2>&1 && break
  sleep 0.5
done

API="localhost:$PORT"
pp() { $PY -m json.tool --indent 2; }
step() { printf '\n\033[1m########## %s ##########\033[0m\n' "$1"; }

step "GET /health"
curl -s "$API/health" | pp

step "POST /cases"
CASE=$(curl -s -X POST "$API/cases" | $PY -c 'import sys,json;print(json.load(sys.stdin)["case_id"])')
echo "case_id = $CASE"

step "POST /cases/$CASE/documents"
curl -s -X POST "$API/cases/$CASE/documents" -H 'Content-Type: application/json' -d '{
  "documents": [{"evidence_id":"E1","filename":"pcn_letter.pdf","kind":"OTHER",
    "text":"PARKING CHARGE NOTICE\nOperator Name: Acme Parking Ltd\nPCN Number: PCN778899\nVehicle Registration: KX19 PLT\nLocation: Riverside Retail Park\nPostcode: M1 4BT\nDate of Contravention: 12/06/2026\nDate of Issue: 02/07/2026\nEntry Time: 14:05\nExit Time: 16:58\nCharge: 100\nAlleged Breach: Overstayed the maximum permitted period\nTrade Association: BPA"}]
}' | pp

step "GET /cases/$CASE/confirmation"
curl -s "$API/cases/$CASE/confirmation" | pp

step "POST /cases/$CASE/confirm"
curl -s -X POST "$API/cases/$CASE/confirm" -H 'Content-Type: application/json' -d '{
  "confirmed": ["pcn_number","vrm","parking_event_date","notice_issue_date"],
  "narrative": "The letter only turned up weeks after the visit. There was a queue at the barrier trying to get out."
}' | pp

step "POST /cases/$CASE/answers"
curl -s -X POST "$API/cases/$CASE/answers" -H 'Content-Type: application/json' \
  -d '{"answers":{"permitted_period_ended":"yes","exit_delay_min":18,"exit_congestion":"yes"}}' | pp

step "POST /cases/$CASE/generate"
curl -s -X POST "$API/cases/$CASE/generate" | pp

step "GET /cases/$CASE/appeal"
curl -s "$API/cases/$CASE/appeal" | $PY -c '
import sys, json, textwrap
d = json.load(sys.stdin)
for k in ("state", "primary_route", "secondary_routes", "pofa_route", "pofa_findings",
          "code_version", "module_ids"):
    print(f"{k:15}: {d[k]}")
print("=" * 78)
for para in d["letter"].split("\n\n"):
    print(textwrap.fill(para, 78)); print()
print("evidence_list  :", d["evidence_list"])'

step "GET /cases/$CASE/trace  (why it argued that)"
curl -s "$API/cases/$CASE/trace" | $PY -c '
import sys, json
d = json.load(sys.stdin)
for t in d["trace"]:
    print("  -", t)
print("  missing_facts:", d["missing_facts"])'
