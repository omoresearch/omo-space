"""Cost ledger — every outside-model call is counted, per workspace.

Why this exists: the hosted-panel idea in docs/WORLD-SENSE.md only works if
both sides can see the bill. A flat hosting fee plus metered intelligence
needs a ledger the host and the player can both read, before any charge.

* Append-only JSONL (one line per call), never rewritten.
* A call records: time, workspace, backend, model, prompt tokens,
  completion tokens, cost in USD (when the backend reports a price, else
  ``cost_usd: null`` and the token counts still stand).
* Caps: ``OMO_COST_DAILY_CAP_USD`` (default: no cap) and a per-workspace
  monthly cap in the workspace record. Breaching a cap does not delete
  history — it refuses the *next* call with a clear error.
* No prompt or completion text is stored here. Ever. Only counts.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

_lock = threading.Lock()


def _path(data_dir):
    return os.path.join(data_dir, "cost_ledger.jsonl")


def record(data_dir, workspace, backend, model, prompt_tokens,
           completion_tokens, cost_usd=None):
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "workspace": workspace,
        "backend": backend,
        "model": model,
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
        "cost_usd": cost_usd,
    }
    with _lock:
        with open(_path(data_dir), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def totals(data_dir, workspace=None, day=None):
    """Totals for a workspace (or all), optionally for one YYYY-MM-DD day."""
    prompt = completion = 0
    cost = 0.0
    cost_known = False
    calls = 0
    path = _path(data_dir)
    if not os.path.exists(path):
        return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                "cost_usd": 0.0, "cost_known": False}
    with _lock:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if workspace and e.get("workspace") != workspace:
                    continue
                if day and not str(e.get("ts", "")).startswith(day):
                    continue
                calls += 1
                prompt += int(e.get("prompt_tokens") or 0)
                completion += int(e.get("completion_tokens") or 0)
                if e.get("cost_usd") is not None:
                    cost += float(e["cost_usd"])
                    cost_known = True
    return {"calls": calls, "prompt_tokens": prompt,
            "completion_tokens": completion, "cost_usd": round(cost, 6),
            "cost_known": cost_known}


def over_daily_cap(data_dir, workspace, cap_usd):
    """True when today's known spend for this workspace meets the cap."""
    if not cap_usd:
        return False
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    t = totals(data_dir, workspace=workspace, day=today)
    return t["cost_known"] and t["cost_usd"] >= float(cap_usd)


# --- OpenRouter price table (snapshot, not a live quote) -------------------
# OpenRouter publishes per-model prices; the bridge does NOT hard-code a
# price list for billing. When a backend returns usage with a cost, that
# cost is recorded. When it returns only tokens, cost stays null and the
# panel shows tokens + "price unknown — check OpenRouter". This table is
# only a fallback estimate, clearly labelled as such in the panel.

def estimate_openrouter_cost_usd(model, prompt_tokens, completion_tokens):
    """Very rough estimate for common models; returns None when unknown."""
    table = {
        # (input $/M tokens, output $/M tokens) — check openrouter.ai
        # before quoting anyone; these move.
        "anthropic/claude-haiku-4.5": (1.0, 5.0),
        "anthropic/claude-sonnet-4.5": (3.0, 15.0),
        "openai/gpt-5-mini": (0.25, 2.0),
        "openai/gpt-5": (1.25, 10.0),
        "google/gemini-2.5-flash": (0.30, 2.50),
        "meta-llama/llama-3.3-70b-instruct": (0.10, 0.35),
    }
    p = table.get(model)
    if not p:
        return None
    return round(prompt_tokens * p[0] / 1_000_000
                 + completion_tokens * p[1] / 1_000_000, 6)
