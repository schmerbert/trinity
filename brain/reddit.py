import os
import praw
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parent.parent / '.env')


def _client():
    return praw.Reddit(
        client_id=os.getenv("REDDIT_CLIENT_ID", ""),
        client_secret=os.getenv("REDDIT_CLIENT_SECRET", ""),
        username=os.getenv("REDDIT_USERNAME", ""),
        password=os.getenv("REDDIT_PASSWORD", ""),
        user_agent=os.getenv("REDDIT_USER_AGENT", "Trinity/1.0"),
    )


def post_to_reddit(subreddit: str, title: str, body: str) -> dict:
    try:
        reddit = _client()
        sub = reddit.subreddit(subreddit)
        submission = sub.submit(title=title, selftext=body)
        return {
            "success": True,
            "url": f"https://reddit.com{submission.permalink}",
            "id": submission.id,
            "subreddit": subreddit,
        }
    except Exception as e:
        return {"error": str(e)}
