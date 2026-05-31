"""
TeamPulse — AI Service Layer
Fix: Groq 'proxies' error — use http_client=httpx.Client() explicitly.
Fix: Correct provider order — Groq first, then OpenAI, then Gemini.
Fix: Groq key validation is now less strict (strips whitespace only).
"""

import os
import json
import re
from loguru import logger
from typing import Optional


def _groq_client():
    try:
        import httpx
        from groq import Groq
        key = os.getenv("GROQ_API_KEY", "").strip()
        if not key:
            logger.debug("GROQ_API_KEY not set")
            return None
        client = Groq(api_key=key, http_client=httpx.Client())
        logger.debug("Groq client created OK")
        return client
    except ImportError:
        logger.warning("groq not installed — run: pip install groq httpx")
        return None
    except Exception as e:
        logger.warning(f"Groq client init error: {e}")
        return None


def _openai_client():
    try:
        import httpx
        from openai import OpenAI
        key = os.getenv("OPENAI_API_KEY", "").strip()
        if not key:
            return None
        return OpenAI(api_key=key, http_client=httpx.Client())
    except ImportError:
        return None
    except Exception as e:
        logger.warning(f"OpenAI init error: {e}")
        return None


def _gemini_model():
    try:
        import google.generativeai as genai
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key:
            return None
        genai.configure(api_key=key)
        return genai.GenerativeModel("gemini-1.5-flash")
    except ImportError:
        return None
    except Exception as e:
        logger.warning(f"Gemini init error: {e}")
        return None


# ── Fallback chain: Groq → OpenAI → Gemini ────────────────────

def ai_complete(prompt: str, system: str = "", max_tokens: int = 2000) -> str:
    errors = []

    # 1. Groq (primary — fastest, free)
    try:
        r = _try_groq(prompt, system, max_tokens)
        if r:
            logger.info("✅ AI via Groq")
            return r
        else:
            errors.append("Groq: client not available (key missing or not installed)")
    except Exception as e:
        errors.append(f"Groq: {e}")
        logger.warning(f"Groq failed: {e}")

    # 2. OpenAI (fallback 1)
    try:
        r = _try_openai(prompt, system, max_tokens)
        if r:
            logger.info("✅ AI via OpenAI")
            return r
        else:
            errors.append("OpenAI: client not available")
    except Exception as e:
        errors.append(f"OpenAI: {e}")
        logger.warning(f"OpenAI failed: {e}")

    # 3. Gemini (fallback 2)
    try:
        r = _try_gemini(prompt, system, max_tokens)
        if r:
            logger.info("✅ AI via Gemini")
            return r
        else:
            errors.append("Gemini: client not available")
    except Exception as e:
        errors.append(f"Gemini: {e}")
        logger.warning(f"Gemini failed: {e}")

    detail = " | ".join(errors)
    raise RuntimeError(
        f"All AI providers failed.\nDetails: {detail}\n"
        "Fix: Add GROQ_API_KEY=gsk_... to backend/.env\n"
        "Free key: https://console.groq.com\n"
        "Then restart the backend server."
    )


def _try_groq(prompt: str, system: str, max_tokens: int) -> Optional[str]:
    client = _groq_client()
    if not client:
        return None
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})

    # Try models in order — use env var first, then fallbacks
    preferred = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip()
    fallbacks  = ["llama-3.1-8b-instant", "llama-3.3-70b-versatile", "llama-3.1-70b-versatile", "gemma2-9b-it"]
    # Deduplicate: preferred first, then rest
    models = [preferred] + [m for m in fallbacks if m != preferred]
    for model in models:
        try:
            r = client.chat.completions.create(
                model=model, messages=msgs,
                max_tokens=max_tokens, temperature=0.3,
            )
            logger.debug(f"Groq model used: {model}")
            return r.choices[0].message.content
        except Exception as e:
            err = str(e).lower()
            if any(x in err for x in ["model", "not found", "decommission", "deprecated", "does not exist"]):
                logger.warning(f"Groq model {model} unavailable, trying next…")
                continue
            raise
    return None


def _try_openai(prompt: str, system: str, max_tokens: int) -> Optional[str]:
    client = _openai_client()
    if not client:
        return None
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    r = client.chat.completions.create(
        model="gpt-4o-mini", messages=msgs,
        max_tokens=max_tokens, temperature=0.3,
    )
    return r.choices[0].message.content


def _try_gemini(prompt: str, system: str, max_tokens: int) -> Optional[str]:
    model = _gemini_model()
    if not model:
        return None
    full = f"{system}\n\n{prompt}" if system else prompt
    return model.generate_content(full).text


def _parse_json(raw: str) -> dict:
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().strip("`").strip()
    start = cleaned.find("{")
    end   = cleaned.rfind("}")
    if start != -1 and end != -1:
        cleaned = cleaned[start:end+1]
    return json.loads(cleaned)


# ── Task extraction ───────────────────────────────────────────

