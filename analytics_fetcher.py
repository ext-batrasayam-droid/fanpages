import json
import os
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

CLIENT_SECRETS_FILE = "client_secret_813198838998-f96bjkb6crs8m9b1qgib9l0c7mjgktk6.apps.googleusercontent.com.json"

SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.readonly",
]

def get_credentials(token_json: str):
    creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds

def get_analytics_service(token_json: str):
    creds = get_credentials(token_json)
    return build("youtubeAnalytics", "v2", credentials=creds)

def get_youtube_service(token_json: str):
    creds = get_credentials(token_json)
    return build("youtube", "v3", credentials=creds)

def fetch_studio_analytics(token_json: str, channel_id: str, start_date: str, end_date: str):
    """Fetch full Studio-level analytics for a channel."""
    try:
        svc = get_analytics_service(token_json)

        def query(metrics, dimensions=None, filters=None, sort=None, max_results=None):
            params = dict(
                ids=f"channel=={channel_id}",
                startDate=start_date,
                endDate=end_date,
                metrics=metrics,
            )
            if dimensions:  params["dimensions"] = dimensions
            if filters:     params["filters"] = filters
            if sort:        params["sort"] = sort
            if max_results: params["maxResults"] = max_results
            return svc.reports().query(**params).execute()

        # ── Overview (all key metrics) ─────────────────────────────
        overview = query(
            "views,estimatedMinutesWatched,averageViewDuration,"
            "averageViewPercentage,likes,comments,shares,"
            "subscribersGained,subscribersLost,"
            "impressions,impressionClickThroughRate,"
            "annotationClickThroughRate,cardClickRate"
        )

        # ── Daily time series ──────────────────────────────────────
        daily = query(
            "views,estimatedMinutesWatched,likes,subscribersGained,"
            "impressions,impressionClickThroughRate",
            dimensions="day", sort="day"
        )

        # ── Traffic sources ────────────────────────────────────────
        traffic = query(
            "views,estimatedMinutesWatched",
            dimensions="insightTrafficSourceType",
            sort="-views", max_results=10
        )

        # ── Top videos with engaged views ─────────────────────────
        top_vids = query(
            "views,estimatedMinutesWatched,averageViewDuration,"
            "averageViewPercentage,likes,comments,shares,"
            "impressions,impressionClickThroughRate",
            dimensions="video", sort="-views", max_results=15
        )

        # ── Geography ─────────────────────────────────────────────
        geo = query(
            "views,estimatedMinutesWatched",
            dimensions="country", sort="-views", max_results=10
        )

        # ── Age/Gender ────────────────────────────────────────────
        age_gender = query("viewerPercentage", dimensions="ageGroup,gender")

        # ── Device type ───────────────────────────────────────────
        device = query("views,estimatedMinutesWatched", dimensions="deviceType", sort="-views")

        # ── Shorts specific ───────────────────────────────────────
        try:
            shorts_overview = query(
                "views,estimatedMinutesWatched,likes,comments,shares,"
                "subscribersGained,impressionClickThroughRate",
                filters="isShortsEligible==1"
            )
            shorts_daily = query(
                "views,estimatedMinutesWatched,likes",
                dimensions="day", sort="day",
                filters="isShortsEligible==1"
            )
        except:
            shorts_overview = None
            shorts_daily = None

        # ── Subscription sources ──────────────────────────────────
        try:
            sub_sources = query(
                "subscribersGained,subscribersLost",
                dimensions="insightTrafficSourceType",
                sort="-subscribersGained", max_results=8
            )
        except:
            sub_sources = None

        ov = _parse_overview(overview)

        # Compute "stayed to watch" = averageViewPercentage
        # Compute "engaged views" = views where like/comment/share happened (approximated)
        engaged = None
        try:
            eng_res = query(
                "views",
                filters="engagementType==ENGAGED_VIEW"
            )
            engaged = _parse_overview(eng_res).get("views", 0)
        except:
            pass

        return {
            "overview": ov,
            "engaged_views": engaged,
            "daily": _parse_rows(daily),
            "traffic": _parse_rows(traffic),
            "top_videos": _parse_rows(top_vids),
            "geo": _parse_rows(geo),
            "age_gender": _parse_rows(age_gender),
            "device": _parse_rows(device),
            "shorts_overview": _parse_overview(shorts_overview) if shorts_overview else None,
            "shorts_daily": _parse_rows(shorts_daily) if shorts_daily else [],
            "sub_sources": _parse_rows(sub_sources) if sub_sources else [],
        }
    except Exception as e:
        return {"error": str(e)}

def _parse_overview(res):
    if not res or not res.get("rows"):
        return {}
    headers = [h["name"] for h in res["columnHeaders"]]
    row = res["rows"][0]
    return dict(zip(headers, row))

def _parse_rows(res):
    if not res or not res.get("rows"):
        return []
    headers = [h["name"] for h in res["columnHeaders"]]
    return [dict(zip(headers, row)) for row in res["rows"]]
