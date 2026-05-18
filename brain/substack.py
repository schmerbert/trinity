import os
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / '.env')

_BASE    = "https://substack.com"
_PUB_URL = os.getenv("SUBSTACK_PUBLICATION_URL", "").rstrip("/")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "Trinity/1.0", "Content-Type": "application/json"})
    resp = s.post(
        f"{_BASE}/api/v1/email-login",
        json={
            "email":            os.getenv("SUBSTACK_EMAIL", ""),
            "password":         os.getenv("SUBSTACK_PASSWORD", ""),
            "captcha_response": None,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return s


def _to_tiptap(text: str) -> str:
    """Convert plain text to TipTap JSON (Substack's editor format)."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    nodes = [
        {"type": "paragraph", "content": [{"type": "text", "text": p}]}
        for p in paras
    ] or [{"type": "paragraph"}]
    return json.dumps({"type": "doc", "content": nodes})


def post_to_substack(title: str, body: str, subtitle: str = "", publish: bool = False) -> dict:
    if not _PUB_URL:
        return {"error": "SUBSTACK_PUBLICATION_URL not set in .env"}
    try:
        s = _session()
        payload = {
            "draft_title":    title,
            "draft_subtitle": subtitle,
            "draft_body":     _to_tiptap(body),
            "type":           "newsletter",
            "draft":          not publish,
            "audience":       "everyone",
        }
        resp = s.post(f"{_PUB_URL}/api/v1/posts", json=payload, timeout=20)
        resp.raise_for_status()
        data     = resp.json()
        post_url = data.get("canonical_url") or f"{_PUB_URL}/p/{data.get('slug', '')}"
        return {
            "success":  True,
            "id":       data.get("id"),
            "url":      post_url,
            "draft":    not publish,
            "title":    title,
        }
    except Exception as e:
        return {"error": str(e)}
