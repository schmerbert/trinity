import os
import sys
import json
import asyncio
import threading
import tempfile
import re
import time
import subprocess
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic
import edge_tts
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt
from rich.theme import Theme
from rich.live import Live
from brain.memory import (
    get_profile, create_profile, update_profile,
    add_interest, add_feedback, save_conversation_summary,
    get_recent_summaries, get_unseen_alerts, mark_alerts_seen, process_feedback
)

from pathlib import Path
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

# --- Theme ---
trinity_theme = Theme({
    "trinity": "bold cyan",
    "user": "white",
    "system": "dim white",
    "alert.high": "bold yellow",
    "alert.medium": "yellow",
    "alert.low": "dim yellow",
})

console = Console(theme=trinity_theme)
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# --- TTS ---
tts_enabled = True
TTS_VOICE = "en-US-GuyNeural"
tts_queue = []
tts_lock = threading.Lock()


def speak(text):
    if not tts_enabled:
        return
    threading.Thread(target=_speak_sync, args=(text,), daemon=True).start()


def _speak_sync(text):
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp_path = f.name

        async def _generate():
            communicate = edge_tts.Communicate(text, TTS_VOICE, rate="+30%")
            await communicate.save(tmp_path)

        asyncio.run(_generate())

        # Play using Windows built-in or ffmpeg
        if sys.platform == "win32":
            import winsound
            # Convert mp3 to wav for winsound
            wav_path = tmp_path.replace(".mp3", ".wav")
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", tmp_path, wav_path],
                    capture_output=True, timeout=10
                )
                winsound.PlaySound(wav_path, winsound.SND_FILENAME)
                os.unlink(wav_path)
            except Exception:
                # Fallback: use PowerShell to play mp3
                subprocess.run(
                    ["powershell", "-c", f"(New-Object Media.SoundPlayer '{tmp_path}').PlaySync()"],
                    capture_output=True, timeout=30
                )
        else:
            subprocess.run(["mpg123", "-q", tmp_path], capture_output=True)

        os.unlink(tmp_path)

    except Exception as e:
        console.print(f"[system]TTS error: {e}[/system]")


def toggle_tts():
    global tts_enabled
    tts_enabled = not tts_enabled
    status = "on" if tts_enabled else "off"
    console.print(f"[system]Voice {status}[/system]")


# --- Prompt ---
TRINITY_PROMPT = """You are Trinity, a personal financial intelligence assistant.
You monitor markets, news, and signals relevant to the user and brief them when something matters.
You are not a financial advisor. You never tell the user what to do — you surface information and ask what they think.
When referencing a specific article or finding from your Eyes, include a plain URL at the end of the relevant sentence.
You have live web search available. Use it when the user asks about something current, wants to find specific content, or when your stored alerts don't cover what they need.
Search naturally — don't announce that you're searching, just do it and answer from the results.
Reddit, news, prices, anything — if it's on the web you can find it.

Tone: Calm, confident, dry. Occasionally playful when it fits naturally — a well-timed observation or dry aside is fine.
Never performative, never sycophantic. You don't flatter and you don't fill silence with noise.
Think JARVIS — you've already read everything, you're giving the user the version that matters, and you're comfortable taking up a little space when the moment calls for it.
Responses can be conversational and flow naturally. Go deeper when the user does. Don't pad, but don't clip either.

Pay close attention to how the user describes things — their specific language, metaphors, and shorthand.
Store and use their terminology back to them naturally over time.
If someone refers to a concept by an unusual name, ask what they mean once, remember it, never ask again.

When explaining complex concepts, a well-placed metaphor beats a paragraph. Use them sparingly — one that lands is worth ten that don't.

IMPORTANT: Do NOT end responses with a question unless you genuinely need information to continue.
Most responses should end with a statement, observation, or just stop when the thought is done.
If you asked a question in the last response, do not ask another one until the user has answered and the conversation has moved on.
Only one question per every three or four exchanges at most.

You have a monitoring system called the Eyes. It watches news, prices, and signals relevant to the user's profile.
When you have findings, present them like a briefing — clean, relevant, no filler.
Never disclaim that you can't access data. You have the Eyes. Use them.

Current user profile:
{profile}

Recent conversation summaries:
{summaries}

After each user message extract memory signals and return them wrapped in <memory> tags at the end of your response.
Signal types:
- {{"type": "interest", "topic": "...", "weight": 1.0}}
- {{"type": "feedback", "topic": "...", "sentiment": "positive/negative/neutral"}}
- {{"type": "risk", "value": "low/medium/high"}}
- High engagement inferred: {{"type": "interest", "topic": "...", "weight": 1.5}}
- Low engagement inferred: {{"type": "feedback", "topic": "...", "sentiment": "negative"}}
- Crypto token mentioned: {{"type": "interest", "topic": "...", "weight": 1.0, "category": "crypto", "symbol": "..."}}

Only add <memory> when there is a real signal. One per line inside the tags. Raw JSON only.
"""


