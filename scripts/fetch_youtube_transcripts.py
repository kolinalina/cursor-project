import os
import re
import requests
import time
from datetime import datetime
from pathlib import Path
import html
from dotenv import load_dotenv
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
from youtube_transcript_api._errors import VideoUnavailable

# Load API Key from .env
load_dotenv()
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

SOURCES_MD = Path("research/sources.md")
TRANSCRIPT_ROOT = Path("research/youtube-transcripts")

YOUTUBE_CHANNEL_URL_RE = re.compile(r"https://www\.youtube\.com/@([A-Za-z0-9_\-]+)/?")

def parse_experts_from_sources(sources_md: Path):
    """Parse experts and YouTube channel URLs from sources.md"""
    experts = []
    with sources_md.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    cur_expert = {}
    for line in lines:
        line = line.strip()
        if line.startswith("##"):
            if cur_expert:
                experts.append(cur_expert)
            cur_expert = {"name": line.strip(" #.")}
        elif line.startswith("- **YouTube:**"):
            yt_url = line.split("**YouTube:**")[-1].strip()
            cur_expert["youtube_url"] = yt_url if "youtube.com" in yt_url else None
        elif line.startswith("- **Annotation:**"):
            annotation = line.split("**Annotation:**")[-1].strip()
            cur_expert["annotation"] = annotation
    if cur_expert:
        experts.append(cur_expert)
    # Only keep those with a YouTube URL
    experts = [e for e in experts if e.get("youtube_url")]
    return experts

def channel_handle_from_url(url):
    """Extract channel handle from YouTube @ URL."""
    m = YOUTUBE_CHANNEL_URL_RE.search(url)
    if m:
        return m.group(1)
    return None

def youtube_channel_id_from_handle(handle):
    """Find YouTube channel ID from a handle using the API."""
    url = (
        f"https://www.googleapis.com/youtube/v3/channels"
        f"?part=id&forHandle={handle}&key={YOUTUBE_API_KEY}"
    )
    r = requests.get(url)
    r.raise_for_status()
    data = r.json()
    items = data.get("items", [])
    if not items:
        return None
    return items[0]["id"]

def fetch_latest_videos(channel_id, max_results=10):
    """Fetch the latest videos for a channel ID."""
    url = (
        f"https://www.googleapis.com/youtube/v3/search?"
        f"key={YOUTUBE_API_KEY}"
        f"&channelId={channel_id}"
        f"&part=snippet"
        f"&order=date"
        f"&maxResults={max_results}"
        f"&type=video"
    )
    r = requests.get(url)
    r.raise_for_status()
    items = r.json().get("items", [])
    videos = []
    for item in items:
        snippet = item["snippet"]
        video_id = item["id"]["videoId"]
        videos.append({
            "video_id": video_id,
            "title": snippet["title"],
            "published_at": snippet["publishedAt"],
            "url": f"https://www.youtube.com/watch?v={video_id}"
        })
    return videos

def save_transcript_markdown(expert_name, video, transcript, fetch_dt):
    """Save transcript as markdown file."""
    safe_name = re.sub(r'[\\/*?:"<>|]', '_', expert_name)
    expert_dir = TRANSCRIPT_ROOT / safe_name
    expert_dir.mkdir(parents=True, exist_ok=True)

    safe_title = re.sub(r'[\\/*?:"<>|]', '_', html.unescape(video["title"]))[:60]
    md_path = expert_dir / f"{safe_title}-{video['video_id']}.md"

    with md_path.open("w", encoding="utf-8") as f:
        published = datetime.strptime(video['published_at'], "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d")
        fetched = datetime.strptime(fetch_dt, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d")
        
        ff.write(f"# {html.unescape(video['title'])}\n\n")
        f.write(f"- **URL:** {video['url']}\n")
        f.write(f"- **Published:** {published}\n")
        f.write(f"- **Date Fetched:** {fetched}\n\n")
        f.write("## Transcript\n\n")
        for entry in transcript:
            start = int(entry.start)
            minutes = start // 60
            seconds = start % 60
            t = f"[{minutes}:{seconds:02d}]"
            f.write(f"{t} {entry.text}\n\n")

def fetch_and_save_transcripts():
    if not YOUTUBE_API_KEY:
        print("Missing YOUTUBE_API_KEY. Please check your .env file.")
        return

    experts = parse_experts_from_sources(SOURCES_MD)
    for expert in experts:
        name = expert["name"]
        yt_url = expert["youtube_url"]
        if not yt_url or yt_url.lower() == "linkedin only":
            continue
        print(f"\nProcessing: {name} ({yt_url})")
        handle = channel_handle_from_url(yt_url)
        if not handle:
            print(f"  Skipping: not a standard @-handle YouTube URL ({yt_url})")
            continue
        channel_id = youtube_channel_id_from_handle(handle)
        if not channel_id:
            print(f"  Failed to find channel ID for handle: @{handle}")
            continue
        try:
            videos = fetch_latest_videos(channel_id, max_results=10)
        except Exception as e:
            print(f"  Error fetching videos: {e}")
            continue

        for video in videos:
            print(f"  Video: {video['title']}")
            try:
                fetcher = YouTubeTranscriptApi()
                transcript = fetcher.fetch(video["video_id"])
            except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable):
                print("    No transcript available, skipping.")
                continue
            fetch_dt = datetime.utcnow().isoformat(timespec='seconds') + "Z"
            save_transcript_markdown(name, video, transcript, fetch_dt)
            print("    Transcript saved.")
            # To avoid quota/rate limit issues
            time.sleep(1)

if __name__ == "__main__":
    fetch_and_save_transcripts()