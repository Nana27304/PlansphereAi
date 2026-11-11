import asyncio
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import httpx
import fitz
from docx import Document as DocxDocument
from io import BytesIO
from .config import settings


def extract_document_text(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        with fitz.open(stream=content, filetype="pdf") as pdf:
            pages = [page.get_text("text") for page in pdf]
        return "\n\n".join(pages).strip()
    if suffix == ".docx":
        doc = DocxDocument(BytesIO(content))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                paragraphs.append(" | ".join(cell.text.strip() for cell in row.cells))
        return "\n".join(paragraphs).strip()
    raise ValueError("Only PDF and DOCX documents are supported")


def document_context(query: str, docs: list[Any], max_chars: int = 14000) -> str:
    terms = {t.lower().strip(".,:;!?()[]{}") for t in query.split() if len(t) > 3}
    scored = []
    for doc in docs:
        body = doc.extracted_text or ""
        if not body:
            continue
        lower = body.lower()
        score = sum(lower.count(term) for term in terms)
        scored.append((score, doc.filename, body[:10000]))
    scored.sort(key=lambda x: x[0], reverse=True)
    selected = []
    used = 0
    for _, name, body in scored[:4]:
        chunk = f"DOCUMENT: {name}\n{body[:max(0, max_chars-used)]}"
        if used + len(chunk) > max_chars:
            break
        selected.append(chunk)
        used += len(chunk)
    return "\n\n---\n\n".join(selected)


def _send_email_sync(to_email: str, code: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = "Your PlanSphere AI verification code"
    msg["From"] = settings.smtp_from_email
    msg["To"] = to_email
    msg.set_content(f"Your PlanSphere AI verification code is {code}. It expires in 10 minutes. Do not share it with anyone.")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
        if settings.smtp_use_tls:
            server.starttls()
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(msg)


async def send_verification(channel: str, destination: str, code: str) -> bool:
    if channel == "email":
        if not (settings.smtp_host and settings.smtp_from_email):
            return False
        await asyncio.to_thread(_send_email_sync, destination, code)
        return True
    if channel == "sms":
        if not (settings.twilio_account_sid and settings.twilio_auth_token and (settings.twilio_from_number or settings.twilio_messaging_service_sid)):
            return False
        url = f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json"
        data = {"To": destination, "Body": f"Your PlanSphere AI verification code is {code}. It expires in 10 minutes."}
        if settings.twilio_messaging_service_sid:
            data["MessagingServiceSid"] = settings.twilio_messaging_service_sid
        else:
            data["From"] = settings.twilio_from_number
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(url, data=data, auth=(settings.twilio_account_sid, settings.twilio_auth_token))
            response.raise_for_status()
        return True
    return False


async def tavily_search(query: str) -> list[dict[str, str]]:
    if not settings.tavily_api_key:
        return []
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {settings.tavily_api_key}", "Content-Type": "application/json"},
            json={"query": query, "search_depth": "advanced", "max_results": 6, "include_answer": False},
        )
        response.raise_for_status()
        payload = response.json()
    return [
        {"title": str(item.get("title", "Untitled")), "url": str(item.get("url", "")), "content": str(item.get("content", ""))[:1800]}
        for item in payload.get("results", [])
    ]


async def call_ai(messages: list[dict[str, str]], model_override: str | None = None) -> str:
    if not settings.openai_api_key:
        raise RuntimeError("AI provider is not configured. Add OPENAI_API_KEY to backend/.env.")
    url = settings.openai_base_url.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
            json={"model": model_override or settings.openai_model, "messages": messages, "temperature": 0.25, "max_tokens": settings.ai_max_output_tokens},
        )
        response.raise_for_status()
        data = response.json()
    return data["choices"][0]["message"]["content"]


def month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def quota_for_tier(tier: str) -> int:
    return {"normal": 20, "professional": 100, "advanced": 250}.get(tier, 20)


def enforce_monthly_quota(user: Any) -> None:
    month = month_key()
    if user.usage_month != month:
        user.usage_month = month
        user.monthly_messages = 0
    quota = quota_for_tier(user.tier)
    if user.monthly_messages >= quota:
        from fastapi import HTTPException
        raise HTTPException(status_code=429, detail=f"You have used this month's {quota} AI messages on the {user.tier.title()} plan. Upgrade or try again next month.")


def sample_size(population: int, confidence_level: float, margin_of_error: float, p: float, nonresponse_rate: float, design_effect: float) -> dict[str, Any]:
    from scipy.stats import norm
    z = float(norm.ppf(1 - (1-confidence_level)/2))
    n0 = (z*z*p*(1-p))/(margin_of_error*margin_of_error)
    corrected = n0 / (1 + ((n0 - 1) / population))
    designed = corrected * design_effect
    adjusted = designed / (1 - nonresponse_rate)
    return {
        "z_score": round(z, 4),
        "infinite_population_sample": round(n0, 2),
        "finite_population_sample": round(corrected, 2),
        "after_design_effect": round(designed, 2),
        "adjusted_for_nonresponse": round(adjusted, 2),
        "recommended_sample_size": int(__import__("math").ceil(adjusted)),
        "formula": "n0 = Z²p(1-p)/e²; n = n0/[1 + (n0-1)/N]; final n = ceil(n × design effect ÷ (1 - nonresponse rate))",
        "assumptions": {"population": population, "confidence_level": confidence_level, "margin_of_error": margin_of_error, "population_proportion": p, "design_effect": design_effect, "nonresponse_rate": nonresponse_rate},
    }