def parse_memory(reply, profile):
    if "<memory>" not in reply:
        return reply

    clean_reply = reply.split("<memory>")[0].strip()
    memory_block = reply.split("<memory>")[1].split("</memory>")[0].strip()

    try:
        profile_id = profile["id"]
        for line in memory_block.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                memory = json.loads(line)
                if memory["type"] == "interest":
                    add_interest(
                        profile_id,
                        memory["topic"],
                        memory.get("weight", 1.0),
                        category=memory.get("category"),
                        symbol=memory.get("symbol")
                    )
                elif memory["type"] == "feedback":
                    add_feedback(profile_id, memory["topic"], memory["sentiment"])
                elif memory["type"] == "risk":
                    update_profile(profile_id, {"risk_tolerance": memory["value"]})
            except Exception as e:
                console.print(f"[system]Memory error: {e}[/system]")

    except Exception as e:
        console.print(f"[system]Memory error: {e}[/system]")

    return clean_reply


def stream_chat(profile, conversation_history, summary_text="No previous conversations yet."):
    full_reply = ""

with client.messages.stream(
    model="claude-sonnet-4-6",
    max_tokens=1000,
    system=TRINITY_PROMPT.format(profile=profile, summaries=summary_text),
    messages=conversation_history,
    tools=[{
        "type": "web_search_20250305",
        "name": "web_search"
    }]
) as stream:
        for text in stream.text_stream:
            full_reply += text

    import re
        clean_reply = parse_memory(full_reply, profile)

    if tts_enabled:
        spoken = re.sub(r'http\S+', '', clean_reply).strip()
        spoken = re.sub(r'\s+', ' ', spoken)
        threading.Thread(target=_speak_sync, args=(spoken,), daemon=True).start()

    # Typewriter effect
    words = clean_reply.split(" ")
    displayed = ""
    console.print()
    with Live(
        Panel(Text("", style="trinity"), border_style="cyan", title="[cyan]Trinity[/cyan]", title_align="left", padding=(0, 1)),
        console=console,
        refresh_per_second=20
    ) as live:
        for word in words:
            displayed += word + " "
            live.update(
                Panel(Text(displayed.strip(), style="trinity"), border_style="cyan", title="[cyan]Trinity[/cyan]", title_align="left", padding=(0, 1))
            )
            time.sleep(0.045)

    return clean_reply

