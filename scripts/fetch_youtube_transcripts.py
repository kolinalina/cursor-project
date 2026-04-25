import os
import re
import requests
import time
from datetime import datetime
from pathlib import Path
import html
import unicodedata
from dotenv import load_dotenv

# Load API Key from .env
load_dotenv()
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
SUPADATA_API_KEY = os.getenv("SUPADATA_API_KEY")

SOURCES_MD = Path("research/sources.md")
TRANSCRIPT_ROOT = Path("research/youtube-transcripts")

YOUTUBE_CHANNEL_URL_RE = re.compile(r"https://www\.youtube\.com/@([A-Za-z0-9_\-]+)/?")
YOUTUBE_SEARCH_QUERY = "SEO AI AI SEO"

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

def fetch_latest_videos(channel_id, max_results=5):
    """Fetch the latest videos for a channel ID."""
    url = (
        f"https://www.googleapis.com/youtube/v3/search?"
        f"key={YOUTUBE_API_KEY}"
        f"&channelId={channel_id}"
        f"&part=snippet"
        f"&q={YOUTUBE_SEARCH_QUERY}"
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

def is_relevant_video_title(title):
    """Keep only videos clearly related to both SEO and AI."""
    normalized = html.unescape(title).lower()
    has_seo = "seo" in normalized or "search engine optimization" in normalized
    has_ai = " ai " in f" {normalized} " or "artificial intelligence" in normalized
    return has_seo and has_ai

def slugify_expert_name(expert_name):
    """Convert expert name into a clean folder slug."""
    # Remove prefixes like "1. ", "12. ", etc.
    cleaned = re.sub(r"^\s*\d+\.\s*", "", expert_name).strip()
    # Normalize unicode (e.g. Solís -> Solis), then slugify
    ascii_name = unicodedata.normalize("NFKD", cleaned).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug or "unknown-expert"

def fetch_transcript_supadata(video_id):
    """Fetch transcript data for a YouTube video from Supadata API."""
    url = "https://api.supadata.ai/v1/youtube/transcript"
    headers = {"x-api-key": SUPADATA_API_KEY}
    params = {"videoId": video_id}

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()

    content = payload.get("content", [])
    transcript = []
    for entry in content:
        text = entry.get("text")
        if not text:
            continue
        start = entry.get("offset") or entry.get("start") or 0
        try:
            start = int(float(start))
        except (TypeError, ValueError):
            start = 0
        transcript.append({"start": start, "text": text})
    return transcript

def save_transcript_markdown(expert_name, video, transcript, fetch_dt):
    """Save transcript as markdown file."""
    safe_name = slugify_expert_name(expert_name)
    expert_dir = TRANSCRIPT_ROOT / safe_name
    expert_dir.mkdir(parents=True, exist_ok=True)

    safe_title = re.sub(r'[\\/*?:"<>|]', '_', html.unescape(video["title"]))[:60]
    md_path = expert_dir / f"{safe_title}.md"

    with md_path.open("w", encoding="utf-8") as f:
        published = datetime.strptime(video['published_at'], "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d")
        fetched = fetch_dt

        f.write(f"# {html.unescape(video['title'])}\n\n")
        f.write(f"- **URL:** {video['url']}\n")
        f.write(f"- **Published:** {published}\n")
        f.write(f"- **Date Fetched:** {fetched}\n\n")
        f.write("## Transcript\n\n")
        for entry in transcript:
            if isinstance(entry, dict):
                start = int(entry.get("start", 0))
                text = entry.get("text", "")
            else:
                start = int(entry.start)
                text = entry.text
            minutes = start // 60
            seconds = start % 60
            t = f"[{minutes}:{seconds:02d}]"
            f.write(f"{t} {text}\n\n")

def fetch_and_save_transcripts():
    if not YOUTUBE_API_KEY:
        print("Missing YOUTUBE_API_KEY. Please check your .env file.")
        return
    if not SUPADATA_API_KEY:
        print("Missing SUPADATA_API_KEY. Please check your .env file.")
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
            videos = fetch_latest_videos(channel_id, max_results=5)
        except Exception as e:
            print(f"  Error fetching videos: {e}")
            continue
        videos = [v for v in videos if is_relevant_video_title(v["title"])]
        if not videos:
            print("  No relevant SEO+AI videos found, skipping.")
            continue

        for video in videos:
            print(f"  Video: {video['title']}")
            try:
                transcript = fetch_transcript_supadata(video["video_id"])
                if not transcript:
                    print("    No transcript content returned, skipping.")
                    continue
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else "unknown"
                if status in (401, 403, 404):
                    print(f"    Transcript unavailable ({status}), skipping.")
                else:
                    print(f"    Supadata HTTP error ({status}), skipping.")
                continue
            except requests.RequestException as e:
                print(f"    Network error fetching transcript: {e.__class__.__name__}, skipping.")
                continue
            except ValueError:
                print("    Invalid Supadata response format, skipping.")
                continue
            except KeyError:
                print("    No transcript available, skipping.")
                continue
            except Exception as e:
                print(f"    Unexpected error: {e.__class__.__name__}, skipping.")
                continue
            fetch_dt = datetime.utcnow().strftime("%Y-%m-%d")
            save_transcript_markdown(name, video, transcript, fetch_dt)
            print("    Transcript saved.")
            # To avoid quota/rate limit issues
            time.sleep(3)

if __name__ == "__main__":
    fetch_and_save_transcripts()