def land_use_change(baseline: dict[str, float], current: dict[str, float], baseline_year: int, current_year: int, unit: str) -> dict[str, Any]:
    classes = sorted(set(baseline) | set(current))
    for d in (baseline, current):
        if any((not isinstance(v, (int, float)) or v < 0) for v in d.values()):
            raise ValueError("Land-use areas must be non-negative numbers in one consistent unit")
    total_b, total_c = sum(baseline.values()), sum(current.values())
    rows = []
    for name in classes:
        b, c = float(baseline.get(name, 0)), float(current.get(name, 0))
        delta = c - b
        rows.append({
            "class": name,
            "baseline_area": round(b, 4), "current_area": round(c, 4), "absolute_change": round(delta, 4),
            "percentage_change": round((delta / b) * 100, 2) if b else None,
            "baseline_share_percent": round((b / total_b) * 100, 2) if total_b else None,
            "current_share_percent": round((c / total_c) * 100, 2) if total_c else None,
            "share_change_percentage_points": round(((c / total_c) - (b / total_b)) * 100, 2) if total_b and total_c else None,
        })
    return {"baseline_year": baseline_year, "current_year": current_year, "area_unit": unit, "baseline_total": round(total_b, 4), "current_total": round(total_c, 4), "net_area_change": round(total_c-total_b, 4), "results": rows,
            "interpretation_note": "Percentage change is relative to the baseline area. Share change is expressed in percentage points. A class with zero baseline area has no mathematically defined percentage change."}


GSS_PXWEB_BASE = "https://statsbank.statsghana.gov.gh/api/v1/en/PHC%202021%20StatsBank"
GSS_TOPICS = {
    "Population", "Housing", "Structures", "Water and Sanitation", "Economic Activity",
    "Education and Literacy", "Fertility and Mortality", "ICT", "Multidimensional Poverty",
    "Human Development Indicators", "Difficulties in Performing Activities",
}


async def gss_pxweb_get(topic: str, table: str | None = None) -> dict[str, Any]:
    from urllib.parse import quote
    if topic not in GSS_TOPICS:
        raise ValueError("Unsupported StatsBank topic")
    if table is not None and not __import__("re").fullmatch(r"[A-Za-z0-9_-]+\.px", table):
        raise ValueError("Table name must be a .px filename")
    url = f"{GSS_PXWEB_BASE}/{quote(topic, safe='')}"
    if table:
        url += f"/{quote(table, safe='')}"
    async with httpx.AsyncClient(timeout=40, follow_redirects=True) as client:
        response = await client.get(url, headers={"Accept": "application/json"})
        response.raise_for_status()
        return response.json()


def gss_variables(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    variables = metadata.get("variables")
    if not isinstance(variables, list):
        raise ValueError("The StatsBank metadata response did not contain a variable list")
    normalized = []
    for variable in variables:
        codes = variable.get("values", []) or []
        labels = variable.get("valueTexts", []) or codes
        if isinstance(codes, dict):
            codes = list(codes.keys())
        if isinstance(labels, dict):
            labels = list(labels.values())
        normalized.append({
            "code": str(variable.get("code", "")),
            "label": str(variable.get("text", variable.get("label", variable.get("code", "")))),
            "values": [str(v) for v in codes],
            "value_labels": [str(v) for v in labels],
            "mandatory": not bool(variable.get("elimination", False)),
        })
    return normalized


def flatten_jsonstat2(payload: dict[str, Any], max_rows: int = 5000) -> list[dict[str, Any]]:
    import itertools
    ids = payload.get("id") or []
    sizes = payload.get("size") or []
    dims = payload.get("dimension") or {}
    values = payload.get("value") or []
    if not ids or not sizes or not isinstance(values, list):
        return []
    categories = []
    labels_by_dimension = []
    for dimension_id in ids:
        category = (dims.get(dimension_id) or {}).get("category") or {}
        index = category.get("index") or {}
        labels = category.get("label") or {}
        if isinstance(index, dict):
            ordered_codes = [k for k, _ in sorted(index.items(), key=lambda item: item[1])]
        elif isinstance(index, list):
            ordered_codes = [str(x) for x in index]
        else:
            ordered_codes = []
        categories.append(ordered_codes)
        labels_by_dimension.append(labels)
    rows = []
    for position, combination in enumerate(itertools.product(*categories)):
        if position >= len(values) or len(rows) >= max_rows:
            break
        value = values[position]
        if value is None:
            continue
        row = {}
        for i, code in enumerate(combination):
            labels = labels_by_dimension[i]
            row[ids[i]] = labels.get(code, code) if isinstance(labels, dict) else code
        row["value"] = value
        rows.append(row)
    return rows