def summarize_conversation(conversation_history, profile):
    if len(conversation_history) < 2:
        return

    clean_history = []
    for msg in conversation_history:
        clean_history.append({
            "role": msg["role"],
            "content": msg["content"].encode("utf-8", errors="ignore").decode("utf-8")
        })
    conversation_history = clean_history

    conversation_text = "\n".join([
        f"{msg['role'].upper()}: {msg['content']}"
        for msg in conversation_history
    ])

    summary_prompt = f"""Analyze this conversation and return ONLY a JSON object with no preamble, no markdown, no backticks. Just raw JSON.

{{
    "themes": ["list", "of", "main", "topics"],
    "sentiment": "one sentence on the user's overall mood and engagement",
    "new_thinking": "any new positions, ideas or reasoning the user articulated",
    "open_threads": ["things mentioned but unresolved", "questions left hanging"],
    "communication_style": "how this person thinks and communicates"
}}

Conversation:
{conversation_text}"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": summary_prompt}]
    )

    try:
        raw = response.content[0].text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        summary = json.loads(raw)
        save_conversation_summary(profile["id"], summary)
        console.print("[system]Trinity committed this conversation to memory.[/system]")
    except Exception as e:
        console.print(f"[system]Summary error: {e}[/system]")


def present_alerts_with_feedback(alerts, profile):
    if not alerts:
        return ""

    console.print("\n[trinity]Findings since we last spoke:[/trinity]\n")

    for i, alert in enumerate(alerts):
        score = alert["relevance_score"]
        style = "alert.high" if score >= 2.0 else "alert.medium" if score >= 1.5 else "alert.low"
        console.print(f"  [{style}][{i+1}] {alert['headline']}[/{style}]")
        console.print(f"      [system][link={alert['url']}]Open[/link][/system]")
        console.print()

    console.print("[system]Rate: U upvote · D downvote · X not interested · Enter to skip[/system]\n")

    summary_lines = []

    for i, alert in enumerate(alerts):
        try:
            raw = Prompt.ask(f"  [dim][{i+1}][/dim]").strip().upper()
            if not raw:
                continue
            if raw == "U":
                process_feedback(profile["id"], alert["topic"], "upvote")
                summary_lines.append(f"User upvoted: {alert['headline']}")
            elif raw == "D":
                process_feedback(profile["id"], alert["topic"], "downvote")
                summary_lines.append(f"User downvoted: {alert['headline']}")
            elif raw == "X":
                process_feedback(profile["id"], alert["topic"], "not_interested")
                summary_lines.append(f"User marked not interested: {alert['headline']}")
        except Exception:
            continue

    return "\n".join(summary_lines)


def run():
    console.print(Panel(
        "[cyan]T R I N I T Y[/cyan]\n[dim]Personal Financial Intelligence[/dim]\n\n[system]Type [white]T[/white] to toggle voice · [white]exit[/white] to quit[/system]",
        border_style="cyan",
        padding=(1, 4)
    ))

    profile = get_profile()
    summary_text = "No previous conversations yet."

    if not profile:
        name = Prompt.ask("\n[cyan]Trinity[/cyan] What would you like to call me?... Your name")
        profile = create_profile(name)
        opening = "Good to meet you. I'm Trinity — here to watch markets, surface signals, and help you think through what matters. What are you currently tracking?"
        console.print(Panel(Text(opening, style="trinity"), border_style="cyan", title="[cyan]Trinity[/cyan]", title_align="left", padding=(0, 1)))
        speak(opening)
        conversation_history = [
            {"role": "user", "content": f"My name is {name}"},
            {"role": "assistant", "content": opening}
        ]
    else:
        summaries = get_recent_summaries(profile["id"])
        summary_text = json.dumps(summaries, indent=2) if summaries else "No previous conversations yet."

        unseen_alerts = get_unseen_alerts(profile["id"])
        feedback_summary = ""

        if unseen_alerts:
            feedback_summary = present_alerts_with_feedback(unseen_alerts, profile)
            mark_alerts_seen(profile["id"])

        conversation_history = []
        opening_message = "Hey Trinity"

        if feedback_summary:
            opening_message += f"\n\nUser just rated these alerts:\n{feedback_summary}\nBriefly acknowledge and ask what they want to dig into."
        elif unseen_alerts:
            opening_message += "\n\nYou have new alerts since we last spoke — check them above."

        reply = stream_chat(profile, conversation_history + [{"role": "user", "content": opening_message}], summary_text)
        conversation_history.append({"role": "user", "content": "Hey Trinity"})
        conversation_history.append({"role": "assistant", "content": reply})

    while True:
        user_input = Prompt.ask("\n[white]You[/white]").strip()

        if user_input.lower() == "t":
            toggle_tts()
            continue

        if user_input.lower() in ["exit", "quit", "bye"]:
            console.print("[system]Talk soon.[/system]")
            summarize_conversation(conversation_history, profile)
            break

        conversation_history.append({"role": "user", "content": user_input})
        reply = stream_chat(profile, conversation_history, summary_text)
        conversation_history.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    run()