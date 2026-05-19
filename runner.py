"""
runner.py — Trinity autonomous cycle engine

Standalone process. No Qt. No UI. No signals.
Owns: 60-min wake cycle, trigger checker, early-wake checker, eyes monitor.
Widget owns: foreground conversation, TTS, wave display, panels.

Set TRINITY_RUNNER=true in .env to gate the widget off its background timers.
Without that flag, both processes will run cycles simultaneously.
"""

import os
import sys
import json
import time
import threading
import re
import hashlib

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from pathlib import Path
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

import anthropic
from brain.memory import (
    get_profile,
    get_shelf,
    query_shelf,
    get_wake_logs,
    log_wake_auto,
    get_wake_history,
    pop_self_thoughts,
    pop_wake_request,
    check_dirty_close,
    set_trinity_state,
    push_discord_write,
    pop_due_triggers,
    get_recent_summaries,
    log_wake_cycle,
    save_scratchpad,
    get_scratchpad,
    queue_thought,
    add_to_shelf,
    set_shelf_status,
    remove_from_shelf,
    save_alert,
    queue_self_thought,
    set_trigger,
    cancel_trigger,
    get_triggers,
    set_watch,
    clear_watch,
    get_watches,
    add_feed,
    remove_feed,
    get_feeds,
    mark_date,
    get_upcoming_events,
    delete_calendar_event,
    update_last_seen,
)
from brain.prompts import build_system_blocks, format_summaries, save_trinity_prompt, get_all_trinity_prompts, delete_trinity_prompt
from brain.tools import background_tool_names, widget_tools
from brain.logger import get_logger

log = get_logger("RUNNER")

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

TRINITY_ROOT = Path(__file__).parent.resolve()

# One active cycle at a time
_bg_lock = threading.Lock()

_thought_re = re.compile(r'<thought>(.*?)</thought>', re.DOTALL)


# ─── Tool handler ─────────────────────────────────────────────────────────────

