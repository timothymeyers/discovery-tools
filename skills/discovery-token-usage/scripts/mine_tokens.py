#!/usr/bin/env python3
"""Discovery App token-usage miner.

Reads .discovery/ exhaust across seven sources and produces a console
read-out. Cost/billing-multiplier data is FEATURE-FLAGGED behind --with-cost
and is never shown unless explicitly requested.

Sources:
  A. ~/Library/Application Support/DiscoveryApp/telemetry/token-usage.jsonl
  B. <ws>/.discovery/engine/clio/checkpoint/*/*/*/*/conversation_history.json  (SESSION_SHUTDOWN model_metrics)
  C. <ws>/.discovery/engine/copilot-cli/logs/*/*/copilot-stdout.log            (ACP prompt-result usage)
  D. <ws>/.discovery/engine-runs/*/*/meta.json                                 (engine prompts)
  E. <ws>/.discovery/engine/clio/instances/*/clio-stderr.log                   (per-invocation Copilot SDK usage)
  F. <ws>/.discovery/logs/sdk.log                                              (embedding volume, grading runs)
  G. <ws>/.discovery/host-tools/vscode-lm-tools.json + llm-router.json         (fixed prompt overhead, routing)

Usage:
  mine_tokens.py <workspace>                        # tokens-only console read-out
  mine_tokens.py <workspace> --with-cost            # add cost columns (feature flag)
  mine_tokens.py <workspace> --report <path.md>     # also emit markdown report
  mine_tokens.py <workspace> --json <path.json>     # override JSON dataset path
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
MULTIPLIER_PATH = os.path.join(SKILL_DIR, "references", "billing_multipliers.json")

# Set from argparse in main()
WS = os.getcwd()
JOURNAL = os.path.expanduser(
    "~/Library/Application Support/DiscoveryApp/telemetry/token-usage.jsonl"
)
WITH_COST = False
REPORT_PATH = None
JSON_PATH = None
VERBOSE = False

# In-memory buffer for the markdown report (populated in parallel with stdout)
_REPORT_BUF = []


def _r(*lines):
    """Append line(s) to the markdown report buffer (no-op if --report not set)."""
    if REPORT_PATH:
        _REPORT_BUF.extend(lines)


def _v(s=""):
    """Verbose output: to stdout only if --verbose, always mirrored to the report buffer."""
    if VERBOSE:
        print(s)
    if REPORT_PATH:
        stripped = s.strip() if isinstance(s, str) else s
        if stripped:
            _r(stripped)


def load_multipliers():
    """Load billing multipliers from references/. Only used if --with-cost."""
    try:
        with open(MULTIPLIER_PATH) as fh:
            data = json.load(fh)
        return data.get("multipliers", {})
    except Exception as e:
        print(f"WARN: could not load multipliers from {MULTIPLIER_PATH}: {e}",
              file=sys.stderr)
        return {}


def hdr(t):
    line = f"\n{'=' * 78}\n{t}\n{'=' * 78}"
    if VERBOSE:
        print(line)
    _r("", f"## {t}", "")


def fmt(rows, cols):
    widths = [max(len(str(r[i])) for r in ([cols] + rows)) for i in range(len(cols))]
    if VERBOSE:
        print("  ".join(c.ljust(widths[i]) for i, c in enumerate(cols)))
        print("  ".join("-" * widths[i] for i in range(len(cols))))
        for r in rows:
            print("  ".join(str(v).rjust(widths[i]) if i else str(v).ljust(widths[i])
                            for i, v in enumerate(r)))
    # Always mirror to markdown table in the report buffer.
    if REPORT_PATH:
        _r("| " + " | ".join(cols) + " |",
           "|" + "|".join(["---"] * len(cols)) + "|")
        for r in rows:
            _r("| " + " | ".join(str(v) for v in r) + " |")
        _r("")


def note(s):
    """Verbose note: to stdout only if --verbose, mirrored to report."""
    if VERBOSE:
        print(s)
    if REPORT_PATH:
        stripped = s.strip()
        if stripped:
            _r(stripped)


# ---------------------------------------------------------------- Source A
FIELD_RE = re.compile(r'"(\w+)":("(?:[^"\\]|\\.)*"|-?[\d.]+|true|false|null)')


def recover_fragment(line):
    """Concurrent appends interleave and corrupt lines. Salvage the field pairs."""
    d = {}
    for k, v in FIELD_RE.findall(line):
        try:
            d[k] = json.loads(v)
        except Exception:
            pass
    return d if ("realInputTokens" in d or "estimatedInputTokens" in d) else None


def mine_journal():
    rows, bad, recovered = [], 0, 0
    if not os.path.exists(JOURNAL):
        return rows, bad, recovered
    for line in open(JOURNAL, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            bad += 1
            frag = recover_fragment(line)
            if frag:
                frag["_recovered"] = True
                rows.append(frag)
                recovered += 1
            continue
        if "_sentinel" in d:
            continue
        rows.append(d)
    return rows, bad, recovered


def j_in(d):
    for k in ("inputTokens", "realInputTokens", "estimatedInputTokens"):
        if k in d:
            return d[k] or 0
    return 0


def j_out(d):
    for k in ("outputTokens", "realOutputTokens", "estimatedOutputTokens"):
        if k in d:
            return d[k] or 0
    return 0


def classify_journal(rows):
    """Split TokenJournal records into real / idle-loop / placeholder.

    Placeholder heuristics (measurement noise, not work):
      - source='vscode-lm-provider' with in=out=1 tokens (estimates, not measurements)
      - source='router' with 0 in / 0 out (shadow records duplicating vscode-otel)

    KNOWN DISCOVERY APP BUG: the extension host can enter a poll-retry loop
    that resends the same fixed prompts hundreds of times to Copilot. All
    records look real (they carry realInputTokens), but they never round-tripped.

      Signature: group by (source, model, sessionId); a stuck group has
      >= 100 records but only <= 5 distinct realInputTokens values (the loop
      re-sends the same 1–5 fixed context windows over and over).

      Observed case: ~2,600 vscode-otel/claude-sonnet-5 records in an
      83-minute window under one sessionId, only 6 distinct realInputTokens
      values. Distinct content was ~750K tokens; journal reported ~364M —
      roughly a 490x inflation.

    NOTE: this heuristic (mercifully) does NOT flag latencyMs=0 alone.
    latencyMs=0 is normal for vscode-otel telemetry — most real records also
    have it. The distinctness-of-realInputTokens signal is what's diagnostic.

    Returns (real_rows, idle_rows, placeholder_rows).
    """
    placeholder_idx = set()
    for i, d in enumerate(rows):
        if (d.get("source") == "vscode-lm-provider"
                and d.get("tokenSource") == "estimate"
                and j_in(d) == 1 and j_out(d) == 1):
            placeholder_idx.add(i)
        elif d.get("source") == "router" and j_in(d) == 0 and j_out(d) == 0:
            placeholder_idx.add(i)

    groups = collections.defaultdict(list)
    for i, d in enumerate(rows):
        if i in placeholder_idx:
            continue
        key = (d.get("source"), d.get("model") or d.get("modelId"),
               d.get("sessionId"))
        groups[key].append(d)

    idle_keys = set()
    for key, records in groups.items():
        if len(records) < 100:
            continue
        distinct = {d.get("realInputTokens") for d in records
                    if d.get("realInputTokens") is not None}
        # A stuck loop resends the same 1–N fixed windows over and over.
        # Ratio of records to distinct prompt sizes >= 50 is a strong signal;
        # small absolute distinct counts also catch the classic case.
        if not distinct:
            continue
        ratio = len(records) / len(distinct)
        if ratio >= 50 or len(distinct) <= 10:
            idle_keys.add(key)

    real, idle, placeholder = [], [], []
    for i, d in enumerate(rows):
        if i in placeholder_idx:
            placeholder.append(d)
            continue
        key = (d.get("source"), d.get("model") or d.get("modelId"),
               d.get("sessionId"))
        if key in idle_keys:
            idle.append(d)
        else:
            real.append(d)
    return real, idle, placeholder


def summarize_idle_window(idle):
    """Describe an idle burst compactly: (source, model, count, time window, sessionId)."""
    if not idle:
        return []
    groups = collections.defaultdict(list)
    for d in idle:
        key = (d.get("source", "?"), d.get("model") or d.get("modelId") or "?")
        groups[key].append(d)
    out = []
    for (src, model), records in sorted(groups.items(), key=lambda x: -len(x[1])):
        times = sorted(r.get("timestamp", "") for r in records if r.get("timestamp"))
        span = f"{times[0][:19]} → {times[-1][:19]}" if times else "?"
        sids = {r.get("sessionId") for r in records if r.get("sessionId")}
        sid_note = f" · {len(sids)} sessionId" + ("s" if len(sids) != 1 else "")
        out.append(f"{len(records):,} {src}/{model}  ({span}{sid_note})")
    return out


# ---------------------------------------------------------------- Source B
SHUTDOWN_RE = re.compile(
    r"'(?P<model>[^']+)': ShutdownModelMetric\("
    r"requests=ShutdownModelMetricRequests\(cost=(?P<cost>[\d.]+), count=(?P<count>\d+)\), "
    r"usage=ShutdownModelMetricUsage\("
    r"cache_read_tokens=(?P<cr>\d+), cache_write_tokens=(?P<cw>\d+), "
    r"input_tokens=(?P<inp>\d+), output_tokens=(?P<out>\d+), "
    r"reasoning_tokens=(?P<reason>\d+)\)"
)
SESSID_RE = re.compile(r"session_id='([^']+)'")


def mine_clio_shutdown():
    """Return dict keyed by (instance, session_id) -> {model: metrics}.

    Checkpoints are cumulative snapshots; keep the max per (instance, session, model).
    """
    best = {}
    pat = os.path.join(WS, ".discovery/engine/clio/checkpoint/*/*/*/*/conversation_history.json")
    for f in glob.glob(pat):
        instance = f.split(os.sep)[-4]
        try:
            events = json.load(open(f, encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for ev in events:
            if ev.get("type") != "SessionEventType.SESSION_SHUTDOWN":
                continue
            data = ev.get("data") or ""
            sm = SESSID_RE.search(data)
            sid = sm.group(1) if sm else "?"
            for m in SHUTDOWN_RE.finditer(data):
                key = (instance, sid, m.group("model"))
                rec = {
                    "cost": float(m.group("cost")),
                    "requests": int(m.group("count")),
                    "cache_read": int(m.group("cr")),
                    "cache_write": int(m.group("cw")),
                    "input": int(m.group("inp")),
                    "output": int(m.group("out")),
                    "reasoning": int(m.group("reason")),
                }
                prev = best.get(key)
                if prev is None or rec["input"] + rec["output"] > prev["input"] + prev["output"]:
                    best[key] = rec
    return best


# ------------------------------------------------ Source B2: per-message model
MSG_ID_RE = re.compile(r"message_id='([^']+)'")
MSG_MODEL_RE = re.compile(r"model='([^']+)'")
MSG_OUT_RE = re.compile(r"output_tokens=(\d+)")


def mine_assistant_messages():
    """Aggregate per-model output from ASSISTANT_MESSAGE events across all
    conversation histories.

    This gives ~100% coverage of Clio's model attribution — matching stderr's
    total output within ~0.2% — and catches models that SESSION_SHUTDOWN's
    rollup misses (e.g. models with few sessions or no clean shutdowns).

    Dedupes on (instance, message_id) because checkpoints are cumulative
    snapshots and the same message can appear across many snapshot files.

    Returns {model: {"messages": N, "output": N}}.
    """
    seen = set()
    per_model = collections.defaultdict(lambda: {"messages": 0, "output": 0})
    for f in glob.glob(os.path.join(
            WS, ".discovery/engine/clio/checkpoint/*/*/*/*/conversation_history.json")):
        instance = f.split(os.sep)[-4]
        try:
            events = json.load(open(f, encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for ev in events:
            if ev.get("type") != "SessionEventType.ASSISTANT_MESSAGE":
                continue
            data = ev.get("data") or ""
            mid = MSG_ID_RE.search(data)
            mod = MSG_MODEL_RE.search(data)
            out = MSG_OUT_RE.search(data)
            if not (mid and mod and out):
                continue
            key = (instance, mid.group(1))
            if key in seen:
                continue
            seen.add(key)
            per_model[mod.group(1)]["messages"] += 1
            per_model[mod.group(1)]["output"] += int(out.group(1))
    return dict(per_model)


def derive_multipliers_from_shutdown(best):
    """Derive per-model empirical billing multipliers from SHUTDOWN's cost/requests.

    NOTE: In observed workspaces this does NOT recover the true nominal
    multipliers because SHUTDOWN's `cost` field is empirical billing with
    cache-read discounts and free-tier absorption applied — not a per-request
    multiplier signal. See `references/traps.md#9`. Kept for diagnostic use in
    the JSON dataset; the terse output uses static multipliers only.

    Returns {model: multiplier}.
    """
    per_model = collections.defaultdict(lambda: {"cost": 0.0, "requests": 0})
    for (_i, _s, model), r in best.items():
        per_model[model]["cost"] += r["cost"]
        per_model[model]["requests"] += r["requests"]
    return {m: round(v["cost"] / v["requests"], 4)
            for m, v in per_model.items() if v["requests"] > 0}


def shutdown_cost_per_model(best):
    """Aggregate SHUTDOWN's recorded (empirical) cost per model.

    This IS the actual billed cost per Clio's telemetry (post cache/tier
    discounts), not a nominal multiplier product. Emit into the JSON
    dataset for reference. Returns {model: total_cost}.
    """
    per_model = collections.Counter()
    for (_i, _s, model), r in best.items():
        per_model[model] += r["cost"]
    return dict(per_model)


# ---------------------------------------------------------------- Source C
def mine_acp():
    out = []
    pat = os.path.join(WS, ".discovery/engine/copilot-cli/logs/*/*/copilot-stdout.log")
    for f in glob.glob(pat):
        agent, run = f.split(os.sep)[-3], f.split(os.sep)[-2]
        for line in open(f, encoding="utf-8", errors="replace"):
            line = line.strip()
            if not line or '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            u = (d.get("result") or {}).get("usage")
            if u:
                out.append({"agent": agent, "run": run,
                            "stopReason": (d.get("result") or {}).get("stopReason"), **u})
    return out


# ---------------------------------------------------------------- Source D
def mine_prompts():
    out = []
    for f in glob.glob(os.path.join(WS, ".discovery/engine-runs/*/*/meta.json")):
        try:
            d = json.load(open(f, encoding="utf-8", errors="replace"))
        except Exception:
            continue
        out.append({
            "engine": d.get("definitionId"),
            "adapterKind": d.get("adapterKind"),
            "instanceId": d.get("instanceId"),
            "state": d.get("state"),
            "startedAt": d.get("startedAt"),
            "completedAt": d.get("completedAt"),
            "promptChars": len(d.get("prompt") or ""),
            "prompt": d.get("prompt") or "",
        })
    return out


# ---------------------------------------------------------------- Source E
SDK_USAGE_RE = re.compile(
    r"Copilot SDK token usage: input=(\d+) output=(\d+) "
    r"cache_read=(\d+) cache_write=(\d+)"
)


def mine_clio_stderr():
    """Per-invocation usage. Each line is emitted TWICE (logger + plain INFO);
    dedupe on the exact tuple to avoid a 2x overcount."""
    out = []
    for f in glob.glob(os.path.join(WS, ".discovery/engine/clio/instances/*/clio-stderr.log")):
        inst = os.path.basename(os.path.dirname(f))
        seen = collections.Counter()
        for line in open(f, encoding="utf-8", errors="replace"):
            m = SDK_USAGE_RE.search(line)
            if not m:
                continue
            key = tuple(int(x) for x in m.groups())
            seen[key] += 1
            if seen[key] > 1:
                continue
            out.append({"instance": inst, "input": key[0], "output": key[1],
                        "cache_read": key[2], "cache_write": key[3]})
    return out


# ---------------------------------------------------------------- Source F
EMBED_RE = re.compile(r'"phase": "embedding", "progress": (\d+), "total": (\d+), '
                      r'"correlationId": "([0-9a-f]+)"')
GRADE_RE = re.compile(r"grading\.agentRun\.completed outcomeId=(\S+) rubricId=(\S+) "
                      r"status=(\S+) runId=(\S+) elapsedSeconds=(\d+)")


def mine_sdk_log():
    embeds, grades = {}, []
    p = os.path.join(WS, ".discovery/logs/sdk.log")
    if not os.path.exists(p):
        return embeds, grades
    for line in open(p, encoding="utf-8", errors="replace"):
        m = EMBED_RE.search(line)
        if m and m.group(1) == m.group(2):  # completion event only
            embeds[m.group(3)] = int(m.group(2))
        g = GRADE_RE.search(line)
        if g:
            grades.append({"outcomeId": g.group(1), "rubricId": g.group(2),
                           "status": g.group(3), "runId": g.group(4),
                           "elapsedSeconds": int(g.group(5))})
    return embeds, grades


# ---------------------------------------------------------------- Source G
def mine_overhead():
    info = {}
    tp = os.path.join(WS, ".discovery/host-tools/vscode-lm-tools.json")
    if os.path.exists(tp):
        d = json.load(open(tp))
        blob = json.dumps(d.get("tools", []))
        info["toolCount"] = len(d.get("tools", []))
        info["toolSchemaChars"] = len(blob)
        info["toolSchemaApproxTokens"] = len(blob) // 4
    rp = os.path.join(WS, ".discovery/llm-router.json")
    if os.path.exists(rp):
        d = json.load(open(rp))
        info["bindings"] = {b["consumerId"]: b["deploymentId"] for b in d.get("bindings", [])}
        ctx = {}
        for prov in d.get("providers", []):
            for m in prov.get("models", []):
                c = m.get("capabilities", {})
                ctx[m["modelId"]] = (c.get("maxContextWindow"), c.get("maxOutputTokens"))
        info["modelContext"] = ctx
    return info


# ------------------------------------------------- Adapter-family comparison
def parse_iso(s):
    m = re.match(r"(.*\.)(\d+)(\+.*|Z)?$", s or "")
    if m:
        s = m.group(1) + m.group(2)[:6].ljust(6, "0") + (m.group(3) or "")
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def selected_model(path):
    """copilot-cli runs record the active model in a config_option_update."""
    sel = None
    for line in open(path, encoding="utf-8", errors="replace"):
        if "configOptions" not in line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        opts = ((d.get("params", {}).get("update", {}) or {}).get("configOptions")
                or (d.get("result", {}) or {}).get("configOptions"))
        for o in opts or []:
            if o.get("id") == "model":
                sel = o.get("currentValue")
    return sel


def compare_adapters(stderr_rows, acp, best, prompts):
    """Clio (Science Engine) vs copilot-cli (Mission Control / Generic Copilot)."""
    fam = {"clio": collections.Counter(), "copilot-cli": collections.Counter()}
    meta = {"clio": {"engines": set(), "models": set()},
            "copilot-cli": {"engines": set(), "models": set()}}

    for p in prompts:
        kind = "clio" if p.get("adapterKind") == "clio" else "copilot-cli"
        meta[kind]["engines"].add(p["engine"])
        fam[kind]["runs"] += 1
        if p.get("startedAt") and p.get("completedAt"):
            try:
                fam[kind]["wallSeconds"] += int(
                    (parse_iso(p["completedAt"]) - parse_iso(p["startedAt"])).total_seconds())
            except Exception:
                pass
        fam[kind]["promptChars"] += p["promptChars"]

    for r in stderr_rows:
        f = fam["clio"]
        f["calls"] += 1
        f["input"] += r["input"]
        f["output"] += r["output"]
        f["cacheRead"] += r["cache_read"]
        f["cacheWrite"] += r["cache_write"]
    for r in best.values():
        fam["clio"]["reasoning"] += r["reasoning"]
        fam["clio"]["premiumUnits"] += r["cost"]
    for k in best:
        meta["clio"]["models"].add(k[2])

    for r in acp:
        f = fam["copilot-cli"]
        f["calls"] += 1
        f["input"] += r.get("inputTokens", 0)
        f["output"] += r.get("outputTokens", 0)
        f["reasoning"] += r.get("thoughtTokens", 0)
        f["cacheRead"] += r.get("cachedReadTokens", 0)
        f["cacheWrite"] += r.get("cachedWriteTokens", 0)
    for f in glob.glob(os.path.join(WS, ".discovery/engine/copilot-cli/logs/*/*/copilot-stdout.log")):
        m = selected_model(f)
        if m:
            meta["copilot-cli"]["models"].add(m)
    return fam, meta


def per_agent_acp(acp):
    agg = collections.defaultdict(collections.Counter)
    for r in acp:
        a = agg[r["agent"]]
        a["prompts"] += 1
        for src, dst in (("inputTokens", "input"), ("outputTokens", "output"),
                         ("thoughtTokens", "reasoning"),
                         ("cachedReadTokens", "cacheRead"),
                         ("cachedWriteTokens", "cacheWrite")):
            a[dst] += r.get(src, 0)
    return agg


def main():
    # ---- A
    rows_journal, bad, recovered = mine_journal()
    hdr(f"A. TokenJournal  ({len(rows_journal)} records | {bad} corrupt lines, "
        f"{recovered} salvaged by fragment recovery)")
    _v(f"   path: {JOURNAL}")
    real_journal, idle_journal, placeholder_journal = classify_journal(rows_journal)
    if idle_journal:
        _v(f"   ⚠ suppressed {len(idle_journal):,} records matching Discovery App "
           f"poll-loop signature")
        for line in summarize_idle_window(idle_journal):
            _v(f"     {line}")
    if placeholder_journal:
        _v(f"   suppressed {len(placeholder_journal):,} placeholder / zero-token records")
    agg = collections.defaultdict(lambda: [0, 0, 0])
    for d in rows_journal:
        k = (d.get("source"), d.get("model") or d.get("modelId"), d.get("tokenSource", "n/a"))
        a = agg[k]
        a[0] += 1
        a[1] += j_in(d)
        a[2] += j_out(d)
    tbl = [[k[0], k[1], k[2], f"{v[0]:,}", f"{v[1]:,}", f"{v[2]:,}"]
           for k, v in sorted(agg.items(), key=lambda x: -x[1][1])]
    fmt(tbl, ["source", "model", "tokenSource", "calls", "in", "out"])

    # ---- B
    best = mine_clio_shutdown()
    hdr(f"B. Clio SESSION_SHUTDOWN model_metrics  ({len(best)} instance/session/model rows)")
    agg = collections.defaultdict(lambda: collections.Counter())
    for (_inst, _sid, model), r in best.items():
        for k, v in r.items():
            agg[model][k] += v
    if WITH_COST:
        tbl = [[m, f"{v['requests']:,}", f"{v['cost']:,.1f}", f"{v['input']:,}",
                f"{v['output']:,}", f"{v['reasoning']:,}", f"{v['cache_read']:,}",
                f"{v['cache_write']:,}"]
               for m, v in sorted(agg.items(), key=lambda x: -x[1]["input"])]
        fmt(tbl, ["model", "requests", "premium$", "input", "output", "reasoning",
                  "cache_read", "cache_write"])
    else:
        tbl = [[m, f"{v['requests']:,}", f"{v['input']:,}",
                f"{v['output']:,}", f"{v['reasoning']:,}", f"{v['cache_read']:,}",
                f"{v['cache_write']:,}"]
               for m, v in sorted(agg.items(), key=lambda x: -x[1]["input"])]
        fmt(tbl, ["model", "requests", "input", "output", "reasoning",
                  "cache_read", "cache_write"])
    tot = collections.Counter()
    for v in agg.values():
        tot.update(v)
    if WITH_COST:
        note(f"\n   TOTAL: {tot['requests']:,} requests | {tot['cost']:,.1f} premium units | "
             f"in {tot['input']:,} | out {tot['output']:,} | "
             f"cache_read {tot['cache_read']:,} | cache_write {tot['cache_write']:,}")
    else:
        note(f"\n   TOTAL: {tot['requests']:,} requests | "
             f"in {tot['input']:,} | out {tot['output']:,} | "
             f"cache_read {tot['cache_read']:,} | cache_write {tot['cache_write']:,}")

    # ---- C
    acp = mine_acp()
    hdr(f"C. Copilot CLI ACP prompt usage  ({len(acp)} completed prompts)")
    agg = collections.defaultdict(lambda: collections.Counter())
    for r in acp:
        a = agg[r["agent"]]
        a["n"] += 1
        for k in ("inputTokens", "outputTokens", "totalTokens", "thoughtTokens",
                  "cachedReadTokens", "cachedWriteTokens"):
            a[k] += r.get(k, 0)
    tbl = [[m, f"{v['n']:,}", f"{v['inputTokens']:,}", f"{v['outputTokens']:,}",
            f"{v['thoughtTokens']:,}", f"{v['cachedReadTokens']:,}",
            f"{v['cachedWriteTokens']:,}"]
           for m, v in sorted(agg.items(), key=lambda x: -x[1]["inputTokens"])]
    fmt(tbl, ["agent", "prompts", "in", "out", "thought", "cache_read", "cache_write"])

    # ---- D
    prompts = mine_prompts()
    hdr(f"D. Engine prompts  ({len(prompts)} runs)")
    tbl = [[p["engine"], p["instanceId"][:8], p["state"], f"{p['promptChars']:,}",
            (p["prompt"][:60].replace("\n", " ") + "...") if p["prompt"] else ""]
           for p in sorted(prompts, key=lambda x: x["startedAt"] or "")]
    fmt(tbl, ["engine", "instance", "state", "promptChars", "prompt (head)"])

    # ---- E
    stderr_rows = mine_clio_stderr()
    hdr(f"E. Clio stderr per-invocation Copilot SDK usage  ({len(stderr_rows)} invocations, deduped)")
    agg = collections.defaultdict(lambda: collections.Counter())
    for r in stderr_rows:
        a = agg[r["instance"]]
        a["n"] += 1
        for k in ("input", "output", "cache_read", "cache_write"):
            a[k] += r[k]
    tbl = [[i[:8], f"{v['n']:,}", f"{v['input']:,}", f"{v['output']:,}",
            f"{v['cache_read']:,}", f"{v['cache_write']:,}"]
           for i, v in sorted(agg.items(), key=lambda x: -x[1]["input"])]
    fmt(tbl, ["instance", "calls", "input", "output", "cache_read", "cache_write"])
    tot = collections.Counter()
    for v in agg.values():
        tot.update(v)
    _v(f"\n   TOTAL: {tot['n']:,} invocations | in {tot['input']:,} | out {tot['output']:,} | "
       f"cache_read {tot['cache_read']:,} | cache_write {tot['cache_write']:,}")
    if tot["input"]:
        _v(f"   cache hit rate: {tot['cache_read'] / tot['input'] * 100:.1f}% of input served from cache")

    # ---- F
    embeds, grade_runs = mine_sdk_log()
    hdr(f"F. sdk.log — embedding + grading exhaust")
    _v(f"   graphrag-zero index jobs: {len(embeds)}")
    for cid, n in sorted(embeds.items(), key=lambda x: -x[1]):
        _v(f"     {cid[:12]}  {n:>9,} embeddings")
    _v(f"   TOTAL embeddings computed: {sum(embeds.values()):,} "
       f"(NOT captured by any token journal)")
    _v(f"\n   agentic grading runs: {len(grade_runs)}")
    for g in grade_runs[:6]:
        _v(f"     run {g['runId'][:8]}  outcome {g['outcomeId'][:8]}  "
           f"{g['status']}  {g['elapsedSeconds']}s")

    # ---- G
    ov = mine_overhead()
    hdr("G. Fixed prompt overhead + routing config")
    _v(f"   host tool schemas: {ov.get('toolCount')} tools, "
       f"{ov.get('toolSchemaChars'):,} chars ~ {ov.get('toolSchemaApproxTokens'):,} tokens")
    _v("   -> injected into EVERY request; explains large input counts")
    _v(f"\n   consumer -> deployment bindings ({len(ov.get('bindings', {}))}):")
    for c, dep in ov.get("bindings", {}).items():
        _v(f"     {c:20} -> {dep}")

    # ---- H: adapter-family comparison
    fam, meta = compare_adapters(stderr_rows, acp, best, prompts)
    hdr("H. CLIO (Science Engine)  vs  COPILOT-CLI (Mission Control / Generic Copilot)")
    c, g = fam["clio"], fam["copilot-cli"]

    def ratio(a, b):
        return f"{a / b:.1f}x" if b else "n/a"

    cmp_rows = [
        ["engines", ", ".join(sorted(meta["clio"]["engines"])) or "-",
         ", ".join(sorted(meta["copilot-cli"]["engines"])) or "-", ""],
        ["models", ", ".join(sorted(meta["clio"]["models"])) or "-",
         ", ".join(sorted(meta["copilot-cli"]["models"])) or "-", ""],
        ["runs", f"{c['runs']:,}", f"{g['runs']:,}", ratio(c["runs"], g["runs"])],
        ["LLM calls", f"{c['calls']:,}", f"{g['calls']:,}", ratio(c["calls"], g["calls"])],
        ["input tokens", f"{c['input']:,}", f"{g['input']:,}", ratio(c["input"], g["input"])],
        ["output tokens", f"{c['output']:,}", f"{g['output']:,}", ratio(c["output"], g["output"])],
        ["reasoning tokens", f"{c['reasoning']:,}", f"{g['reasoning']:,}",
         ratio(c["reasoning"], g["reasoning"])],
        ["cache read", f"{c['cacheRead']:,}", f"{g['cacheRead']:,}", ""],
        ["cache write", f"{c['cacheWrite']:,}", f"{g['cacheWrite']:,}", ""],
        ["cache hit rate",
         f"{c['cacheRead'] / c['input'] * 100:.1f}%" if c["input"] else "-",
         f"{g['cacheRead'] / g['input'] * 100:.1f}%" if g["input"] else "-", ""],
        ["input:output ratio",
         f"{c['input'] / c['output']:.0f}:1" if c["output"] else "-",
         f"{g['input'] / g['output']:.0f}:1" if g["output"] else "-", ""],
        ["wall clock", f"{c['wallSeconds'] / 3600:,.1f} h", f"{g['wallSeconds'] / 3600:,.1f} h",
         ratio(c["wallSeconds"], g["wallSeconds"])],
        ["prompt chars", f"{c['promptChars']:,}", f"{g['promptChars']:,}", ""],
        ["input / run",
         f"{c['input'] // c['runs']:,}" if c["runs"] else "-",
         f"{g['input'] // g['runs']:,}" if g["runs"] else "-", ""],
        ["input / hour",
         f"{c['input'] / (c['wallSeconds'] / 3600):,.0f}" if c["wallSeconds"] else "-",
         f"{g['input'] / (g['wallSeconds'] / 3600):,.0f}" if g["wallSeconds"] else "-", ""],
    ]
    if WITH_COST:
        cmp_rows.insert(-2,
            ["recorded cost", f"{c['premiumUnits']:,.1f} premium units",
             "not recorded (derive from multiplier)", ""])
    fmt(cmp_rows, ["metric", "CLIO / science-engine", "COPILOT-CLI", "C:CC"])
    note("\n   NOTE: the two families are measured by DIFFERENT mechanisms —")
    note("   Clio = per-invocation stderr lines; copilot-cli = per-prompt ACP usage blocks.")
    note("   'LLM calls' is therefore not strictly like-for-like; token totals are.")

    # ---- H3 (feature-flagged): derived cost per model, using ACP multipliers
    if WITH_COST:
        multipliers = load_multipliers()
        hdr("H3. Derived cost per model (--with-cost)")
        cost_rows = []
        missing = []
        # Clio side: use SESSION_SHUTDOWN request count (agg from B).
        clio_model_requests = collections.Counter()
        for (_inst, _sid, model), r in best.items():
            clio_model_requests[model] += r["requests"]
        for model, reqs in clio_model_requests.most_common():
            mult = multipliers.get(model)
            if mult is None:
                missing.append(model)
                cost_rows.append(["clio", model, f"{reqs:,}", "?", "?"])
            else:
                cost_rows.append(["clio", model, f"{reqs:,}", f"{mult}x",
                                  f"{reqs * mult:,.1f}"])
        # copilot-cli side: one selected model per stdout log; count prompts per log.
        cli_model_requests = collections.Counter()
        for f in glob.glob(os.path.join(WS, ".discovery/engine/copilot-cli/logs/*/*/copilot-stdout.log")):
            sel = selected_model(f)
            if not sel:
                continue
            prompts_in_log = 0
            for line in open(f, encoding="utf-8", errors="replace"):
                if '"usage"' in line and '"result"' in line:
                    prompts_in_log += 1
            cli_model_requests[sel] += prompts_in_log
        for model, reqs in cli_model_requests.most_common():
            mult = multipliers.get(model)
            if mult is None:
                missing.append(model)
                cost_rows.append(["copilot-cli", model, f"{reqs:,}", "?", "?"])
            else:
                cost_rows.append(["copilot-cli", model, f"{reqs:,}", f"{mult}x",
                                  f"{reqs * mult:,.1f}"])
        fmt(cost_rows, ["adapter", "model", "requests", "multiplier", "premium units"])
        if missing:
            note(f"\n   models missing from multiplier catalog: {', '.join(sorted(set(missing)))}")
            note("   (see references/billing_multipliers.json to add)")

    hdr("H2. copilot-cli breakdown by agent (Mission Control vs Generic Copilot vs catalog)")
    agg = per_agent_acp(acp)
    agent_rows = [[a, f"{v['prompts']:,}", f"{v['input']:,}", f"{v['output']:,}",
             f"{v['reasoning']:,}", f"{v['cacheRead']:,}",
             f"{v['cacheRead'] / v['input'] * 100:.1f}%" if v["input"] else "-"]
            for a, v in sorted(agg.items(), key=lambda x: -x[1]["input"])]
    fmt(agent_rows, ["agent", "prompts", "input", "output", "reasoning", "cache read", "hit rate"])

    # Never write into the installed skill directory: it may be read-only (plugin
    # installs) and mined output can contain workspace paths and full agent prompts.
    # Default to the current working directory; --json overrides.
    outp = JSON_PATH or os.path.join(os.getcwd(), "token_usage_mined.json")
    with open(outp, "w") as fh:
        json.dump({
            "workspace": WS,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "with_cost": WITH_COST,
            "journal": rows_journal,
            "clio_shutdown": [{"instance": k[0], "sessionId": k[1], "model": k[2], **v}
                              for k, v in best.items()],
            "acp_usage": acp,
            "engine_prompts": prompts,
            "clio_stderr_usage": stderr_rows,
            "embeddings": embeds,
            "grade_runs": grade_runs,
            "overhead": ov,
            "adapter_comparison": {k: dict(v) for k, v in fam.items()},
            "adapter_meta": {k: {kk: sorted(vv) for kk, vv in v.items()}
                             for k, v in meta.items()},
            "journal_classification": {
                "real": len(real_journal),
                "idle_suppressed": len(idle_journal),
                "placeholder_suppressed": len(placeholder_journal),
                "idle_windows": summarize_idle_window(idle_journal),
            },
            "assistant_messages_per_model": mine_assistant_messages(),
            "shutdown_cost_per_model": shutdown_cost_per_model(best),
            "empirical_multipliers_diagnostic": derive_multipliers_from_shutdown(best),
        }, fh, indent=1)
    _v(f"\nWrote normalized dataset -> {outp}")

    _terse_summary(
        stderr_rows=stderr_rows, best=best, acp=acp, prompts=prompts,
        real_journal=real_journal, idle_journal=idle_journal,
        placeholder_journal=placeholder_journal,
        embeds=embeds,
    )

    if REPORT_PATH:
        _write_report()


def _humanize(n):
    """Compact number rendering: 1.2M, 830K, 5.4K, or raw."""
    if n is None:
        return "-"
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:,}"


def _terse_summary(*, stderr_rows, best, acp, prompts, real_journal,
                   idle_journal, placeholder_journal, embeds):
    """Always-visible top-line read-out. Kept intentionally short."""
    # --- Clio per-model: ASSISTANT_MESSAGE gives ~100% attribution coverage
    # (matches stderr output within ~0.2% in observed workspaces, catches
    #  models that SESSION_SHUTDOWN's rollup misses like sonnet-5).
    clio_total = collections.Counter()
    for r in stderr_rows:
        for k in ("input", "output", "cache_read"):
            clio_total[k] += r[k]
    clio_calls = len(stderr_rows)
    clio_runs = sum(1 for p in prompts if p.get("adapterKind") == "clio")
    clio_hit = (clio_total["cache_read"] / clio_total["input"] * 100
                if clio_total["input"] else 0)
    clio_msg_agg = mine_assistant_messages()
    msg_total_output = sum(v["output"] for v in clio_msg_agg.values())

    # --- copilot-cli per-model (from C, model resolved from selected_model)
    cli_agg = collections.defaultdict(collections.Counter)
    for f in glob.glob(os.path.join(WS, ".discovery/engine/copilot-cli/logs/*/*/copilot-stdout.log")):
        m = selected_model(f) or "unknown"
        for line in open(f, encoding="utf-8", errors="replace"):
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            u = (d.get("result") or {}).get("usage")
            if not u:
                continue
            a = cli_agg[m]
            a["prompts"] += 1
            a["input"] += u.get("inputTokens", 0)
            a["output"] += u.get("outputTokens", 0)
            a["cache_read"] += u.get("cachedReadTokens", 0)
    cli_total = collections.Counter()
    for a in cli_agg.values():
        for k, v in a.items():
            cli_total[k] += v
    cli_runs = sum(1 for p in prompts if p.get("adapterKind") == "copilot-cli")
    cli_hit = (cli_total["cache_read"] / cli_total["input"] * 100
               if cli_total["input"] else 0)

    # --- Interactive plane (real records only)
    inter_agg = collections.defaultdict(lambda: [0, 0, 0])
    for d in real_journal:
        model = d.get("model") or d.get("modelId") or "?"
        a = inter_agg[model]
        a[0] += 1
        a[1] += j_in(d)
        a[2] += j_out(d)
    inter_total = [sum(v[i] for v in inter_agg.values()) for i in range(3)]

    # ----- print
    # Static-only multipliers. Clio cost = SHUTDOWN request count × static multiplier
    # (SHUTDOWN's own `cost` field is empirical billing with cache/tier discounts,
    #  not a per-request multiplier signal — see traps.md).
    static_mults = load_multipliers() if WITH_COST else {}
    shutdown_reqs_per_model = collections.Counter()
    if WITH_COST:
        for (_i, _s, model), r in best.items():
            shutdown_reqs_per_model[model] += r["requests"]

    print()
    print(f"Engine — Clio           {clio_runs} runs · {clio_calls} SDK calls · "
          f"{_humanize(clio_total['input'])} in / {_humanize(clio_total['output'])} out"
          f" · {clio_hit:.1f}% cached")
    if clio_msg_agg:
        print("  per-model (from ASSISTANT_MESSAGE events; "
              "input apportioned by output share):")
    for m, agg in sorted(clio_msg_agg.items(), key=lambda x: -x[1]["output"]):
        share = agg["output"] / msg_total_output if msg_total_output else 0
        appx_in = int(clio_total["input"] * share) if msg_total_output else 0
        cost_suffix = ""
        if WITH_COST:
            mult = static_mults.get(m)
            reqs = shutdown_reqs_per_model.get(m)
            if mult is None:
                cost_suffix = f" ·       ? units (model not in multiplier catalog)"
            elif reqs is None:
                cost_suffix = f" ·       ? units ({mult:g}x, no request count in SHUTDOWN)"
            else:
                cost_suffix = f" · {reqs * mult:>7,.1f} units ({reqs:,} req × {mult:g}x)"
        print(f"    {m:22} {agg['messages']:>5,} msgs · {share*100:>4.1f}% · "
              f"~{_humanize(appx_in):>6} in / {_humanize(agg['output']):>7} out"
              f"{cost_suffix}")

    print()
    print(f"Engine — copilot-cli    {cli_runs} runs · {cli_total['prompts']} prompts · "
          f"{_humanize(cli_total['input'])} in / {_humanize(cli_total['output'])} out"
          f" · {cli_hit:.1f}% cached")
    if cli_agg:
        print("  per-model:")
    for m, a in sorted(cli_agg.items(), key=lambda x: -x[1]["input"]):
        hit = a["cache_read"] / a["input"] * 100 if a["input"] else 0
        cost_suffix = ""
        if WITH_COST:
            mult = static_mults.get(m)
            if mult is None:
                cost_suffix = f" ·       ? units (not in multiplier catalog)"
            else:
                cost_suffix = f" · {a['prompts'] * mult:>7,.1f} units ({a['prompts']:,} prm × {mult:g}x)"
        print(f"    {m:22} {a['prompts']:>5,} prompts · "
              f"{_humanize(a['input']):>7} in / {_humanize(a['output']):>7} out"
              f" · {hit:>5.1f}% cached{cost_suffix}")

    print()
    print(f"Interactive             {inter_total[0]:,} calls · "
          f"{_humanize(inter_total[1])} in / {_humanize(inter_total[2])} out")
    if inter_agg:
        print("  per-model:")
    for m, v in sorted(inter_agg.items(), key=lambda x: -x[1][1]):
        print(f"    {m:22} {v[0]:>5,} calls · "
              f"{_humanize(v[1]):>7} in / {_humanize(v[2]):>7} out")
    if idle_journal:
        # Compact the poll-loop callout
        windows = summarize_idle_window(idle_journal)
        print(f"    ⚠ suppressed {len(idle_journal):,} records matching the Discovery App "
              f"poll-loop signature")
        print(f"       (same sessionId, many records, few distinct realInputTokens):")
        for w in windows:
            print(f"        {w}")

    if embeds:
        total = sum(embeds.values())
        print()
        print(f"Embeddings              {_humanize(total)} ({len(embeds)} index jobs) "
              f"— uncounted in any journal")
    print()


def _write_report():
    """Flush the accumulated markdown buffer to REPORT_PATH."""
    today = datetime.date.today().isoformat()
    header = [
        f"# Discovery App — Token Usage Report",
        "",
        f"**Workspace:** `{WS}`",
        f"**Generated:** {today}",
        f"**Cost data included:** {'yes' if WITH_COST else 'no (default; token counts only)'}",
        "",
        "> Generated by the `discovery-token-usage` skill "
        "(https://github.com/timothymeyers/discovery-tools).",
        "",
        "---",
    ]
    with open(REPORT_PATH, "w") as fh:
        fh.write("\n".join(header + _REPORT_BUF))
        fh.write("\n")
    print(f"Wrote markdown report -> {REPORT_PATH}")


def _parse_args():
    p = argparse.ArgumentParser(
        description="Mine Discovery App .discovery/ exhaust for token usage.")
    p.add_argument("workspace", nargs="?", default=os.getcwd(),
                   help="Discovery workspace root (contains .discovery/). Defaults to cwd.")
    p.add_argument("--with-cost", action="store_true",
                   help="FEATURE FLAG: include cost / billing-multiplier data. "
                        "Off by default; only surface when explicitly requested.")
    p.add_argument("--report", metavar="PATH",
                   help="Also emit a markdown report to this path.")
    p.add_argument("--json", metavar="PATH", dest="json_path",
                   help="Override output path for the normalized JSON dataset.")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Also print the full detailed source tables (A/B/C/D/E/F/G/H/H2). "
                        "By default only the terse summary is printed.")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    WS = os.path.abspath(os.path.expanduser(args.workspace))
    WITH_COST = args.with_cost
    REPORT_PATH = os.path.abspath(os.path.expanduser(args.report)) if args.report else None
    JSON_PATH = os.path.abspath(os.path.expanduser(args.json_path)) if args.json_path else None
    VERBOSE = args.verbose
    if not os.path.isdir(os.path.join(WS, ".discovery")):
        print(f"ERROR: {WS}/.discovery/ not found. Pass a Discovery workspace root.",
              file=sys.stderr)
        sys.exit(2)
    main()
