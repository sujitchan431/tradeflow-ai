"""TradeFlow AI Agent — Supabase client layer for businesses + stage tables."""
import os, json, urllib.request, urllib.error

def load_env():
    env = {}
    env_file = os.path.expanduser("~/.hermes/.env")
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env

ENV = load_env()
API_URL = ENV["SUPABASE_URL"]
API_KEY = ENV["SUPABASE_SERVICE_ROLE_KEY"]
HEADERS = {"apikey": API_KEY, "Authorization": f"Bearer {API_KEY}"}

def _req(path, method="GET", body=None, extra_headers=None):
    """Make a Supabase REST API request."""
    url = f"{API_URL}/rest/v1/{path}"
    h = {**HEADERS}
    if extra_headers:
        h.update(extra_headers)
    req = urllib.request.Request(url, method=method, headers=h)
    if body:
        req.data = json.dumps(body).encode()
        if "Content-Type" not in h:
            h["Content-Type"] = "application/json"
            req = urllib.request.Request(url, method=method, headers=h, data=req.data)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        content = resp.read().decode()
        return json.loads(content) if content else []
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        return {"error": e.code, "body": body}
    except Exception as e:
        return {"error": str(e)}


def get_businesses(state=None, limit=100, offset=0, enrichment_status=None, has_email=None):
    """Fetch businesses by pipeline state."""
    filters = []
    if state:
        filters.append(f"status=eq.{state}")
    if enrichment_status:
        filters.append(f"enrichment_status=eq.{enrichment_status}")
    if has_email is True:
        filters.append("email=not.is.null")
    if has_email is False:
        filters.append("email=is.null")
    
    filter_str = "&".join(filters)
    path = f"businesses?select=*&limit={limit}&offset={offset}"
    if filter_str:
        path += "&" + filter_str
    return _req(path)


def count_businesses(state=None, enrichment_status=None):
    """Count businesses matching filters."""
    filters = []
    if state:
        filters.append(f"status=eq.{state}")
    if enrichment_status:
        filters.append(f"enrichment_status=eq.{enrichment_status}")
    filter_str = "&".join(filters)
    path = f"businesses?select=id&limit=0"
    if filter_str:
        path += "&" + filter_str
    
    url = f"{API_URL}/rest/v1/{path}"
    h = {**HEADERS, "Prefer": "count=exact"}
    req = urllib.request.Request(url, headers=h)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        cr = resp.headers.get("content-range", "")
        return int(cr.split("/")[-1]) if "/" in cr else 0
    except:
        return 0


def update_business(business_id, fields):
    """Update a single business."""
    path = f"businesses?id=eq.{business_id}"
    h = {**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"}
    return _req(path, method="PATCH", body=fields, extra_headers=h)


def update_businesses_batch(ids, fields):
    """Update multiple businesses."""
    if not ids:
        return 0
    fixed = 0
    batch = list(ids)
    for i in range(0, len(batch), 500):
        chunk = batch[i:i+500]
        ids_str = ",".join(str(x) for x in chunk)
        path = f"businesses?id=in.({ids_str})"
        h = {**HEADERS, "Content-Type": "application/json", "Prefer": "return=minimal"}
        result = _req(path, method="PATCH", body=fields, extra_headers=h)
        if isinstance(result, dict) and "error" in result:
            continue
        fixed += len(chunk)
    return fixed


def get_stage_scoring(business_id=None, limit=100):
    """Get scoring records."""
    if business_id:
        path = f"stage_scoring?select=*&business_id=eq.{business_id}&limit=1"
    else:
        path = f"stage_scoring?select=*&limit={limit}&order=pipeline_score.desc"
    return _req(path)


def get_stage_offers(business_id=None, limit=100):
    """Get offer records."""
    if business_id:
        path = f"stage_offers?select=*&business_id=eq.{business_id}&limit=5"
    else:
        path = f"stage_offers?select=*&limit={limit}"
    return _req(path)


def get_stage_outreach(business_id=None, limit=100):
    """Get outreach records."""
    if business_id:
        path = f"stage_outreach?select=*&business_id=eq.{business_id}&limit=5"
    else:
        path = f"stage_outreach?select=*&limit={limit}&order=created_at.desc"
    return _req(path)


def get_stage_demos(business_id=None, limit=100):
    """Get demo records."""
    if business_id:
        path = f"stage_demos?select=*&business_id=eq.{business_id}&limit=5"
    else:
        path = f"stage_demos?select=*&limit={limit}"
    return _req(path)


def count_outreach_events(business_id=None):
    """Count outreach events for a business."""
    if business_id:
        path = f"stage_outreach?select=id&business_id=eq.{business_id}"
    else:
        path = f"stage_outreach?select=id&limit=0"
    url = f"{API_URL}/rest/v1/{path}"
    h = {**HEADERS, "Prefer": "count=exact"}
    req = urllib.request.Request(url, headers=h)
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        cr = resp.headers.get("content-range", "")
        return int(cr.split("/")[-1]) if "/" in cr else 0
    except:
        return 0