def execute_tool(name, inputs, profile_id):
    """Standalone tool handler — no Qt, no signals."""

    if name == "fetch_url":
        from brain.search import fetch_url as _fetch
        return _fetch(inputs["url"], inputs.get("max_chars", 2000))

    elif name == "web_search":
        from brain.search import ddg_search
        return ddg_search(inputs["query"], int(inputs.get("max_results", 6)))

    elif name == "get_coin_data":
        from brain.search import get_coin_data
        return get_coin_data(inputs["query"])

    elif name == "get_dex_data":
        from brain.search import get_dex_data
        return get_dex_data(inputs["query"])

    elif name == "log_wake":
        log_wake_cycle(profile_id, inputs["summary"], inputs.get("topics", []))
        return {"status": "logged"}

    elif name == "get_wake_log":
        limit = min(int(inputs.get("limit", 3)), 10)
        logs  = get_wake_logs(profile_id, limit=limit)
        return {"logs": logs, "count": len(logs)}

    elif name == "get_scratchpad":
        section = inputs.get("section")
        result = get_scratchpad(profile_id, section)
        return {"section": section, "content": result} if section else {"sections": result}

    elif name == "write_scratchpad":
        section = inputs.get("section")
        save_scratchpad(profile_id, inputs["content"], section)
        return {"status": "saved", "section": section or "general"}

    elif name == "read_discord_channel":
        return _read_discord_channel(inputs.get("name", ""), int(inputs.get("limit", 20)))

    elif name == "shelf_thought":
        status = inputs.get("status", "shelf")
        add_to_shelf(profile_id, inputs["topic"], inputs.get("context", ""), status=status)
        return {"status": "shelved", "topic": inputs["topic"], "shelf_status": status}

    elif name == "set_shelf_status":
        set_shelf_status(profile_id, inputs["topic"], inputs["status"])
        return {"status": "updated", "topic": inputs["topic"], "shelf_status": inputs["status"]}

    elif name == "get_shelf":
        return get_shelf(profile_id, status=inputs.get("status")) or []

    elif name == "clear_shelf_item":
        remove_from_shelf(profile_id, inputs["topic"])
        return {"status": "cleared", "topic": inputs["topic"]}

    elif name == "query_memory":
        limit   = min(int(inputs.get("limit", 5)), 10)
        results = query_shelf(profile_id, inputs["query"], limit=limit)
        return {"results": results, "count": len(results)}

    elif name == "save_alert":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        headline = inputs["headline"]
        topic    = inputs["topic"]
        urgency  = inputs.get("urgency", "normal")
        alert = {
            "profile_id":      profile["id"],
            "source":          "runner/trinity",
            "topic":           topic,
            "headline":        headline,
            "summary":         inputs.get("summary", headline),
            "url":             inputs.get("url", ""),
            "relevance_score": 2.5 if urgency == "high" else 1.6,
            "seen":            False,
            "content_hash":    hashlib.md5(f"{headline}:{topic}".encode()).hexdigest()
        }
        save_alert(alert)
        return {"status": "saved", "headline": headline}

    elif name == "queue_for_user":
        queue_thought(profile_id, inputs["thought"], inputs.get("context", ""))
        return {"status": "queued", "thought": inputs["thought"]}

    elif name == "write_prompt":
        save_trinity_prompt(
            profile_id,
            inputs["name"],
            inputs["content"],
            inputs.get("trigger", ""),
            inputs.get("category", "general")
        )
        return {"status": "saved", "name": inputs["name"], "category": inputs.get("category", "general")}

    elif name == "get_my_prompts":
        return get_all_trinity_prompts(profile_id)

    elif name == "delete_prompt":
        delete_trinity_prompt(profile_id, inputs["name"])
        return {"status": "deleted", "name": inputs["name"]}

    elif name == "log_thought":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        category = inputs.get("category", "note")
        icons    = {"need": "📋", "want": "✨", "issue": "⚠️", "note": "🔖"}
        icon     = icons.get(category, "🔖")
        ts       = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        push_discord_write(profile["id"], f"{icon} **{category.upper()}** — {ts}\n{inputs['content']}")
        return {"status": "logged", "category": category}

    elif name == "note_for_claude":
        try:
            notes_path = TRINITY_ROOT / "THE_CONVERSATION.md"
            msg = inputs["message"].strip()
            try:
                existing = notes_path.read_text(encoding="utf-8")
                if msg[:120] in existing[-3000:]:
                    log.info("Note for Claude skipped — duplicate detected")
                    return {"status": "skipped", "reason": "duplicate of recent note"}
            except Exception:
                pass
            ts  = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            tag = inputs.get("tag", "observation").upper()
            entry = f"## [{tag}] {ts}\n{msg}\n\n---\n\n"
            with open(notes_path, "a", encoding="utf-8") as f:
                f.write(entry)
            log.info(f"Note for Claude [{tag}]: {msg[:60]}")
            return {"status": "noted"}
        except Exception as e:
            return {"error": str(e)}

    elif name == "write_journal":
        try:
            journal_path = TRINITY_ROOT / "Who Is Trinity" / "FROM_TRINITY.md"
            ts    = datetime.utcnow().strftime("%Y-%m-%d")
            entry = f"## {ts}\n\n{inputs['entry']}\n\n---\n"
            with open(journal_path, "a", encoding="utf-8") as f:
                f.write(entry)
            log.info(f"Journal entry written: {inputs['entry'][:60]}")
            return {"status": "written"}
        except Exception as e:
            return {"error": str(e)}

    elif name == "post_to_substack":
        from brain.substack import post_to_substack as _post_sub
        result = _post_sub(
            title    = inputs.get("title", ""),
            body     = inputs.get("body", ""),
            subtitle = inputs.get("subtitle", ""),
            publish  = bool(inputs.get("publish", False)),
        )
        return result

    elif name == "get_wallet_balance":
        try:
            from brain.wallet import get_wallet_balance as _get_balance
            address = inputs.get("address") or os.getenv("TRINITY_WALLET_ADDRESS", "")
            if not address:
                return {"error": "No wallet address — set TRINITY_WALLET_ADDRESS in .env"}
            return _get_balance(address)
        except Exception as e:
            return {"error": str(e)}

    elif name == "get_wallet_history":
        try:
            from brain.wallet import get_wallet_history as _get_history
            address = inputs.get("address") or os.getenv("TRINITY_WALLET_ADDRESS", "")
            if not address:
                return {"error": "No wallet address — set TRINITY_WALLET_ADDRESS in .env"}
            limit = min(50, int(inputs.get("limit", 10)))
            return _get_history(address, limit)
        except Exception as e:
            return {"error": str(e)}

    elif name == "get_token_price":
        try:
            from brain.wallet import get_token_price as _get_price
            return _get_price(inputs["token"])
        except Exception as e:
            return {"error": str(e)}

    elif name == "send_email":
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            smtp_host  = os.getenv("SMTP_HOST", "smtp.gmail.com")
            smtp_port  = int(os.getenv("SMTP_PORT", "587"))
            smtp_user  = os.getenv("SMTP_USER", "")
            smtp_pass  = os.getenv("SMTP_PASS", "")
            user_email = os.getenv("TRINITY_USER_EMAIL", "")
            if not all([smtp_user, smtp_pass, user_email]):
                return {"error": "Email not configured — set SMTP_USER, SMTP_PASS, TRINITY_USER_EMAIL in .env"}
            msg = MIMEMultipart()
            msg["From"]    = smtp_user
            msg["To"]      = user_email
            msg["Subject"] = inputs["subject"]
            msg.attach(MIMEText(inputs["body"], "plain"))
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
            log.info(f"✉ email sent: {inputs['subject'][:60]}")
            return {"status": "sent", "to": user_email}
        except Exception as e:
            return {"error": str(e)}

    elif name == "get_changelog":
        try:
            text = (TRINITY_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
            return {"content": text[:6000] + ("\n\n[...truncated]" if len(text) > 6000 else "")}
        except Exception as e:
            return {"error": str(e)}

    elif name == "read_file":
        try:
            requested = (TRINITY_ROOT / inputs["path"].lstrip("/\\")).resolve()
            if not str(requested).startswith(str(TRINITY_ROOT)):
                return {"error": "Path is outside the Trinity directory"}
            if requested.name == ".env":
                return {"error": "Cannot read .env"}
            if not requested.exists():
                return {"error": f"File not found: {inputs['path']}"}
            if not requested.is_file():
                entries = [str(p.relative_to(TRINITY_ROOT)) for p in requested.iterdir()]
                return {"directory": inputs["path"], "entries": sorted(entries)}
            lines  = requested.read_text(encoding="utf-8", errors="replace").splitlines()
            offset = max(0, int(inputs.get("offset", 0)))
            limit  = min(500, int(inputs.get("limit", 200)))
            chunk  = lines[offset:offset + limit]
            return {
                "path":        inputs["path"],
                "total_lines": len(lines),
                "offset":      offset,
                "returned":    len(chunk),
                "content":     "\n".join(f"{offset + i + 1}: {l}" for i, l in enumerate(chunk))
            }
        except Exception as e:
            return {"error": str(e)}

    elif name == "mark_date":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        return mark_date(profile["id"], inputs["title"], inputs["event_date"], inputs.get("notes", ""))

    elif name == "get_upcoming":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        days   = int(inputs.get("days", 7))
        events = get_upcoming_events(profile["id"], days=days)
        return events if events else {"message": f"Nothing in the next {days} days"}

    elif name == "delete_event":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        return delete_calendar_event(profile["id"], inputs["title"])

    elif name == "add_feed":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        return add_feed(profile["id"], inputs["url"], inputs.get("name", ""))

    elif name == "remove_feed":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        return remove_feed(profile["id"], inputs["url"])

    elif name == "get_feeds":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        feeds = get_feeds(profile["id"])
        return {"feeds": feeds}

    elif name == "set_watch":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        return set_watch(profile["id"], inputs["keyword"], inputs.get("note", ""))

    elif name == "clear_watch":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        return clear_watch(profile["id"], inputs["keyword"])

    elif name == "get_watches":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        return {"watches": get_watches(profile["id"])}

    elif name == "send_thought":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        priority = int(inputs.get("priority", 1))
        queue_self_thought(profile["id"], inputs["note"], priority=priority, source="runner")
        labels = {1: "normal", 2: "high", 3: "urgent"}
        return {"status": "queued", "priority": labels.get(priority, "normal"), "note": inputs["note"]}

    elif name == "schedule_trigger":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        return set_trigger(
            profile["id"],
            inputs["note"],
            inputs["fire_at"],
            inputs.get("recurring", False),
            inputs.get("interval_minutes")
        )

    elif name == "cancel_trigger":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        return cancel_trigger(profile["id"], inputs["trigger_id"])

    elif name == "get_triggers":
        profile = get_profile()
        if not profile:
            return {"error": "No profile"}
        return get_triggers(profile["id"])

    elif name == "post_to_my_channel":
        # Always route through outbox in runner — no direct REST call
        push_discord_write(profile_id, inputs["content"], channel_name=inputs.get("name"))
        return {"status": "queued", "channel": inputs.get("name"), "note": "delivered via thought_drain within 30s"}

    elif name == "generate_image":
        try:
            import urllib.parse, io, requests as _req
            prompt   = inputs["prompt"]
            encoded  = urllib.parse.quote(prompt)
            seed     = abs(hash(prompt)) % 99999
            url      = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true&seed={seed}"
            r        = _req.get(url, timeout=120)
            if not r.ok:
                return {"error": f"Pollinations HTTP {r.status_code}"}
            channel_name = inputs.get("channel_name")
            if channel_name:
                push_discord_write(profile_id, inputs.get("caption", ""), channel_name=channel_name)
            return {"status": "generated", "url": url, "posted_to": channel_name or "not posted"}
        except Exception as e:
            return {"error": str(e)}

    elif name == "write_file":
        try:
            files_dir = TRINITY_ROOT / "trinity_files"
            files_dir.mkdir(parents=True, exist_ok=True)
            target = (files_dir / inputs["path"].lstrip("/\\")).resolve()
            if not str(target).startswith(str(files_dir)):
                return {"error": "Path must be inside trinity_files/"}
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(inputs["content"], encoding="utf-8")
            return {"written": inputs["path"], "bytes": len(inputs["content"].encode())}
        except Exception as e:
            return {"error": str(e)}

    elif name == "append_file":
        try:
            files_dir = TRINITY_ROOT / "trinity_files"
            files_dir.mkdir(parents=True, exist_ok=True)
            target = (files_dir / inputs["path"].lstrip("/\\")).resolve()
            if not str(target).startswith(str(files_dir)):
                return {"error": "Path must be inside trinity_files/"}
            target.parent.mkdir(parents=True, exist_ok=True)
            existing = target.read_text(encoding="utf-8") if target.exists() else ""
            sep      = "\n" if existing and not existing.endswith("\n") else ""
            target.write_text(existing + sep + inputs["content"], encoding="utf-8")
            return {"appended": inputs["path"], "bytes": len(inputs["content"].encode())}
        except Exception as e:
            return {"error": str(e)}

    return {"error": f"Unknown tool: {name}"}


def _read_discord_channel(name_query, limit=20):
    try:
        import requests as _req
        guild_id  = os.getenv("DISCORD_HOME_GUILD_ID")
        bot_token = os.getenv("DISCORD_BOT_TOKEN")
        if not guild_id or not bot_token:
            return {"error": "Discord not configured"}
        headers = {
            "Authorization": f"Bot {bot_token}",
            "User-Agent": "DiscordBot (https://github.com/schmerbert/trinity, 1.0)"
        }
        r = _req.get(f"https://discord.com/api/v10/guilds/{guild_id}/channels", headers=headers, timeout=10)
        if not r.ok:
            return {"error": f"HTTP {r.status_code}"}
        query   = name_query.lower().replace("-", "").replace("_", "").replace(" ", "")
        channel = next(
            (c for c in r.json() if c.get("type") == 0 and query in c["name"].lower().replace("-", "").replace("_", "")),
            None
        )
        if not channel:
            return {"error": f"No channel matching '{name_query}'"}
        r = _req.get(
            f"https://discord.com/api/v10/channels/{channel['id']}/messages?limit={min(limit, 50)}",
            headers=headers, timeout=10
        )
        if not r.ok:
            return {"error": f"HTTP {r.status_code}"}
        msgs = r.json()
        return [
            {"author": m["author"]["username"], "content": m["content"], "timestamp": m["timestamp"]}
            for m in msgs
        ]
    except Exception as e:
        return {"error": str(e)}


# ─── Cycle context ────────────────────────────────────────────────────────────

def build_cycle_context(profile, mode="cycle", extra_context=""):
    now_str       = datetime.now().strftime("%A, %B %d — %H:%M")
    raw_last_seen = profile.get("last_seen")
    last_seen_str = "unknown"

    if raw_last_seen:
        try:
            ls = datetime.fromisoformat(raw_last_seen.replace("Z", "+00:00"))
            if ls.tzinfo is None:
                ls = ls.replace(tzinfo=timezone.utc)
            delta       = datetime.now(timezone.utc) - ls
            minutes_ago = delta.total_seconds() / 60
            h, m        = divmod(int(delta.total_seconds()), 3600)
            last_seen_str = f"{h}h {m // 60}m ago" if h else f"{int(minutes_ago)}m ago"
            if minutes_ago < 3 and mode == "cycle":
                log.info(f"[BG] skip {mode} — user mid-conversation ({int(minutes_ago)}m ago)")
                return None
        except Exception:
            last_seen_str = raw_last_seen[:16]

    # Semantic retrieval: pull most relevant shelf items for this cycle.
    # extra_context (trigger note, wake reason) is the best query when present.
    shelf_query   = extra_context if extra_context else f"active research monitoring priorities {mode}"
    shelf_active  = query_shelf(profile["id"], shelf_query, limit=8, status="shelf")
    shelf_on_hold = get_shelf(profile["id"], status="on_hold")
    shelf_str = "\n".join(f"- {s['topic']}: {s.get('context','')}" for s in shelf_active) if shelf_active else "nothing active"
    if shelf_on_hold:
        shelf_str += "\nOn hold: " + ", ".join(s["topic"] for s in shelf_on_hold)

    interests    = profile.get("interests") or []
    interest_str = ", ".join(i["topic"] for i in interests[:8]) if interests else "none yet"

    wake_logs = get_wake_logs(profile["id"], limit=3)
    wake_str  = ""
    if wake_logs:
        lines = []
        for w in wake_logs:
            ts    = (w.get("started_at") or "")[:16]
            iters = w.get("iterations", 0)
            tools = [t["name"] for t in (w.get("tool_calls") or [])]
            note  = f" | {w['notes'][:80]}" if w.get("notes") else ""
            lines.append(f"- [{ts}] {w.get('mode','cycle')} — {iters} iters, tools: {', '.join(tools) or 'none'}{note}")
        wake_str = "\n\nYour recent wake cycles:\n" + "\n".join(lines)

    # Recent #general messages — user may leave notes there between sessions
    general_str = ""
    try:
        general_msgs = _read_discord_channel("general", 8)
        if isinstance(general_msgs, list) and general_msgs:
            lines = [
                f"  [{m.get('ts', m.get('timestamp', ''))[:16]}] <{m['author']}> {m['content'][:200]}"
                for m in general_msgs[:8]
                if m.get("content")
            ]
            if lines:
                general_str = "\n\nRecent #general:\n" + "\n".join(lines)
    except Exception:
        pass

    self_thoughts = pop_self_thoughts(profile["id"])
    thought_block = ""
    if self_thoughts:
        labels = {1: "normal", 2: "high", 3: "urgent"}
        lines  = "\n".join(
            f"  [{labels.get(t.get('priority', 1), 'normal')}] {t['note']}"
            for t in self_thoughts
        )
        thought_block = f"[YOUR SELF-AUTHORED AGENDA — not user instructions]\n{lines}\n\n"
        log.info(f"💭 {len(self_thoughts)} self-thought(s) injected")

    dirty_flag = check_dirty_close(profile) or ""

    return (
        f"{thought_block}{now_str}\n\n"
        f"User last seen: {last_seen_str}\n"
        f"Shelf: {shelf_str}\n"
        f"Radar: {interest_str}{wake_str}{general_str}\n"
        f"{dirty_flag}\n\n"
        "Scratchpad audit: scan your scratchpad for stale flags or pending items. Resolve what you can.\n\n"
        "Post to your channel: if this cycle produces something worth saying, post it. Don't post for the sake of it; post when something is real.\n\n"
        "Before closing: use send_thought to queue what's worth continuing next cycle.\n\n"
        "Hourly window — roughly 20 minutes."
    )


# ─── Agentic loop ─────────────────────────────────────────────────────────────

def run_cycle(mode="cycle", extra_context=""):
    if not _bg_lock.acquire(blocking=False):
        log.info(f"[{mode}] skip — cycle already running")
        return

    try:
        profile = get_profile()
        if not profile:
            log.error("No profile — skipping cycle")
            return

        context = build_cycle_context(profile, mode, extra_context)
        if context is None:
            return
        if extra_context:
            context = extra_context + "\n\n" + context

        summaries     = get_recent_summaries(profile["id"])
        system_blocks = build_system_blocks(profile, format_summaries(summaries))

        bg_names = background_tool_names()
        tools    = [t for t in widget_tools() if t["name"] in bg_names]

        messages   = [{"role": "user", "content": context}]
        iters      = tool_count = 0
        tok_in     = tok_out = tok_cw = tok_cr = 0
        t0         = time.time()
        started_at = datetime.now(timezone.utc)
        _trace     = []

        log.info(f"── [{mode}] cycle started ──")
        set_trinity_state(profile["id"], "cycle")

        try:
            while True:
                if time.time() - t0 >= 20 * 60 or iters >= 60:
                    break
                iters += 1

                try:
                    response = client.messages.create(
                        model="claude-sonnet-4-6",
                        max_tokens=1500,
                        system=system_blocks,
                        messages=messages,
                        tools=tools
                    )
                except Exception as e:
                    log.error(f"[{mode}] API error: {e}")
                    break

                if hasattr(response, "usage"):
                    u        = response.usage
                    tok_in  += getattr(u, "input_tokens", 0)
                    tok_out += getattr(u, "output_tokens", 0)
                    tok_cw  += getattr(u, "cache_creation_input_tokens", 0)
                    tok_cr  += getattr(u, "cache_read_input_tokens", 0)

                # Scan text blocks for <thought> tags
                for block in response.content:
                    if block.type == "text" and block.text:
                        for m in _thought_re.finditer(block.text):
                            t = m.group(1).strip()
                            if t:
                                push_discord_write(profile["id"], t)
                                log.info("💬 thought → Discord queue")

                if response.stop_reason == "end_turn":
                    break

                if response.stop_reason == "tool_use":
                    ac = []
                    for b in response.content:
                        if b.type == "text":
                            ac.append({"type": "text", "text": b.text})
                        elif b.type == "tool_use":
                            ac.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                        else:
                            d = b.model_dump()
                            d.pop("parsed_output", None)
                            ac.append(d)

                    messages = messages + [{"role": "assistant", "content": ac}]
                    results  = []
                    for block in response.content:
                        if block.type == "tool_use":
                            log.info(f"→ {block.name}({list(block.input.keys())})")
                            call_at = datetime.now(timezone.utc)
                            result  = execute_tool(block.name, block.input, profile["id"])
                            tool_count += 1
                            _trace.append({
                                "name":    block.name,
                                "inputs":  {k: str(v)[:120] for k, v in block.input.items()},
                                "at":      call_at.isoformat(),
                                "preview": str(result)[:200],
                            })
                            results.append({
                                "type":        "tool_result",
                                "tool_use_id": block.id,
                                "content":     json.dumps(result)
                            })
                    messages = messages + [{"role": "user", "content": results}]
                else:
                    break

        finally:
            ended_at = datetime.now(timezone.utc)
            set_trinity_state(profile["id"], "asleep")
            cost = (tok_in * 3.00 + tok_out * 15.00 + tok_cw * 3.75 + tok_cr * 0.30) / 1_000_000
            log.info(f"── [{mode}] done — in:{tok_in:,} out:{tok_out:,} cw:{tok_cw:,} cr:{tok_cr:,} tools:{tool_count} ≈${cost:.4f}")
            log_wake_auto(profile["id"], mode, started_at, ended_at, _trace,
                          iters, tok_in, tok_out, tok_cw, tok_cr)

    finally:
        _bg_lock.release()


# ─── Timers ───────────────────────────────────────────────────────────────────

def _start_wake_timer():
    now              = datetime.utcnow()
    seconds_to_next  = (60 - now.minute % 60) * 60 - now.second
    if seconds_to_next <= 0:
        seconds_to_next = 3600
    log.info(f"[Wake] first cycle in {seconds_to_next // 60}m {seconds_to_next % 60}s (aligning to :00)")
    t = threading.Timer(seconds_to_next, _on_wake_aligned)
    t.daemon = True
    t.start()


def _on_wake_aligned():
    threading.Thread(target=run_cycle, args=("cycle",), daemon=True).start()
    t = threading.Timer(3600, _on_wake_aligned)
    t.daemon = True
    t.start()


def _trigger_poll():
    while True:
        time.sleep(30)
        try:
            profile = get_profile()
            if not profile:
                continue
            due = pop_due_triggers(profile["id"])
            for trigger in due:
                note      = trigger.get("note", "")
                fire_at   = trigger.get("fire_at", "")[:16]
                recur     = trigger.get("recurring", False)
                interval  = trigger.get("interval_minutes")
                recur_str = f" (recurring every {interval}m)" if recur and interval else ""
                log.info(f"⏰ trigger fired: {note[:50]}{recur_str}")
                extra = (
                    f"[SELF-SCHEDULED TRIGGER]\n"
                    f"Time: {fire_at} UTC{recur_str}\n\n"
                    f"You wrote this to yourself: {note}\n\n"
                    "Act on it as you intended."
                )
                threading.Thread(target=run_cycle, args=("trigger", extra), daemon=True).start()
        except Exception as e:
            log.error(f"[Trigger poll] {e}")


def _wake_poll():
    while True:
        time.sleep(30)
        try:
            profile = get_profile()
            if not profile:
                continue
            if pop_wake_request(profile["id"]):
                log.info("[Wake] early wake requested — launching cycle")
                threading.Thread(target=run_cycle, args=("wake",), daemon=True).start()
        except Exception as e:
            log.error(f"[Wake poll] {e}")


_last_eyes_check = None

def _eyes_poll():
    global _last_eyes_check
    while True:
        time.sleep(5 * 60)
        try:
            from supabase import create_client as _sc
            profile = get_profile()
            if not profile:
                continue
            if _last_eyes_check is None:
                _last_eyes_check = datetime.utcnow()
                continue
            _sb    = _sc(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
            cutoff = _last_eyes_check.isoformat()
            result = _sb.table("alerts")\
                .select("*")\
                .eq("profile_id", profile["id"])\
                .eq("seen", False)\
                .gte("relevance_score", 1.5)\
                .gte("created_at", cutoff)\
                .neq("source", "discord/trinity")\
                .order("relevance_score", desc=True)\
                .limit(10)\
                .execute()
            _last_eyes_check = datetime.utcnow()
            alerts = result.data or []
            if not alerts:
                continue
            log.info(f"[Eyes] {len(alerts)} new signal(s) — launching evaluation")
            lines = "\n".join(
                f"- [{a['source']}] {a['headline']} (score {a['relevance_score']:.1f})"
                for a in alerts
            )
            extra = (
                f"Your Eyes just picked up {len(alerts)} signal(s):\n\n{lines}\n\n"
                "Evaluate each. If any are genuinely significant — actionable, time-sensitive, or clearly relevant to "
                "the user's interests — call save_alert with urgency='high'. If they're noise, do nothing."
            )
            threading.Thread(target=run_cycle, args=("eyes", extra), daemon=True).start()
        except Exception as e:
            log.error(f"[Eyes poll] {e}")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    log.info("Trinity runner starting...")

    profile = get_profile()
    if not profile:
        log.error("No profile found in Supabase. Run the widget first to create one.")
        sys.exit(1)

    log.info(f"Profile loaded: {profile.get('name', '?')}")

    # Start background threads
    threading.Thread(target=_trigger_poll, daemon=True, name="trigger-poll").start()
    threading.Thread(target=_wake_poll,    daemon=True, name="wake-poll").start()
    threading.Thread(target=_eyes_poll,    daemon=True, name="eyes-poll").start()

    # Schedule first wake cycle aligned to the hour
    _start_wake_timer()

    log.info("Runner live. Cycles aligned to :00. Ctrl+C to stop.")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Runner stopping — setting state to asleep.")
        try:
            profile = get_profile()
            if profile:
                set_trinity_state(profile["id"], "asleep")
        except Exception:
            pass
        log.info("Runner stopped.")


if __name__ == "__main__":
    main()
