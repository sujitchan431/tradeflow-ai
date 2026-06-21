#!/usr/bin/env python3
"""TradeFlow AI Agent — Pipeline Orchestrator.
Runs businesses through full pipeline: enrich → score → offer → outreach → monitor.

Commands:
  tick     — Run agents on a batch (enrichment, scoring, offers, outreach)
  advance  — Fast-forward businesses that already have stage data
  sweep    — Check for replies, advance responded → booked
  status   — Show pipeline state
  drain    — Run tick in a loop
"""
import sys, argparse
from datetime import datetime
from routing import tick_batch, advance_batch, sweep_replies, pipeline_status, STAGES
from supabase_client import count_businesses


def cmd_status():
    st = pipeline_status()
    print(f"\n{'='*60}")
    print(f"  TradeFlow AI Agent — Pipeline Status")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'='*60}")
    print(f"\n  {'Stage':<25} {'Count':>8}  {'Progress'}")
    print(f"  {'-'*45}")

    total = st.get('total', 0)
    for stage in STAGES:
        count = st.get(stage, 0)
        if count > 0:
            pct = f"{count/total*100:.1f}%" if total > 0 else ""
            bar = "█" * min(int(count / total * 20), 20) if total > 0 else ""
            print(f"  {stage:<25} {count:>6}  {bar} {pct}")

    print(f"\n  {'Total':<25} {total:>6}")
    print(f"  Progress: {st.get('progress_pct', 0)}%")
    print()


def cmd_tick(batch_size=5):
    """Run full pipeline: enrichment → scoring → offers → outreach."""
    print(f"\n=== TradeFlow Tick (batch={batch_size}) ===\n")
    results = tick_batch(limit=batch_size)
    print(f"  Enriched:  {results['enriched']}")
    print(f"  Scored:    {results['scored']}")
    print(f"  Offered:   {results['offered']}")
    print(f"  Outreached:{results['outreached']}")
    if results['errors']:
        print(f"  Errors:    {results['errors']}")
    print()


def cmd_advance(batch_size=50):
    """Fast-advance businesses that already have stage data."""
    print(f"\n=== TradeFlow Advance (batch={batch_size}) ===\n")
    count = advance_batch(limit=batch_size)
    print(f"  Advanced: {count} businesses")
    print()


def cmd_sweep():
    """Check for replies."""
    print(f"\n=== TradeFlow Sweep ===\n")
    results = sweep_replies(limit=100)
    print(f"  Replied:      {results['replied']}")
    print(f"  Booked:       {results['booked']}")
    print(f"  Disqualified: {results['disqualified']}")
    print()


def cmd_drain(batch_size=3, max_iter=20):
    """Run tick until pipeline fully processed."""
    print(f"\n=== TradeFlow Drain (batch={batch_size}, max={max_iter}) ===\n")
    for i in range(max_iter):
        st = pipeline_status()
        pending_new = st.get('new', 0)
        pending_enriched = st.get('enriched', 0)
        pending_scored = st.get('scored', 0)
        pending_offered = st.get('offer_generated', 0)

        if pending_new + pending_enriched + pending_scored + pending_offered == 0:
            print(f"  Pipeline drained after {i} iterations.")
            break

        print(f"  Iteration {i+1}: new={pending_new} enriched={pending_enriched} scored={pending_scored} offered={pending_offered}")
        cmd_tick(batch_size=batch_size)
    cmd_status()


def main():
    parser = argparse.ArgumentParser(description="TradeFlow AI Agent")
    parser.add_argument("command", nargs="?", default="status",
                        choices=["tick", "advance", "sweep", "status", "drain"])
    parser.add_argument("--batch", type=int, default=5)
    args = parser.parse_args()

    if args.command == "status":
        cmd_status()
    elif args.command == "tick":
        cmd_tick(batch_size=args.batch)
    elif args.command == "advance":
        cmd_advance(batch_size=args.batch * 10)
    elif args.command == "sweep":
        cmd_sweep()
    elif args.command == "drain":
        cmd_drain(batch_size=args.batch)


if __name__ == "__main__":
    main()
