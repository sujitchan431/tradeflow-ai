#!/usr/bin/env python3
"""TradeFlow pipeline delta tracker — reports change since last check."""
import json, os
from datetime import datetime, timezone
import sys
sys.path.insert(0, '/root/tradeflow_ai')
from supabase_client import count_businesses

STATE_FILE = os.path.expanduser("~/.hermes/state/tradeflow_delta.json")

now = datetime.now(timezone.utc)
current = {}
for s in ['new', 'scored', 'offer_generated', 'outreached', 'responded', 'booked', 'disqualified']:
    current[s] = count_businesses(state=s)

prev = {}
if os.path.exists(STATE_FILE):
    with open(STATE_FILE) as f:
        prev = json.load(f)

# Calculate deltas
lines = []
lines.append(f"📊 TradeFlow Pipeline — {now.strftime('%H:%M UTC')}")
lines.append("")
lines.append("| Stage | Now | Δ |")
lines.append("|---|---|---|")

total_delta = 0
for s in current:
    now_val = current[s]
    prev_val = prev.get(s, now_val)
    delta = now_val - prev_val
    if delta != 0 or now_val > 0:
        sign = f"+{delta}" if delta > 0 else str(delta)
        lines.append(f"| {s} | {now_val} | {sign} |")
        total_delta += abs(delta)

if total_delta == 0:
    lines.append("")
    lines.append("⏳ Still running — no stage changes this interval.")

# Check if pipeline done
scored = current.get('scored', 0)
if scored == 0:
    lines.append("")
    lines.append("✅ PIPELINE COMPLETE — all scored processed!")

# Save current state
with open(STATE_FILE, 'w') as f:
    json.dump(current, f)

print('\n'.join(lines))
