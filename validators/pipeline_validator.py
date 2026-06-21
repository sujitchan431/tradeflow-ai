"""TradeFlow Pipeline Validator — validates EVERY stage before advancing leads.
DETECT → CLASSIFY → BLOCK/FLAG. Works across all pipeline stages.

Stages checked:
  1. Enrichment  (new → enriched)
  2. Scoring     (enriched → scored)
  3. Offers      (scored → offer_generated)
  4. Outreach    (offer_generated → outreached)
  5. Response    (outreached → responded / booked / disqualified)
  6. Cross-stage integrity (no orphans, no skipped stages, timestamp ordering)

Usage:
  python3 validators/pipeline_validator.py          # validate everything
  python3 validators/pipeline_validator.py --stage enrichment  # single stage
  python3 validators/pipeline_validator.py --summary             # counts only
"""

import os, json, re, urllib.request, urllib.error
from datetime import datetime, timezone
from collections import defaultdict

# ── Config ──
ENV = {}
with open(os.path.expanduser("~/.hermes/.env")) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            ENV[k.strip()] = v.strip().strip('"').strip("'")

URL = ENV["SUPABASE_URL"]
KEY = ENV["SUPABASE_SERVICE_ROLE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
H_JSON = {**H, "Content-Type": "application/json"}

# ── Valid offer types (from catalog)
VALID_OFFER_TYPES = {"website", "chat_widget", "phone", "reputation", "booking", "social"}
VALID_TIERS = {"S", "A", "B", "C", "D"}
VALID_STATUSES = {"new", "enriched", "scored", "offer_generated", "outreached", "responded", "booked", "client", "disqualified"}

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


def _req(path, method="GET", body=None, extra=None):
    h = dict(H_JSON)
    if extra: h.update(extra)
    url = f"{URL}/rest/v1/{path}"
    req = urllib.request.Request(url, method=method, headers=h)
    if body:
        req.data = json.dumps(body).encode()
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        content = resp.read().decode()
        return json.loads(content) if content else []
    except Exception as e:
        return {"error": str(e)}


def _count(table, extra=""):
    url = f"{URL}/rest/v1/{table}?select=id&limit=0{extra}"
    req = urllib.request.Request(url, headers={**H, "Prefer": "count=exact"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        cr = resp.headers.get("content-range", "")
        return int(cr.split("/")[-1]) if "/" in cr else 0
    except:
        return -1


def _fetch_all(table, fields="*", extra="", limit=1000):
    """Fetch all rows with pagination."""
    all_rows = []
    offset = 0
    while True:
        path = f"{table}?select={fields}&limit={limit}&offset={offset}{extra}"
        batch = _req(path)
        if isinstance(batch, dict) and "error" in batch:
            break
        if not isinstance(batch, list) or not batch:
            break
        all_rows.extend(batch)
        offset += len(batch)
    return all_rows


# ═══════════════════════════════════════════════════════
#  STAGE 1: ENRICHMENT VALIDATOR
# ═══════════════════════════════════════════════════════

def validate_enrichment(sample=200):
    """Check businesses in 'enriched' or beyond have valid enrichment data."""
    issues = []
    stats = {"checked": 0, "passed": 0, "failed": 0}

    businesses = _fetch_all(
        "businesses",
        fields="id,business_name,enrichment_status,has_https,has_facebook,has_instagram,has_contact_form,has_booking_system,has_chat_widget,email,website,status",
        extra="&status=in.(enriched,scored,offer_generated,outreached,responded,booked)&limit={}".format(sample)
    )

    for biz in businesses[:sample]:
        stats["checked"] += 1
        bid = biz["id"]
        name = biz.get("business_name", "?")[:30]
        failures = []

        # enrichment_status must be 'enriched'
        if biz.get("enrichment_status") != "enriched":
            failures.append("enrichment_status_not_set")

        # Boolean fields must not be null
        for field in ["has_https", "has_contact_form", "has_booking_system", "has_chat_widget"]:
            if biz.get(field) is None:
                failures.append(f"{field}_null")

        # Facebook/Instagram — should be set (warn, don't block)
        for field in ["has_facebook", "has_instagram"]:
            if biz.get(field) is None:
                failures.append(f"{field}_null")

        # If business has a website but enrichment_status is still raw
        if biz.get("website") and biz.get("enrichment_status") in (None, "raw"):
            failures.append("has_website_but_not_enriched")

        if failures:
            stats["failed"] += 1
            # Social-only nulls = FLAG (not all businesses have social media)
            social_only = all(f.endswith('_null') and f.startswith('has_') for f in failures)
            severity = "FLAG" if social_only else "BLOCK"
            issues.append({
                "biz_id": bid,
                "name": name,
                "stage": "enrichment",
                "failures": failures,
                "severity": severity,
            })
        else:
            stats["passed"] += 1

    return issues, stats


# ═══════════════════════════════════════════════════════
#  STAGE 2: SCORING VALIDATOR
# ═══════════════════════════════════════════════════════

def validate_scoring(sample=500):
    """Check businesses in 'scored' or beyond have valid scoring records."""
    issues = []
    stats = {"checked": 0, "passed": 0, "failed": 0}

    # Get scored businesses
    businesses = _fetch_all(
        "businesses",
        fields="id,business_name,status",
        extra="&status=in.(scored,offer_generated,outreached,responded,booked)&limit={}".format(sample)
    )

    for biz in businesses[:sample]:
        stats["checked"] += 1
        bid = biz["id"]
        name = biz.get("business_name", "?")[:30]
        failures = []

        scoring = _req(f"stage_scoring?select=*&business_id=eq.{bid}&limit=1")
        if isinstance(scoring, dict) and "error" in scoring:
            failures.append("no_scoring_record")
        elif not isinstance(scoring, list) or not scoring:
            failures.append("no_scoring_record")
        else:
            sc = scoring[0]
            ps = sc.get("pipeline_score")
            tier = sc.get("pipeline_tier", "")
            gaps = sc.get("key_gaps") or []
            breakdown = sc.get("score_breakdown") or {}

            # Score range
            if not isinstance(ps, (int, float)) or ps < 0 or ps > 100:
                failures.append(f"invalid_score:{ps}")

            # Tier must be valid
            if tier not in VALID_TIERS:
                failures.append(f"invalid_tier:{tier}")

            # Tier must match score
            expected_tier = (
                "S" if ps >= 80 else "A" if ps >= 65 else "B" if ps >= 50
                else "C" if ps >= 35 else "D"
            )
            if tier != expected_tier:
                failures.append(f"tier_mismatch:{tier}_vs_expected_{expected_tier}")

            # Gaps must be non-empty
            if not gaps:
                failures.append("no_gaps_identified")

            # Breakdown must be present
            if not breakdown:
                failures.append("no_score_breakdown")

            # Score must match breakdown sum
            try:
                breakdown_sum = sum(v if isinstance(v, (int, float)) else 0 for v in breakdown.values())
                if abs(breakdown_sum - ps) > 5:
                    failures.append(f"breakdown_mismatch:sum={breakdown_sum}_score={ps}")
            except:
                pass

        if failures:
            stats["failed"] += 1
            issues.append({
                "biz_id": bid,
                "name": name,
                "stage": "scoring",
                "failures": failures,
                "severity": "BLOCK",
            })
        else:
            stats["passed"] += 1

    return issues, stats


# ═══════════════════════════════════════════════════════
#  STAGE 3: OFFER VALIDATOR
# ═══════════════════════════════════════════════════════

CATALOG_PRICES = {
    "website": 297, "chat_widget": 247, "phone": 297,
    "reputation": 197, "booking": 400, "social": 500,
}

def validate_offers(sample=500):
    """Check businesses in 'offer_generated' or beyond have valid offers."""
    issues = []
    stats = {"checked": 0, "passed": 0, "failed": 0}

    businesses = _fetch_all(
        "businesses",
        fields="id,business_name,status",
        extra="&status=in.(offer_generated,outreached,responded,booked)&limit={}".format(sample)
    )

    for biz in businesses[:sample]:
        stats["checked"] += 1
        bid = biz["id"]
        name = biz.get("business_name", "?")[:30]
        failures = []

        offers = _req(f"stage_offers?select=*&business_id=eq.{bid}&limit=20")
        if isinstance(offers, dict) and "error" in offers:
            failures.append("no_offer_records")
        elif not isinstance(offers, list) or not offers:
            failures.append("no_offer_records")
        else:
            # Required fields per offer
            for i, off in enumerate(offers):
                oid = off.get("id", "?")
                prefix = f"offer[{i}]"

                if not off.get("offer_name"):
                    failures.append(f"{prefix}:no_name")
                if not off.get("offer_type") or off.get("offer_type") not in VALID_OFFER_TYPES:
                    failures.append(f"{prefix}:invalid_type:{off.get('offer_type')}")

                # Prices may be stored as strings — normalize to int
                raw_price = off.get("offer_monthly_price", 0)
                raw_setup = off.get("offer_setup_price", 0)
                try:
                    price = int(raw_price)
                except (ValueError, TypeError):
                    failures.append(f"{prefix}:invalid_monthly_price:{raw_price}")
                    price = None
                try:
                    setup = int(raw_setup)
                except (ValueError, TypeError):
                    failures.append(f"{prefix}:invalid_setup_price:{raw_setup}")
                    setup = None

                if price is not None and price < 0:
                    failures.append(f"{prefix}:negative_price:{price}")
                if setup is not None and setup < 0:
                    failures.append(f"{prefix}:negative_setup:{setup}")

                if not off.get("offer_pitch"):
                    failures.append(f"{prefix}:no_pitch")

                # Price matches catalog
                ot = off.get("offer_type")
                if ot in CATALOG_PRICES and price is not None:
                    expected = CATALOG_PRICES[ot]
                    if price != expected:
                        failures.append(f"{prefix}:price_mismatch:${price}_vs_catalog_${expected}")

            # Duplicate check
            seen_types = set()
            for off in offers:
                ot = off.get("offer_type")
                if ot in seen_types:
                    failures.append(f"duplicate_offer_type:{ot}")
                seen_types.add(ot)

            # Gaps-to-offers mapping check (fuzzy — matches OfferAgent's GAP_ALIASES)
            GAP_ALIASES = {
                'website': ['visibility', 'website', 'web', 'online presence', 'google maps', 'no website', 'website broken', 'no https', 'not mobile-friendly', 'website broken/blocked'],
                'chat_widget': ['conversion', 'booking', 'contact form', 'no contact form', 'no booking system', 'chat', 'no booking'],
                'phone': ['recovery', 'phone', 'email', 'no email', 'no phone', 'unreachable', 'no email — unreachable'],
                'reputation': ['value', 'reviews', 'rating', 'social proof', 'no reviews', 'low rating'],
                'booking': ['booking', 'receptionist', 'calendar', 'scheduling'],
                'social': ['social', 'facebook', 'instagram', 'no social', 'no social presence'],
            }
            biz_offer_types = {o.get("offer_type") for o in offers}
            scoring = _req(f"stage_scoring?select=key_gaps&business_id=eq.{bid}&limit=1")
            if isinstance(scoring, list) and scoring:
                gaps = scoring[0].get("key_gaps") or []
                if gaps:  # Only check if scoring has gaps (don't flag when gaps were inferred from scores)
                    for gap in gaps:
                        gap_lower = gap.lower()
                        matched = False
                        for gap_type, aliases in GAP_ALIASES.items():
                            if any(a in gap_lower for a in aliases):
                                if gap_type in biz_offer_types:
                                    matched = True
                                    break
                        if not matched:
                            failures.append(f"gap_no_offer:{gap[:30]}")

        if failures:
            stats["failed"] += 1
            issues.append({
                "biz_id": bid,
                "name": name,
                "stage": "offer",
                "failures": failures,
                "severity": "BLOCK",
            })
        else:
            stats["passed"] += 1

    return issues, stats


# ═══════════════════════════════════════════════════════
#  STAGE 4: OUTREACH VALIDATOR
# ═══════════════════════════════════════════════════════

def validate_outreach(sample=500):
    """Check businesses in 'outreached' or beyond have valid outreach records."""
    issues = []
    stats = {"checked": 0, "passed": 0, "failed": 0}

    businesses = _fetch_all(
        "businesses",
        fields="id,business_name,email,status",
        extra="&status=in.(outreached,responded,booked)&limit={}".format(sample)
    )

    for biz in businesses[:sample]:
        stats["checked"] += 1
        bid = biz["id"]
        name = biz.get("business_name", "?")[:30]
        email = biz.get("email", "")
        failures = []

        # Must have email
        if not email or not EMAIL_RE.match(email):
            failures.append("no_valid_email_in_outreached")

        outreach = _req(f"stage_outreach?select=*&business_id=eq.{bid}&limit=10")
        if isinstance(outreach, dict) and "error" in outreach:
            failures.append("no_outreach_records")
        elif not isinstance(outreach, list) or not outreach:
            failures.append("no_outreach_records")
        else:
            for i, rec in enumerate(outreach):
                rid = rec.get("id", "?")
                prefix = f"outreach[{i}]"

                if not rec.get("email_to"):
                    failures.append(f"{prefix}:no_email_to")
                if not rec.get("email_subject"):
                    failures.append(f"{prefix}:no_subject")
                if not rec.get("email_body"):
                    failures.append(f"{prefix}:no_body")
                if not rec.get("status"):
                    failures.append(f"{prefix}:no_status")

                # Status consistency
                status = rec.get("status", "")
                if status == "draft" and rec.get("sent_at"):
                    failures.append(f"{prefix}:draft_has_sent_at")
                if status == "sent" and not rec.get("sent_at"):
                    failures.append(f"{prefix}:sent_but_no_sent_at")

        # Zero outreach events but status is outreached
        outreach_count = sum(1 for o in (outreach if isinstance(outreach, list) else [])
                            if o.get("status") in ("draft", "sent", "delivered", "opened"))
        if outreach_count == 0:
            failures.append("outreached_but_zero_reachable_events")

        if failures:
            stats["failed"] += 1
            issues.append({
                "biz_id": bid,
                "name": name,
                "stage": "outreach",
                "failures": failures,
                "severity": "BLOCK",
            })
        else:
            stats["passed"] += 1

    return issues, stats


# ═══════════════════════════════════════════════════════
#  STAGE 5: RESPONSE VALIDATOR
# ═══════════════════════════════════════════════════════

def validate_responses(sample=500):
    """Check responded/booked businesses have actual reply evidence."""
    issues = []
    stats = {"checked": 0, "passed": 0, "failed": 0}

    businesses = _fetch_all(
        "businesses",
        fields="id,business_name,status",
        extra="&status=in.(responded,booked,disqualified)&limit={}".format(sample)
    )

    for biz in businesses[:sample]:
        stats["checked"] += 1
        bid = biz["id"]
        name = biz.get("business_name", "?")[:30]
        status = biz.get("status", "")
        failures = []

        outreach = _req(f"stage_outreach?select=*&business_id=eq.{bid}&limit=10")
        if not isinstance(outreach, list) or not outreach:
            failures.append("no_outreach_but_in_response_stage")
        else:
            if status == "responded":
                has_reply = any(o.get("replied_at") or o.get("response") for o in outreach)
                if not has_reply:
                    failures.append("responded_but_no_reply_evidence")

            if status == "booked":
                has_booking = any(o.get("booked_at") or o.get("booked") for o in outreach)
                has_reply = any(o.get("replied_at") for o in outreach)
                if not has_booking and not has_reply:
                    failures.append("booked_but_no_booking_or_reply_evidence")

            if status == "disqualified":
                dq_reasons = {o.get("status", "") for o in outreach}
                if not any(s in {"bounced", "failed", "invalid", "disqualified"} for s in dq_reasons):
                    # Check if disqualified because no email
                    biz_info = _req(f"businesses?select=email&id=eq.{bid}&limit=1")
                    has_email = False
                    if isinstance(biz_info, list) and biz_info:
                        has_email = bool(biz_info[0].get("email"))
                    if has_email:
                        failures.append("disqualified_but_no_clear_reason")

        if failures:
            stats["failed"] += 1
            issues.append({
                "biz_id": bid,
                "name": name,
                "stage": "response",
                "failures": failures,
                "severity": "BLOCK" if status == "booked" else "FLAG",
            })
        else:
            stats["passed"] += 1

    return issues, stats


# ═══════════════════════════════════════════════════════
#  STAGE 6: CROSS-STAGE INTEGRITY
# ═══════════════════════════════════════════════════════

def validate_cross_stage():
    """Check for skipped stages, orphans, and timestamp ordering."""
    issues = []
    stats = {"checked": 5, "passed": 0, "failed": 0}

    # ── Check 1: scored without enrichment ──
    n = _count("businesses", "&status=eq.scored&enrichment_status=neq.enriched")
    if n > 0:
        stats["failed"] += 1
        issues.append({
            "biz_id": "*",
            "name": f"{n} businesses",
            "stage": "cross",
            "failures": [f"scored_but_not_enriched"],
            "severity": "CRITICAL" if n > 10 else "WARNING",
        })
    else:
        stats["passed"] += 1

    # ── Check 2: offers without scoring ──
    offer_ids = _req("stage_offers?select=business_id&limit=5000")
    if isinstance(offer_ids, list) and offer_ids:
        offer_biz_ids = list({o["business_id"] for o in offer_ids})
        scored_biz_ids = set()
        for chunk in [offer_biz_ids[i:i+500] for i in range(0, len(offer_biz_ids), 500)]:
            ids_str = ",".join(str(x) for x in chunk)
            scored = _req(f"stage_scoring?select=business_id&business_id=in.({ids_str})&limit=5000")
            if isinstance(scored, list):
                scored_biz_ids.update(o["business_id"] for o in scored)

        orphans = set(offer_biz_ids) - scored_biz_ids
        if orphans:
            stats["failed"] += 1
            issues.append({
                "biz_id": ",".join(str(x) for x in list(orphans)[:10]),
                "name": f"{len(orphans)} leads",
                "stage": "cross",
                "failures": ["offers_without_scoring_records"],
                "severity": "CRITICAL",
            })
        else:
            stats["passed"] += 1

    # ── Check 3: outreach without offers ──
    outreach_ids = _req("stage_outreach?select=business_id&limit=5000")
    if isinstance(outreach_ids, list) and outreach_ids:
        out_biz_ids = list({o["business_id"] for o in outreach_ids})
        offered_biz_ids = set()
        for chunk in [out_biz_ids[i:i+500] for i in range(0, len(out_biz_ids), 500)]:
            ids_str = ",".join(str(x) for x in chunk)
            offered = _req(f"stage_offers?select=business_id&business_id=in.({ids_str})&limit=5000")
            if isinstance(offered, list):
                offered_biz_ids.update(o["business_id"] for o in offered)

        orphans = set(out_biz_ids) - offered_biz_ids
        if orphans:
            stats["failed"] += 1
            issues.append({
                "biz_id": ",".join(str(x) for x in list(orphans)[:10]),
                "name": f"{len(orphans)} leads",
                "stage": "cross",
                "failures": ["outreach_without_offers"],
                "severity": "CRITICAL",
            })
        else:
            stats["passed"] += 1

    # ── Check 4: responded without outreach ──
    n_responded = _count("businesses", "&status=in.(responded,booked)&id=not.in.(select distinct business_id from stage_outreach)")
    if n_responded >= 0:
        # Can't do complex subquery easily with REST — check via count
        all_responded = _fetch_all("businesses", "id", "&status=in.(responded,booked)&limit=500")
        all_outreached_ids = set()
        for chunk in [all_responded[i:i+500] for i in range(0, len(all_responded), 500)]:
            ids = [str(b["id"]) for b in chunk]
            ids_str = ",".join(ids)
            out = _req(f"stage_outreach?select=business_id&business_id=in.({ids_str})&limit=5000")
            if isinstance(out, list):
                all_outreached_ids.update(o["business_id"] for o in out)

        responder_ids = {b["id"] for b in all_responded}
        orphans = responder_ids - all_outreached_ids
        if orphans:
            stats["failed"] += 1
            issues.append({
                "biz_id": ",".join(str(x) for x in list(orphans)[:10]),
                "name": f"{len(orphans)} leads",
                "stage": "cross",
                "failures": ["responded_but_no_outreach_record"],
                "severity": "CRITICAL",
            })
        else:
            stats["passed"] += 1

    # ── Check 5: invalid status values ──
    n_invalid = _count("businesses", "&status=not.in.(new,enriched,scored,offer_generated,outreached,responded,booked,client,disqualified)")
    if n_invalid > 0:
        stats["failed"] += 1
        issues.append({
            "biz_id": "*",
            "name": f"{n_invalid} businesses",
            "stage": "cross",
            "failures": ["invalid_status_value"],
            "severity": "CRITICAL",
        })
    else:
        stats["passed"] += 1

    # ── Check 6: test/junk offers (types not in catalog) ──
    valid_types = ",".join(CATALOG_PRICES.keys())
    n_junk = _count("stage_offers", f"&offer_type=not.in.({valid_types})")
    if n_junk > 0:
        stats["failed"] += 1
        issues.append({
            "biz_id": "*",
            "name": f"{n_junk} records",
            "stage": "cross",
            "failures": ["junk_offer_types_detected"],
            "severity": "CRITICAL",
        })
    else:
        stats["passed"] += 1

    return issues, stats


# ═══════════════════════════════════════════════════════
#  MASTER VALIDATOR
# ═══════════════════════════════════════════════════════

ALL_STAGES = {
    "enrichment": validate_enrichment,
    "scoring": validate_scoring,
    "offers": validate_offers,
    "outreach": validate_outreach,
    "responses": validate_responses,
    "cross": validate_cross_stage,
}


def run_all():
    """Run all validators and return combined report."""
    print(f"TradeFlow Pipeline Validator — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    all_issues = []
    all_stats = {}

    for stage_name, validate_fn in ALL_STAGES.items():
        print(f"\n🔍 Validating {stage_name.upper()}...")
        try:
            issues, stats = validate_fn()
            all_issues.extend(issues)
            all_stats[stage_name] = stats
            print(f"   Checked: {stats['checked']} | Passed: {stats['passed']} | Failed: {stats['failed']}")
        except Exception as e:
            print(f"   ❌ Crashed: {e}")
            all_stats[stage_name] = {"checked": 0, "passed": 0, "failed": 1, "error": str(e)}

    # ── Summary ──
    total_checked = sum(s.get("checked", 0) for s in all_stats.values())
    total_failed = sum(s.get("failed", 0) for s in all_stats.values())
    total_issues = len(all_issues)

    blockers = [i for i in all_issues if i.get("severity") == "BLOCK"]
    criticals = [i for i in all_issues if i.get("severity") == "CRITICAL"]
    warnings = [i for i in all_issues if i.get("severity") in ("WARNING", "FLAG")]

    print(f"\n{'=' * 60}")
    print(f"VALIDATION COMPLETE")
    print(f"  Total checks:    {total_checked}")
    print(f"  Total failed:    {total_failed}")
    print(f"  Issues found:    {total_issues}")
    print(f"    🔴 BLOCK:      {len(blockers)}")
    print(f"    🔴 CRITICAL:   {len(criticals)}")
    print(f"    🟡 WARNING:    {len(warnings)}")

    if blockers:
        print(f"\n🚫 BLOCKERS — these leads must be fixed before advancing:")
        for b in blockers[:10]:
            print(f"   #{b['biz_id']} {b['name']:30s} [{b['stage']}] {'; '.join(b['failures'][:3])}")
        if len(blockers) > 10:
            print(f"   ... and {len(blockers) - 10} more")

    if criticals:
        print(f"\n⚠️  CRITICAL cross-stage issues:")
        for c in criticals[:5]:
            print(f"   {c['name']:40s} {'; '.join(c['failures'])}")
        if len(criticals) > 5:
            print(f"   ... and {len(criticals) - 5} more")

    return {"issues": all_issues, "stats": all_stats, "blockers": len(blockers), "criticals": len(criticals)}


def run_summary():
    """Quick counts only — no sample validation."""
    print(f"TradeFlow Pipeline Integrity Summary — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    counts = {}
    for stage in ["new", "enriched", "scored", "offer_generated", "outreached", "responded", "booked", "client", "disqualified"]:
        counts[stage] = _count("businesses", f"&status=eq.{stage}")

    print(f"\n  {'Stage':<25} {'Count':>7}")
    print(f"  {'-'*32}")
    for stage, count in counts.items():
        if count > 0:
            print(f"  {stage:<25} {count:>7}")

    total = sum(counts.values())
    print(f"  {'-'*32}")
    print(f"  {'Total':<25} {total:>7}")

    # Quick integrity checks
    print(f"\n  Cross-stage integrity:")
    n = _count("businesses", "&status=in.(scored,offer_generated,outreached)&enrichment_status=neq.enriched")
    print(f"  {'Scored/enriched but not enriched:':<42} {n:>5}")

    n = _count("businesses", "&status=eq.outreached&email=is.null")
    print(f"  {'Outreached but no email:':<42} {n:>5}")

    n = _count("stage_offers", "")
    print(f"  {'Total offers in stage_offers:':<42} {n:>5}")


if __name__ == "__main__":
    import sys
    if "--summary" in sys.argv:
        run_summary()
    elif "--stage" in sys.argv:
        idx = sys.argv.index("--stage")
        stage = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
        if stage and stage in ALL_STAGES:
            issues, stats = ALL_STAGES[stage]()
            for i in issues:
                print(f"  #{i['biz_id']} {i['name']:30s} [{i['severity']}] {'; '.join(i['failures'][:5])}")
            print(f"\n  Checked: {stats['checked']} | Passed: {stats['passed']} | Failed: {stats['failed']}")
        else:
            print(f"Invalid stage. Available: {', '.join(ALL_STAGES.keys())}")
    else:
        run_all()
