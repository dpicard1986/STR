"""Live candidate search via the Claude API's web search tool.

Independent of the CSV/Gmail ingestion pipeline - queried fresh on every run,
so the digest always shows Claude's current picks (even if they repeat day to
day). Requires ANTHROPIC_API_KEY; callers should treat failures as non-fatal.
"""
from __future__ import annotations
import json, os, re

MODEL = os.environ.get("STR_AGENT_SEARCH_MODEL", "claude-opus-4-8")


def _build_prompt(cfg, n):
    s = cfg["search"]
    markets = s.get("markets")
    scope = (f"only these markets: {', '.join(markets)}" if markets
              else "any of the configured VT/NH/MA/ME ski or beach markets")
    return (
        "Search Redfin for current active for-sale listings that fit this short-term-rental "
        "investment screen:\n"
        f"- States: {', '.join(s['states'])}\n"
        f"- Price at or below ${s['max_price']:,.0f}\n"
        f"- Within {s['max_ski_miles']} miles of a ski lift base OR {s['max_beach_miles']} miles "
        "of public beach access\n"
        "- Condo, townhouse, or single-family; whole ownership or condotel only "
        "(reject quarter-share/fractional/timeshare)\n"
        f"- Scope: {scope}\n\n"
        f"Pick the {n} most promising candidates for a short-term-rental investment (favor "
        "listings that look under-priced for the area, have value-add signals in the remarks "
        "like 'as-is'/'TLC'/'priced to sell', or have had recent price cuts). For each, use the "
        "Redfin listing page as the url.\n\n"
        "After you finish searching, respond with ONLY a JSON array (no prose, no markdown "
        f"fence) of up to {n} objects, each with exactly these keys: address (string), market "
        "(best-guess town/area name), state (2-letter), price (number, no $ or commas), beds "
        "(number), baths (number), url (the Redfin listing URL), note (one sentence on why "
        "it's a candidate)."
    )


def _extract_json(text):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end != -1:
            text = text[start:end + 1]
    return json.loads(text)


def find_candidates(cfg, n=5):
    """Ask Claude to search Redfin right now and return up to n candidates with links."""
    import anthropic
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": _build_prompt(cfg, n)}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}]

    response = None
    for _ in range(4):  # server-side search loop can pause_turn; resume by resending
        response = client.messages.create(
            model=MODEL, max_tokens=4096, tools=tools, messages=messages,
        )
        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue
        break
    if response is None:
        return []

    text = "".join(b.text for b in response.content if b.type == "text")
    try:
        candidates = _extract_json(text)
    except (ValueError, json.JSONDecodeError):
        return []
    return candidates[:n]
