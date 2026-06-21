"""TradeFlow Self-Healing - Read-only anomaly detection.
DETECT -> CLASSIFY -> ALERT. Never auto-fixes.
"""
import os, json, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from collections import Counter

NOW = datetime.now(timezone.utc)
STAGE_STUCK_HOURS = 48
DRAFT_STALE_HOURS = 48
QUOTA_WARN_PCT = 0.85
QUOTA_CRIT_PCT = 0.95


def _env():
    env = {}
    with open(os.path.expanduser("~/.hermes/.env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env

_E = _env()
_URL = _E.get("SUPABASE_URL", "")
_KEY = _E.get("SUPABASE_SERVICE_ROLE_KEY", "")
_H = {"apikey": _KEY, "Authorization": "Bearer " + _KEY}


def _count(table, extra=""):
    url = "%s/rest/v1/%s?select=id&limit=0%s" % (_URL, table, extra)
    req = urllib.request.Request(url, headers={**_H, "Prefer": "count=exact"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return int(resp.headers.get("content-range", "0-0/0").split("/")[-1])
    except:
        return -1


def detect_stuck():
    cutoff = (NOW - timedelta(hours=STAGE_STUCK_HOURS)).isoformat()
    out = []
    for stage in ["new", "enriched", "scored", "offer_generated"]:
        n = _count("businesses", "&status=eq.%s&updated_at=lt.%s" % (stage, cutoff))
        if n > 10:
            out.append({"category": "STAGE_STUCK", "severity": "CRITICAL" if n>50 else "WARNING",
                        "title": "%d stuck in '%s' >%dh" % (n, stage, STAGE_STUCK_HOURS),
                        "count": n, "recovery_hint": "run_tradeflow_tick"})
    return out


def detect_dead_letters():
    cutoff = (NOW - timedelta(hours=DRAFT_STALE_HOURS)).isoformat()
    n = _count("stage_outreach", "&status=eq.draft&created_at=lt.%s" % cutoff)
    if n > 2:
        return [{"category": "DEAD_LETTER", "severity": "CRITICAL" if n>10 else "WARNING",
                 "title": "%d drafts >%dh" % (n, DRAFT_STALE_HOURS),
                 "count": n, "recovery_hint": "run_email_sender"}]
    return []


def detect_quota():
    out = []
    sf = os.path.expanduser("~/.hermes/state/resend_daily_tf.json")
    if os.path.exists(sf):
        try:
            st = json.load(open(sf))
            if st.get("date") == NOW.strftime("%Y-%m-%d"):
                s, lim = st.get("sent", 0), 100
                pct = s / lim
                if pct >= QUOTA_CRIT_PCT:
                    out.append({"category": "QUOTA_NEAR", "severity": "CRITICAL",
                                "title": "Resend: %d/%d (%.0f%%)" % (s, lim, pct*100),
                                "recovery_hint": "reduce_send_volume"})
                elif pct >= QUOTA_WARN_PCT:
                    out.append({"category": "QUOTA_NEAR", "severity": "WARNING",
                                "title": "Resend: %d/%d (%.0f%%)" % (s, lim, pct*100),
                                "recovery_hint": "monitor_quota"})
        except: pass
    return out


def detect_data_gaps():
    out = []
    n = _count("businesses", "&status=eq.offer_generated&email=is.null")
    if n > 5:
        out.append({"category": "DATA_GAP", "severity": "WARNING",
                    "title": "%d offer-ready businesses missing email" % n,
                    "count": n, "recovery_hint": "re_enrich"})
    return out


def detect_orphans():
    out = []
    n = _count("stage_outreach", "&status=eq.draft&sent_at=is.null")
    if n > 20:
        out.append({"category": "DATA_GAP", "severity": "WARNING",
                    "title": "%d unsent drafts in queue" % n,
                    "recovery_hint": "run_email_sender"})
    return out


def run_all():
    all_a = []
    for name, fn in [("Stuck", detect_stuck), ("DeadLetters", detect_dead_letters),
                      ("Data", detect_data_gaps), ("Quota", detect_quota), ("Orphans", detect_orphans)]:
        try:
            all_a.extend(fn())
        except Exception as e:
            all_a.append({"category": "FAIL", "severity": "WARNING",
                          "title": "%s crashed: %s" % (name, str(e)[:100])})
    return all_a


def report(anomalies):
    if not anomalies:
        return "TRADEFLOW HEALER - All healthy."
    lines = ["TRADEFLOW HEALER - %s" % NOW.strftime("%Y-%m-%d %H:%M UTC"),
             "%d critical, %d warnings" % (
                 sum(1 for a in anomalies if a["severity"]=="CRITICAL"),
                 sum(1 for a in anomalies if a["severity"]=="WARNING")), ""]
    for a in anomalies:
        icon="!" if a["severity"]=="CRITICAL" else "-"
        lines.append("[%s] %s %s" % (icon, a["category"], a["title"]))
        if a.get("recovery_hint"):
            lines.append("    fix: %s" % a["recovery_hint"])
    return "\n".join(lines)


def run():
    print("TradeFlow Self-Healing - Scanning...")
    a = run_all()
    print(report(a))
    if any(x["severity"]=="CRITICAL" for x in a):
        print("\nACTION REQUIRED.")
    return a
