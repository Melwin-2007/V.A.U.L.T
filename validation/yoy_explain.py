"""Natural-language explanation generator for YoY validation results."""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx


def _template_explanation(result: dict[str, Any]) -> str:
    """Deterministic rule-based explanation that works without any LLM key."""
    metric = str(result.get("metric") or "Metric").replace("_", " ").title()
    status = result.get("status")
    severity = result.get("severity")
    pct = result.get("pct_change")
    threshold = result.get("threshold_used")
    prior_val = result.get("prior_value")
    curr_val = result.get("current_value")
    unit = result.get("unit") or ""
    anomaly_type = result.get("anomaly_type")

    if status == "not_applicable" or pct is None:
        return str(
            result.get("reason")
            or f"{metric} could not be evaluated for YoY change."
        )

    direction = "rose" if pct >= 0 else "fell"
    unit_suffix = f" {unit}" if unit else ""
    movement = f"{metric} {direction} {abs(pct):.1f}% vs prior year"
    if prior_val is not None and curr_val is not None:
        movement += f" ({prior_val}{unit_suffix} -> {curr_val}{unit_suffix})"

    if anomaly_type == "likely_unit_error":
        return (
            f"{movement}, above the {threshold}% threshold. "
            "Magnitude (~1000x) strongly indicates a unit/scale extraction error "
            "(e.g., thousands vs. millions); verify the table header and source snippet."
        )
    if anomaly_type == "sign_flip":
        return (
            f"{movement}, reversing sign compared with the prior period. "
            "Possible causes: genuine shift between profit and loss, accounting restatement, "
            "or sign extraction error; human review required."
        )
    if severity == "fail" or not result.get("passed"):
        return (
            f"{movement}, above the {threshold}% threshold. "
            "Possible causes: acquisition, divestiture, restatement, or extraction error; "
            "verify the source."
        )
    if severity == "warn":
        return (
            f"{movement}, approaching the {threshold}% review threshold. "
            "No escalation triggered, but worth noting during audit review."
        )
    return f"{movement}, within the configured {threshold}% threshold."


def _build_llm_prompt(result: dict[str, Any]) -> str:
    """Send only compact numbers and the short snippet (never entire filings)."""
    snippet = str(result.get("source_snippet") or "")[:500]
    return (
        "Explain this SEC 10-K YoY validation result in 2 concise sentences for a financial auditor. "
        "Do not invent facts; distinguish likely 1000x unit errors from genuine business swings.\n"
        f"Metric: {result.get('metric')}\n"
        f"Prior: {result.get('prior_value')} {result.get('unit') or ''}\n"
        f"Current: {result.get('current_value')} {result.get('unit') or ''}\n"
        f"YoY %: {result.get('pct_change')}%\n"
        f"Threshold %: {result.get('threshold_used')}%\n"
        f"Severity: {result.get('severity')} ({result.get('anomaly_type')})\n"
        f"Snippet: {snippet}"
    )


def _call_llm(prompt: str, api_key: str, timeout: float = 5.0) -> Optional[str]:
    """Call Gemini or Groq if LLM_API_KEY is configured; return None on any failure."""
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if not provider:
        provider = "groq" if api_key.startswith("gsk_") else "gemini"

    try:
        if provider == "groq":
            resp = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "llama-3.1-8b-instant",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 120,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            return text or None

        # Default: Google Gemini generateContent endpoint
        resp = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 120},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        return text or None
    except Exception:
        return None


def explain(result: dict[str, Any], *, timeout: float = 5.0) -> str:
    """Return a concise explanation for a YoY check result.

    Uses a deterministic template by default. If ``LLM_API_KEY`` is set in the
    environment, calls Gemini or Groq with only the numbers and source snippet,
    falling back cleanly to the template on timeout or error.
    """
    if not isinstance(result, dict):
        return "Invalid validation result."

    api_key = os.getenv("LLM_API_KEY", "").strip()
    if api_key and result.get("status") != "not_applicable":
        prompt = _build_llm_prompt(result)
        llm_text = _call_llm(prompt, api_key, timeout=timeout)
        if llm_text:
            return llm_text

    return _template_explanation(result)
