"""BYO brain — one interface, any model.

An Omo desk today can only run the CLIs installed on its own PC (Hermes,
Claude Code, Codex). Each of those is a *subscription the player already
pays for* somewhere else — a Muse plan, a Claude Pro plan, a ChatGPT plan —
and none of them is reachable from the bridge except by typing into its
terminal.

This module is the adapter that fixes that, without picking a winner:

* ``complete(backend, model, messages, tools)`` works the same for every
  backend. A desk agent asks for a completion; it does not care which
  model answers, only that the answer comes back as text + optional tool
  calls in the fixed World Sense tool format.
* Backends:
  - ``echo`` — deterministic test backend. No network, no cost. Returns
    the first tool call the prompt asks for, or a canned reply. This is
    what the tests use, and what a new contributor runs first.
  - ``muse`` — Muse (this project's own agent family) via its CLI, when
    installed. The point erfan asked Yifan about: "borrow intelligence
    from subscriptions we already have". A Muse subscription is one such
    subscription. No key is stored by the bridge; the CLI owns auth.
  - ``claude`` / ``codex`` / ``hermes`` — the CLIs Omo already ships tabs
    for, driven non-interactively (``claude -p``, ``codex exec``,
    ``hermes chat --print``). Same rule: the CLI owns auth.
  - ``openrouter`` — any model on OpenRouter via its OpenAI-compatible
    ``/chat/completions`` endpoint. The API key comes from the
    ``OPENROUTER_API_KEY`` environment variable (never from a file the
    bridge writes, never logged). This is the "we provide the API" path:
    the host holds one OpenRouter key, players pick a model, the cost
    ledger counts every call.
* Every call is recorded in the cost ledger (tokens + USD when known).
  A daily cap (``OMO_COST_DAILY_CAP_USD``) refuses the next call instead
  of silently spending.

Security: backends are allow-listed by name. The bridge never shells out
with unsanitised input, never logs a key, and never sends a prompt to a
backend the workspace did not choose. ``openrouter`` is off unless the
key is present in the environment.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request

import cost_ledger

BACKENDS = ("echo", "muse", "claude", "codex", "hermes", "openrouter")


class BrainError(Exception):
    pass


def available_backends():
    """Which backends could run right now (binary present / key present)."""
    import shutil
    out = {"echo": True}
    for name, binary in (("muse", "muse"), ("claude", "claude"),
                         ("codex", "codex"), ("hermes", "hermes")):
        out[name] = shutil.which(binary) is not None
    out["openrouter"] = bool(os.environ.get("OPENROUTER_API_KEY"))
    return out


def _run_cli(binary, argv, prompt, timeout=120):
    try:
        proc = subprocess.run([binary] + argv, input=prompt.encode(),
                              capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise BrainError(f"{binary} is not installed; see the install hint for that tab")
    except subprocess.TimeoutExpired:
        raise BrainError(f"{binary} timed out after {timeout}s")
    text = proc.stdout.decode("utf-8", "replace").strip()
    if proc.returncode != 0 and not text:
        raise BrainError(f"{binary} failed: {proc.stderr.decode('utf-8', 'replace')[:500]}")
    return text


def _echo(messages, tools):
    """Deterministic test brain: echoes the last user message's intent."""
    user = next((m.get("content", "") for m in reversed(messages)
                 if m.get("role") == "user"), "")
    # If the prompt names a tool, return a call to it with empty args — the
    # caller validates args before anything runs. Otherwise, plain text.
    for tool in tools or []:
        if tool.get("name") in user:
            return {"text": f"echo: calling {tool['name']}",
                    "tool_calls": [{"name": tool["name"], "args": {}}],
                    "prompt_tokens": len(user) // 4,
                    "completion_tokens": 8}
    return {"text": f"echo: I see {len(user)} chars of world. No tool asked for.",
            "tool_calls": [], "prompt_tokens": len(user) // 4,
            "completion_tokens": 12}


def _openrouter(model, messages, tools, timeout=120):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise BrainError("OPENROUTER_API_KEY is not set; set it in the bridge environment to use OpenRouter")
    body = {"model": model, "messages": messages}
    if tools:
        body["tools"] = [{"type": "function",
                          "function": {"name": t["name"],
                                       "description": t.get("description", ""),
                                       "parameters": {"type": "object",
                                                      "properties": {}}}} for t in tools]
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "HTTP-Referer": "https://omo.space",
                 "X-Title": "Omo Space World Sense"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:  # network / 4xx / 5xx — report, don't retry here
        raise BrainError(f"OpenRouter request failed: {exc}")
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    usage = data.get("usage") or {}
    tool_calls = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except ValueError:
            args = {}
        tool_calls.append({"name": fn.get("name"), "args": args})
    cost = cost_ledger.estimate_openrouter_cost_usd(
        model, int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0))
    return {"text": msg.get("content") or "",
            "tool_calls": tool_calls,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "cost_usd": cost}


def complete(backend, model, messages, tools=None, workspace="?",
             data_dir=None):
    """One completion from any backend. Returns text + tool_calls.

    ``model`` is advisory for CLI backends (they use their own default)
    and required for ``openrouter``. ``workspace`` is only used for the
    cost ledger. Raises BrainError on any failure.
    """
    if backend not in BACKENDS:
        raise BrainError(f"unknown backend {backend!r}; one of {', '.join(BACKENDS)}")
    cap = os.environ.get("OMO_COST_DAILY_CAP_USD")
    if data_dir and cap and cost_ledger.over_daily_cap(data_dir, workspace, cap):
        raise BrainError(f"daily cost cap ${cap} reached for workspace {workspace!r}")

    if backend == "echo":
        result = _echo(messages, tools or [])
    elif backend == "openrouter":
        if not model:
            raise BrainError("openrouter needs a model, e.g. anthropic/claude-haiku-4.5")
        result = _openrouter(model, messages, tools or [])
    elif backend == "muse":
        prompt = "\n\n".join(f"{m.get('role')}: {m.get('content')}" for m in messages)
        text = _run_cli("muse", ["--print", prompt], prompt)
        result = {"text": text, "tool_calls": [],
                  "prompt_tokens": len(prompt) // 4,
                  "completion_tokens": len(text) // 4}
    elif backend == "claude":
        prompt = "\n\n".join(f"{m.get('role')}: {m.get('content')}" for m in messages)
        text = _run_cli("claude", ["-p", prompt], prompt)
        result = {"text": text, "tool_calls": [],
                  "prompt_tokens": len(prompt) // 4,
                  "completion_tokens": len(text) // 4}
    elif backend == "codex":
        prompt = "\n\n".join(f"{m.get('role')}: {m.get('content')}" for m in messages)
        text = _run_cli("codex", ["exec", prompt], prompt)
        result = {"text": text, "tool_calls": [],
                  "prompt_tokens": len(prompt) // 4,
                  "completion_tokens": len(text) // 4}
    elif backend == "hermes":
        prompt = "\n\n".join(f"{m.get('role')}: {m.get('content')}" for m in messages)
        text = _run_cli("hermes", ["chat", "--print", prompt], prompt)
        result = {"text": text, "tool_calls": [],
                  "prompt_tokens": len(prompt) // 4,
                  "completion_tokens": len(text) // 4}
    else:  # pragma: no cover — guarded by the allow-list above
        raise BrainError(f"backend {backend!r} not implemented")

    if data_dir:
        cost_ledger.record(data_dir, workspace, backend, model or backend,
                           result.get("prompt_tokens", 0),
                           result.get("completion_tokens", 0),
                           result.get("cost_usd"))
    return result
