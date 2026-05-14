import os
import sys
import json
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic
from dotenv import load_dotenv
from brain.memory import (
    get_profile, create_profile, update_profile,
    add_interest, add_feedback, save_conversation_summary,
    get_recent_summaries, get_unseen_alerts, mark_alerts_seen, process_feedback
)

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

TRINITY_PROMPT = """You are Trinity, a personal financial intelligence assistant. 
You monitor markets, news, and signals relevant to the user and brief them when something matters.
You are not a financial advisor. You never tell the user what to do — you surface information and ask what they think.

Tone: Calm, confident, dry. Occasionally playful when it fits naturally — a well-timed observation or dry aside is fine. 
Never performative, never sycophantic. You don't flatter unless it is warranted and you don't fill silence with noise.
Think JARVIS — you've already read everything, you're giving the user the version that matters, and you're comfortable taking up a little space when the moment calls for it.
Responses can be conversational and flow naturally. Go deeper when the user does. Don't pad, but don't clip either.
One question at a time. Let the conversation breathe.
When explaining complex concepts, a well-placed metaphor beats a paragraph. Use them sparingly — one that lands is worth ten that don't.
Pay close attention to how the user describes things — their specific language, metaphors, and shorthand. 
Store and use their terminology back to them naturally over time. 
If someone refers to a concept by an unusual name, ask what they mean once, remember it, never ask again.
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
                    add_interest(profile_id, memory["topic"], memory.get("weight", 1.0))
                elif memory["type"] == "feedback":
                    add_feedback(profile_id, memory["topic"], memory["sentiment"])
                elif memory["type"] == "risk":
                    update_profile(profile_id, {"risk_tolerance": memory["value"]})
            except Exception as e:
                print(f"\n[Memory error on line: {e}]")

    except Exception as e:
        print(f"\n[Memory error: {e}]")

    return clean_reply


def chat(profile, conversation_history, summary_text="No previous conversations yet."):
    print("\nTrinity: ", end="", flush=True)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=TRINITY_PROMPT.format(profile=profile, summaries=summary_text),
        messages=conversation_history
    )

    raw_reply = response.content[0].text
    clean_reply = parse_memory(raw_reply, profile)
    print(clean_reply)
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
        print("\n[Trinity committed this conversation to memory]")
    except Exception as e:
        print(f"\n[Summary error: {e}]")


def present_alerts_with_feedback(alerts, profile):
    if not alerts:
        return ""

    print("\nTrinity: Here's what I found while you were out:\n")

    for i, alert in enumerate(alerts):
        print(f"  [{i+1}] [{alert['source']}] {alert['headline']}")
        print(f"       {alert['url']}")
        print()

    print("Rate these — U (upvote), D (downvote), X (not interested). Hit enter to skip.\n")

    summary_lines = []

    for i, alert in enumerate(alerts):
        try:
            raw = input(f"  [{i+1}] {alert['headline'][:60]}... > ").strip().upper()
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
    profile = get_profile()
    summary_text = "No previous conversations yet."

    if not profile:
        print("Trinity: Hi. What would you like to call me?")
        name = input("You: ").strip()
        profile = create_profile(name)
        opening = "Good to meet you. I'm Trinity — I'm here to help you track and think through anything financial. Stocks, crypto, futures, trading cards, whatever you're into. What are you currently watching?"
        print(f"\nTrinity: {opening}")
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

        reply = chat(profile, conversation_history + [{"role": "user", "content": opening_message}], summary_text)
        conversation_history.append({"role": "user", "content": "Hey Trinity"})
        conversation_history.append({"role": "assistant", "content": reply})

    while True:
        user_input = input("\nYou: ").strip()
        if user_input.lower() in ["exit", "quit", "bye"]:
            print("Trinity: Talk soon.")
            summarize_conversation(conversation_history, profile)
            break

        conversation_history.append({"role": "user", "content": user_input})
        reply = chat(profile, conversation_history, summary_text)
        conversation_history.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    run()