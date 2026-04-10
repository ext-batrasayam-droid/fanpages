import re
import json
from googleapiclient.discovery import build

API_KEY = "AIzaSyAfcuUvJj4KLbQ8n5AxGRTpadT5yo0-Tpg"

def get_youtube_client():
    return build("youtube", "v3", developerKey=API_KEY)

def extract_channel_id(url_or_id):
    url_or_id = url_or_id.strip()
    if re.match(r'^UC[a-zA-Z0-9_-]{22}$', url_or_id):
        return url_or_id
    if '@' in url_or_id:
        handle = re.search(r'@([a-zA-Z0-9_.-]+)', url_or_id)
        if handle:
            return resolve_handle('@' + handle.group(1))
    match = re.search(r'/channel/(UC[a-zA-Z0-9_-]{22})', url_or_id)
    if match:
        return match.group(1)
    match = re.search(r'(?:/c/|/user/)([a-zA-Z0-9_.-]+)', url_or_id)
    if match:
        return resolve_username(match.group(1))
    return None

def resolve_handle(handle):
    try:
        yt = get_youtube_client()
        res = yt.search().list(part="snippet", q=handle, type="channel", maxResults=1).execute()
        if res["items"]:
            return res["items"][0]["snippet"]["channelId"]
    except:
        pass
    return None

def resolve_username(username):
    try:
        yt = get_youtube_client()
        res = yt.channels().list(part="id", forUsername=username).execute()
        if res.get("items"):
            return res["items"][0]["id"]
    except:
        pass
    return None

def fetch_channel_stats(channel_id):
    try:
        yt = get_youtube_client()
        res = yt.channels().list(
            part="snippet,statistics,contentDetails,brandingSettings",
            id=channel_id
        ).execute()
        if not res.get("items"):
            return None
        item = res["items"][0]
        stats = item.get("statistics", {})
        snippet = item.get("snippet", {})
        branding = item.get("brandingSettings", {}).get("image", {})

        subscribers = int(stats.get("subscriberCount", 0))
        total_views = int(stats.get("viewCount", 0))
        video_count = int(stats.get("videoCount", 0))
        avg_views = round(total_views / video_count) if video_count > 0 else 0
        engagement_rate = round((avg_views / subscribers * 100), 2) if subscribers > 0 else 0

        thumbnail = snippet.get("thumbnails", {}).get("high", {}).get("url", "")

        # Fetch top 5 videos
        top_videos = fetch_top_videos(channel_id, yt)

        return {
            "channel_id": channel_id,
            "channel_name": snippet.get("title", "N/A"),
            "description": snippet.get("description", "")[:300],
            "country": snippet.get("country", "N/A"),
            "thumbnail": thumbnail,
            "subscribers": subscribers,
            "total_views": total_views,
            "video_count": video_count,
            "avg_views_per_video": int(avg_views),
            "engagement_rate": engagement_rate,
            "url": f"https://www.youtube.com/channel/{channel_id}",
            "top_videos": top_videos
        }
    except Exception as e:
        return {"error": str(e), "channel_id": channel_id}

def fetch_top_videos(channel_id, yt=None):
    try:
        if not yt:
            yt = get_youtube_client()
        search_res = yt.search().list(
            part="snippet",
            channelId=channel_id,
            order="viewCount",
            type="video",
            maxResults=5
        ).execute()
        videos = []
        for item in search_res.get("items", []):
            vid_id = item["id"]["videoId"]
            vid_res = yt.videos().list(part="statistics,snippet", id=vid_id).execute()
            if vid_res.get("items"):
                v = vid_res["items"][0]
                videos.append({
                    "video_id": vid_id,
                    "title": v["snippet"]["title"],
                    "thumbnail": v["snippet"]["thumbnails"]["medium"]["url"],
                    "views": int(v["statistics"].get("viewCount", 0)),
                    "likes": int(v["statistics"].get("likeCount", 0)),
                    "comments": int(v["statistics"].get("commentCount", 0)),
                    "published": v["snippet"]["publishedAt"][:10],
                    "url": f"https://www.youtube.com/watch?v={vid_id}"
                })
        return videos
    except:
        return []
