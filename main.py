#!/usr/bin/env python3
"""TradeFlow AI Agent — Pipeline Orchestrator.
Inspired by Ad Scout AI. Manages the end-to-end pipeline for TradeFlow businesses.

Commands:
  tick     — Process one batch of businesses (default: 10 per stage)
  advance  — Auto-advance businesses that meet criteria
  sweep    — Check for replies and advance responded → booked
  status   — Show pipeline state
  drain    — Run tick in a loop until all businesses are processed
"""
import sys, os, time, argparse
from datetime import datetime
from routing import (
    tick_batch, advance_business, pipeline_status,
    STAGES, NEXT_STAGE, check_advance_criteria
)
from supabase_client import (
    get_businesses, count_businesses, update_business, update_businesses_batch
)


def cmd_status():
    """Show full pipeline status."""
    st = pipeline_status()
    print(f"\n{'='*60}")
    print(f"  TradeFlow AI Agent — Pipeline Status")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'='*60}")
    print(f"\n  {'Stage':<25} {'Count':>8}  {'Progress'}")
    print(f"  {'-'*45}")
    
    total = st.get("total", 0)
    for stage in STAGES:
        count = st.get(stage, 0)
        if count > 0:
            pct = f"{count/total*100:.1f}%" if total > 0 else "0%"
            bar = "█" * int(count / total * 20) if total > 0 else ""
            print(f"  {stage:<25} {count:>6}  {bar} {pct}")
    
    print(f"\n  {'Total':<25} {total:>6}")
    print(f"  Progress: {st.get('progress_pct', 0)}%")
    print()


def cmd_tick(batch_size=10):
    """Process one batch of businesses per stage."""
    print(f"\n=== TradeFlow Tick (batch={batch_size}) ===\n")
    
    # Process stages in pipeline order (skip terminal/empty states)
    active_stages = ["new", "enriched", "scored", "offer_generated", "outreached"]
    
    total_processed = 0
    total_advanced = 0
    
    for stage in active_stages:
        count = count_businesses(state=stage)
        if count == 0:
            continue
        
        print(f"  Stage: {stage} ({count} pending)")
        processed, advanced, errors = tick_batch(stage, limit=batch_size)
        print(f"    → processed={processed} advanced={advanced} errors={errors}")
        total_processed += processed
        total_advanced += advanced
    
    print(f"\n  Total: processed={total_processed} advanced={total_advanced}")
    return total_processed, total_advanced


def cmd_advance(batch_size=50):
    """Auto-advance businesses that already meet next-stage criteria.
    This is the fast path — no agent processing, just state transitions.
    """
    print(f"\n=== TradeFlow Advance (batch={batch_size}) ===\n")
    
    stages_to_check = ["new", "enriched", "scored", "offer_generated", "outreached", "responded"]
    total_advanced = 0
    
    for stage in stages_to_check:
        next_stage = NEXT_STAGE.get(stage)
        if not next_stage:
            continue
        
        businesses = get_businesses(state=stage, limit=batch_size)
        if isinstance(businesses, dict) and "error" in businesses:
            continue
        if not businesses:
            continue
        
        advanced_ids = []
        for biz in businesses:
            can, reason = check_advance_criteria(biz["id"], stage, next_stage)
            if can:
                advanced_ids.append(biz["id"])
        
        if advanced_ids:
            result = update_businesses_batch(advanced_ids, {"status": next_stage})
            print(f"  {stage} → {next_stage}: {len(advanced_ids)} advanced")
            total_advanced += len(advanced_ids)
        else:
            # Show why NOT advancing (sample first business)
            if businesses:
                biz = businesses[0]
                can, reason = check_advance_criteria(biz["id"], stage, next_stage)
                print(f"  {stage} → {next_stage}: 0 advanced (sample: {reason})")
    
    print(f"\n  Total advanced: {total_advanced}")
    return total_advanced


def cmd_sweep():
    """Check for replies, advance responded businesses."""
    print(f"\n=== TradeFlow Sweep ===\n")
    
    from agents.response_agent import ResponseAgent
    agent = ResponseAgent()
    
    # Check outreached businesses for replies
    businesses = get_businesses(state="outreached", limit=100)
    if isinstance(businesses, dict) and "error" in businesses:
        businesses = []
    
    replied = 0
    booked = 0
    disqualified = 0
    
    for biz in businesses:
        result = agent.process(biz)
        if result.get("advanced"):
            stage = result.get("stage", "")
            if stage == "responded":
                replied += 1
            elif stage == "booked":
                booked += 1
            elif result.get("disqualified"):
                disqualified += 1
    
    print(f"  Outreached checked: {len(businesses)}")
    print(f"  → Responded: {replied}")
    print(f"  → Booked: {booked}")
    print(f"  → Disqualified: {disqualified}")
    
    # Also check responded businesses for bookings
    businesses = get_businesses(state="responded", limit=100)
    if isinstance(businesses, dict) and "error" in businesses:
        businesses = []
    
    newly_booked = 0
    for biz in businesses:
        result = agent.process(biz)
        if result.get("advanced") and result.get("stage") == "booked":
            newly_booked += 1
    
    if newly_booked:
        print(f"  Responded → Booked: {newly_booked}")
    
    return replied + booked + disqualified + newly_booked


def cmd_drain(batch_size=5):
    """Run tick in a loop until pipeline is fully processed."""
    print(f"\n=== TradeFlow Drain (batch={batch_size}) ===\n")
    
    iteration = 0
    max_iterations = 100
    
    while iteration < max_iterations:
        iteration += 1
        processed, advanced = cmd_tick(batch_size=batch_size)
        
        if processed == 0:
            print(f"\n  Pipeline drained after {iteration} iterations.")
            break
        
        time.sleep(1)
    
    cmd_status()


def main():
    parser = argparse.ArgumentParser(description="TradeFlow AI Agent")
    parser.add_argument("command", nargs="?", default="status",
                        choices=["tick", "advance", "sweep", "status", "drain"])
    parser.add_argument("--batch", type=int, default=10,
                        help="Batch size (default: 10)")
    parser.add_argument("--drain", action="store_true",
                        help="Run in drain mode (loop)")
    
    args = parser.parse_args()
    
    if args.command == "status":
        cmd_status()
    elif args.command == "tick":
        cmd_tick(batch_size=args.batch)
    elif args.command == "advance":
        cmd_advance(batch_size=args.batch * 5)
    elif args.command == "sweep":
        cmd_sweep()
    elif args.command == "drain":
        cmd_drain(batch_size=args.batch)


if __name__ == "__main__":
    main()
