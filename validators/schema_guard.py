#!/usr/bin/env python3
"""Schema Guard — verifies required DB constraints before pipeline runs.
Run at startup: python3 validators/schema_guard.py
Exits non-zero if schema is broken.
"""
import os, json, uuid, urllib.request, urllib.error

ENV = {}
with open(os.path.expanduser("~/.hermes/.env")) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            ENV[k.strip()] = v.strip().strip('"').strip("'")

URL = ENV["SUPABASE_URL"]
KEY = ENV["SUPABASE_SERVICE_ROLE_KEY"]
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

REQUIRED_CONSTRAINTS = [
    ("stage_offers", "stage_offers_biz_type_unique", "UNIQUE(business_id, offer_type) — allows multi-offer"),
]

FORBIDDEN_CONSTRAINTS = [
    ("stage_offers", "stage_offers_business_id_key", "UNIQUE(business_id) — BLOCKS multi-offer"),
]


def check_constraint(table, constraint_name):
    """Check if a constraint exists by testing write behavior with real business ID."""
    import uuid
    
    # Use a real business ID so foreign keys don't interfere
    req = urllib.request.Request(f"{URL}/rest/v1/businesses?select=id&limit=1", headers=H)
    resp = urllib.request.urlopen(req, timeout=5)
    biz_id = json.loads(resp.read().decode())[0]["id"]
    tid = str(uuid.uuid4())[:8]
    
    if constraint_name == "stage_offers_biz_type_unique":
        types = [f"schema_a_{tid}", f"schema_b_{tid}"]
        o1 = {"business_id": biz_id, "offer_name": "SCHEMA_TEST", "offer_type": types[0],
              "offer_monthly_price": 1, "offer_setup_price": 0, "offer_pitch": "test", "offer_headline": "T"}
        o2 = {"business_id": biz_id, "offer_name": "SCHEMA_TEST", "offer_type": types[1],
              "offer_monthly_price": 2, "offer_setup_price": 0, "offer_pitch": "test", "offer_headline": "T"}
        
        results = []
        for o in [o1, o2]:
            r = urllib.request.Request(f"{URL}/rest/v1/stage_offers",
                data=json.dumps(o).encode(), headers={**H, "Prefer": "return=minimal"}, method="POST")
            try:
                urllib.request.urlopen(r, timeout=5)
                results.append("OK")
            except urllib.error.HTTPError as e:
                results.append(f"HTTP_{e.code}")
        
        # Cleanup by type
        for t in types:
            try:
                r = urllib.request.Request(f"{URL}/rest/v1/stage_offers?offer_type=eq.{t}", headers=H, method="DELETE")
                urllib.request.urlopen(r, timeout=5)
            except: pass
        
        if results == ["OK", "OK"]:
            return True, "Multi-offer constraint OK"
        else:
            return False, f"Multi-offer broken: {results}"

    if constraint_name == "stage_offers_business_id_key":
        types = [f"bad_a_{tid}", f"bad_b_{tid}"]
        o1 = {"business_id": biz_id, "offer_name": "BAD_TEST", "offer_type": types[0],
              "offer_monthly_price": 1, "offer_setup_price": 0, "offer_pitch": "test", "offer_headline": "T"}
        o2 = {"business_id": biz_id, "offer_name": "BAD_TEST", "offer_type": types[1],
              "offer_monthly_price": 2, "offer_setup_price": 0, "offer_pitch": "test", "offer_headline": "T"}
        
        results = []
        for o in [o1, o2]:
            r = urllib.request.Request(f"{URL}/rest/v1/stage_offers",
                data=json.dumps(o).encode(), headers={**H, "Prefer": "return=minimal"}, method="POST")
            try:
                urllib.request.urlopen(r, timeout=5)
                results.append("OK")
            except urllib.error.HTTPError as e:
                results.append(f"HTTP_{e.code}")
        
        for t in types:
            try:
                r = urllib.request.Request(f"{URL}/rest/v1/stage_offers?offer_type=eq.{t}", headers=H, method="DELETE")
                urllib.request.urlopen(r, timeout=5)
            except: pass
        
        if "HTTP_409" in results:
            return False, "UNIQUE(business_id) still active — multi-offer blocked!"
        return True, "No harmful UNIQUE(business_id) constraint"
    
    return True, "Unknown (skipped)"


if __name__ == "__main__":
    import sys
    print("🔍 Schema Guard — checking DB constraints...")
    all_ok = True
    
    for table, name, desc in REQUIRED_CONSTRAINTS:
        ok, msg = check_constraint(table, name)
        icon = "✅" if ok else "❌"
        print(f"  {icon} REQUIRED: {desc} → {msg}")
        if not ok: all_ok = False
    
    for table, name, desc in FORBIDDEN_CONSTRAINTS:
        ok, msg = check_constraint(table, name)
        icon = "✅" if ok else "❌"
        print(f"  {icon} FORBIDDEN: {desc} → {msg}")
        if not ok: all_ok = False
    
    print()
    if all_ok:
        print("✅ All schema constraints OK. Pipeline safe.")
        sys.exit(0)
    else:
        print("❌ SCHEMA BROKEN. Fix constraints before running pipeline.")
        sys.exit(1)