_TASK_SYSTEM = """You are an expert project manager AI that extracts detailed actionable tasks from meeting notes, transcripts, and discussions.

CRITICAL RULES:
1. Extract EVERY task, action item, and assignment — miss nothing
2. ALWAYS include the assignee name exactly as mentioned (e.g. "John", "Sarah", "the dev team")
3. Include the FULL task description with all context, not just a short title
4. Extract exact deadline text (e.g. "by Friday EOD", "next Monday", "end of sprint")
5. Convert relative deadlines to ISO dates based on today if possible
6. Set priority based on urgency words: "urgent/ASAP/blocker" = critical, "important/soon" = high, "when you can" = low
7. If no assignee is mentioned, set assignee to null but still extract the task

Return ONLY valid JSON — no markdown, no explanation:

{
  "tasks": [
    {
      "title": "Clear, specific action item title",
      "description": "Full context: what needs to be done, why, any specific requirements mentioned",
      "assignee": "Exact person name as mentioned, or null",
      "deadline": "YYYY-MM-DD or null",
      "deadline_text": "Exact original deadline text like 'by Friday EOD'",
      "priority": "critical|high|medium|low",
      "confidence": 0.95,
      "follow_up": ["Specific follow-up action 1", "Specific follow-up action 2"]
    }
  ],
  "summary": "Comprehensive 3-5 sentence summary covering: what was discussed, key decisions made, who is responsible for what, and any blockers",
  "key_points": ["Specific key point with person names and details", "Another key point"],
  "decisions": ["Specific decision made with context"],
  "next_steps": ["Specific next step with owner if mentioned"]
}"""


def extract_tasks_from_text(text: str, team_members: list = None) -> dict:
    member_ctx = ""
    if team_members:
        names = ", ".join([m.get("displayName", m.get("email", "")) for m in team_members if m])
        if names:
            member_ctx = f"\nKnown team members: {names}"
    prompt = f"""Extract all tasks from this text:{member_ctx}\n\nTEXT:\n{text[:6000]}\n\nReturn JSON only."""
    try:
        raw = ai_complete(prompt, _TASK_SYSTEM, max_tokens=3000)
        result = _parse_json(raw)
        logger.info(f"Extracted {len(result.get('tasks', []))} tasks")
        return result
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        return {"tasks": [], "summary": "Could not parse AI response.", "key_points": [], "decisions": [], "next_steps": []}
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(str(e))


def summarize_content(content: str, content_type: str = "document") -> dict:
    prompt = f"""Summarize this {content_type}. Return JSON only:
{{"summary":"2-3 sentence summary","key_points":["point"],"decisions":["decision"],"open_questions":["question"],"next_steps":["step"]}}

CONTENT:\n{content[:5000]}"""
    try:
        raw = ai_complete(prompt, "You are an expert summarizer. Return ONLY valid JSON.", max_tokens=1500)
        return _parse_json(raw)
    except Exception as e:
        logger.error(f"Summarization error: {e}")
        return {"summary": content[:300], "key_points": [], "decisions": [], "open_questions": [], "next_steps": []}


def generate_catchup_summary(messages: list, user_name: str) -> str:
    if not messages:
        return "No messages to catch up on!"
    lines = "\n".join([
        f"{m.get('user', {}).get('displayName', 'Someone')}: {m.get('content', '')}"
        for m in messages[-50:]
    ])
    prompt = f"Write a friendly catch-up summary for {user_name} who missed these messages:\n\n{lines}\n\nWrite 2-4 paragraphs."
    try:
        return ai_complete(prompt, "You are a helpful team assistant.", max_tokens=600)
    except Exception as e:
        return f"Could not generate summary: {str(e)}"


def generate_daily_digest(team_name: str, stats: dict) -> str:
    prompt = f"""Write a motivating daily digest for team "{team_name}":
Tasks completed: {stats.get('tasks_completed',0)}, New: {stats.get('tasks_created',0)}, Overdue: {stats.get('overdue_tasks',0)}, Messages: {stats.get('messages',0)}
Write 2-3 paragraphs."""
    try:
        return ai_complete(prompt, "You are a team productivity assistant.", max_tokens=500)
    except Exception as e:
        return f"Team completed {stats.get('tasks_completed',0)} tasks today. Keep it up!"


def answer_question_with_context(question: str, context_docs: list) -> dict:
    if not context_docs:
        return {"answer": "No relevant documents found.", "sources": [], "confidence": 0.0}
    context = "\n\n---\n\n".join([
        f"Document: {d.get('title','Untitled')}\n{d.get('content','')[:1500]}"
        for d in context_docs
    ])
    prompt = f"""Question: {question}\n\nContext:\n{context}\n\nReturn JSON only:
{{"answer":"answer","sources":["Title"],"confidence":0.9}}"""
    try:
        raw = ai_complete(prompt, "Answer only from provided documents. Return JSON only.", max_tokens=1000)
        return _parse_json(raw)
    except Exception as e:
        return {"answer": f"Could not generate answer: {str(e)}", "sources": [d.get("title") for d in context_docs], "confidence": 0.3}


def recommend_task_priority(tasks: list) -> dict:
    if not tasks:
        return {"recommendations": [], "insight": "No active tasks."}
    tasks_json = json.dumps([{"id":t.get("id"),"title":t.get("title"),"deadline":t.get("deadline"),"currentPriority":t.get("priority"),"status":t.get("status")} for t in tasks[:20]], indent=2)
    prompt = f"""Review tasks, suggest priority changes. Return JSON only:
{{"recommendations":[{{"task_id":"id","recommended_priority":"high","reason":"reason"}}],"insight":"observation"}}

Tasks:\n{tasks_json}"""
    try:
        raw = ai_complete(prompt, "You are a productivity expert. Return only JSON.", max_tokens=800)
        return _parse_json(raw)
    except Exception:
        return {"recommendations": [], "insight": "Could not generate recommendations."}


def check_ai_providers() -> dict:
    return {
        "groq":   _groq_client() is not None,
        "openai": _openai_client() is not None,
        "gemini": _gemini_model() is not None,
    }
