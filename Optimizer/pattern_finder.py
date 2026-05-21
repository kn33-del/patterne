import json
import re
from pathlib import Path
from typing import List

def parse_result_log(log_text: str) -> dict:
    """
    Extracts the RESULT line from agent output and parses it into a dict.
    Expected format:
    RESULT: {strategy_used} | WNS_before: {x} | WNS_after: {y} | delta: {z} | outcome: improved/regressed/no_change
    """
    result_pattern = re.compile(
        r"RESULT:\s*(?P<strategy>[^|]+?)\s*\|\s*"
        r"WNS_before:\s*(?P<wns_before>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*\|\s*"
        r"WNS_after:\s*(?P<wns_after>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*\|\s*"
        r"delta:\s*(?P<delta>[+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*\|\s*"
        r"outcome:\s*(?P<outcome>improved|regressed|no_change)\b",
        re.IGNORECASE,
    )
    match = result_pattern.search(log_text)
    if not match:
        return {}
    return {
        "strategy": match.group("strategy").strip(),
        "wns_before": float(match.group("wns_before").strip()),
        "wns_after": float(match.group("wns_after").strip()),
        "delta": float(match.group("delta").strip()),
        "outcome": match.group("outcome").strip().lower()
    }

def update_history(history_path: str, result: dict, failing_endpoints: List[str]):
    """
    Loads history JSON, updates strategy outcome counts and endpoint failure counts, writes it back.
    """
    path = Path(history_path)
    try:
        with path.open('r', encoding='utf-8') as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        history = {"strategy_outcomes": {}, "endpoint_failures": {}}

    history.setdefault("strategy_outcomes", {})
    history.setdefault("endpoint_failures", {})

    # Update strategy outcomes
    strat = result.get("strategy")
    outcome = result.get("outcome")
    if strat and outcome:
        history["strategy_outcomes"].setdefault(strat, {})
        history["strategy_outcomes"][strat].setdefault("improved", 0)
        history["strategy_outcomes"][strat].setdefault("regressed", 0)
        history["strategy_outcomes"][strat].setdefault("no_change", 0)
        history["strategy_outcomes"][strat].setdefault(outcome, 0)
        history["strategy_outcomes"][strat][outcome] += 1

    # Update endpoint failures
    seen_endpoints = set()
    for ep in failing_endpoints:
        endpoint = str(ep).strip()
        if not endpoint or endpoint in seen_endpoints:
            continue
        seen_endpoints.add(endpoint)
        history["endpoint_failures"][endpoint] = history["endpoint_failures"].get(endpoint, 0) + 1

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        json.dump(history, f, indent=2)

def build_pattern_summary(history_path: str) -> str:
    """
    Reads history JSON and returns a plain string summary.
    """
    try:
        with open(history_path, 'r', encoding='utf-8') as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return "No pattern history available."

    lines = []
    strategy_items = sorted(history.get("strategy_outcomes", {}).items())
    for strat, outcomes in strategy_items:
        for outcome in ("improved", "regressed", "no_change"):
            count = int(outcomes.get(outcome, 0) or 0)
            if count == 0:
                continue
            lines.append(f"{strat} {outcome} {count}x.")

    endpoint_items = sorted(
        history.get("endpoint_failures", {}).items(),
        key=lambda item: (-int(item[1]), item[0]),
    )
    for ep, count in endpoint_items[:10]:
        noun = "run" if int(count) == 1 else "runs"
        lines.append(f"Endpoint {ep} failed {count} {noun}.")
    return " ".join(lines) if lines else "No pattern history available."
