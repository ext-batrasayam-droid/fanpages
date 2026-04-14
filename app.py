from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, flash, session
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from database import db, User, Channel, Snapshot, SavedReel, InternNote, OAuthToken
from youtube_fetcher import extract_channel_id, fetch_channel_stats
from analytics_fetcher import CLIENT_SECRETS_FILE, SCOPES, fetch_studio_analytics
from report_generator import generate_excel
from datetime import datetime, timedelta
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
import json, os

app = Flask(__name__)
app.config["SECRET_KEY"] = "yt-shorts-secret-2024"
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///ytreport.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # allow http for localhost

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))

# ── Seed default users ────────────────────────────────────────────────────────
def seed_users():
    # Remove old generic accounts and their channels safely
    with db.session.no_autoflush:
        for old in ["intern1", "intern2", "intern3", "intern4", "intern5", "reviewer1", "reviewer2"]:
            u = User.query.filter_by(username=old).first()
            if u:
                Channel.query.filter_by(user_id=u.id).delete()
                db.session.delete(u)
    db.session.commit()

    defaults = [
        ("basudha",    "Basudha@123",    "reviewer", "Basudha"),
        ("sudhanshu",  "Sudhanshu@123",  "reviewer", "Sudhanshu"),
        ("sayam",      "Sayam@123",      "intern",   "Sayam"),
        ("saurabh",    "Saurabh@123",    "intern",   "Saurabh"),
        ("vivek",      "Vivek@123",      "intern",   "Vivek"),
        ("adishreya",  "Adishreya@123",  "intern",   "Adishreya"),
        ("harsh",      "Harsh@123",      "intern",   "Harsh"),
    ]
    colors = ["#ff0000","#e91e63","#9c27b0","#3f51b5","#009688","#ff9800","#795548"]
    for i, (uname, pwd, role, name) in enumerate(defaults):
        u = User.query.filter_by(username=uname).first()
        if u:
            # Always force-update password and role on startup
            u.password = generate_password_hash(pwd)
            u.role = role
            u.full_name = name
        else:
            db.session.add(User(
                username=uname,
                password=generate_password_hash(pwd),
                role=role,
                full_name=name,
                avatar_color=colors[i % len(colors)]
            ))
    db.session.commit()

with app.app_context():
    db.create_all()
    seed_users()

# ── Auth ──────────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET","POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        u = User.query.filter_by(username=request.form["username"]).first()
        if u and check_password_hash(u.password, request.form["password"]):
            login_user(u)
            return redirect(url_for("dashboard"))
        flash("Invalid credentials")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    if current_user.role == "reviewer":
        return redirect(url_for("reviewer_dashboard"))
    return redirect(url_for("intern_dashboard"))

# ── Intern Routes ─────────────────────────────────────────────────────────────
@app.route("/intern/dashboard")
@login_required
def intern_dashboard():
    if current_user.role != "intern":
        return redirect(url_for("reviewer_dashboard"))
    channels = Channel.query.filter_by(user_id=current_user.id).all()
    stats = _intern_stats(channels)
    return render_template("intern/dashboard.html", channels=channels, stats=stats)

@app.route("/intern/profile", methods=["GET","POST"])
@login_required
def intern_profile():
    if request.method == "POST":
        current_user.full_name = request.form.get("full_name", current_user.full_name)
        current_user.bio = request.form.get("bio", current_user.bio)
        current_user.avatar_color = request.form.get("avatar_color", current_user.avatar_color)
        if request.form.get("new_password"):
            current_user.password = generate_password_hash(request.form["new_password"])
        db.session.commit()
        flash("Profile updated!")
    return render_template("intern/profile.html")

@app.route("/intern/channels")
@login_required
def intern_channels():
    channels = Channel.query.filter_by(user_id=current_user.id).all()
    return render_template("intern/channels.html", channels=channels)

@app.route("/intern/channel/add", methods=["POST"])
@login_required
def add_channel():
    url = request.form.get("url","").strip()
    category = request.form.get("category","")
    notes = request.form.get("notes","")
    if not url:
        flash("URL required")
        return redirect(url_for("intern_channels"))
    cid = extract_channel_id(url)
    if not cid:
        flash("Could not resolve channel ID from that URL")
        return redirect(url_for("intern_channels"))
    if Channel.query.filter_by(user_id=current_user.id, channel_id=cid).first():
        flash("Channel already added")
        return redirect(url_for("intern_channels"))
    ch = Channel(user_id=current_user.id, channel_id=cid, channel_url=url, category=category, notes=notes)
    db.session.add(ch)
    db.session.commit()
    # Fetch initial snapshot
    _refresh_channel(ch)
    flash("Channel added!")
    return redirect(url_for("intern_channels"))

@app.route("/intern/channel/<int:cid>/delete", methods=["POST"])
@login_required
def delete_channel(cid):
    ch = Channel.query.get_or_404(cid)
    if ch.user_id != current_user.id and current_user.role != "reviewer":
        return "Forbidden", 403
    db.session.delete(ch)
    db.session.commit()
    flash("Channel removed")
    return redirect(request.referrer or url_for("intern_channels"))

@app.route("/intern/channel/<int:cid>/refresh", methods=["POST"])
@login_required
def refresh_channel(cid):
    ch = Channel.query.get_or_404(cid)
    _refresh_channel(ch)
    flash("Channel refreshed!")
    return redirect(request.referrer or url_for("intern_channels"))

@app.route("/intern/channel/<int:cid>/edit", methods=["POST"])
@login_required
def edit_channel(cid):
    ch = Channel.query.get_or_404(cid)
    if ch.user_id != current_user.id:
        return "Forbidden", 403
    ch.category = request.form.get("category", ch.category)
    ch.notes = request.form.get("notes", ch.notes)
    db.session.commit()
    return redirect(url_for("intern_channels"))

# ── Reviewer Routes ───────────────────────────────────────────────────────────
@app.route("/reviewer/dashboard")
@login_required
def reviewer_dashboard():
    if current_user.role != "reviewer":
        return redirect(url_for("intern_dashboard"))
    interns = User.query.filter_by(role="intern").all()
    all_channels = Channel.query.all()
    stats = _global_stats(all_channels)

    def _sub_count(ch):
        s = _latest_snap(ch)
        return s.subscribers if s else 0
    top_channels = sorted(all_channels, key=_sub_count, reverse=True)[:8]

    # Top intern leaderboard
    period = request.args.get("period", "month")
    now = datetime.utcnow()
    if period == "day":
        since = now - timedelta(days=1)
    elif period == "year":
        since = now - timedelta(days=365)
    else:  # month
        since = now - timedelta(days=30)

    leaderboard = []
    for intern in interns:
        channels = Channel.query.filter_by(user_id=intern.id).all()
        total_subs, total_views, total_eng, ch_count = 0, 0, 0, 0
        channels_added = Channel.query.filter_by(user_id=intern.id)\
            .filter(Channel.added_at >= since).count()
        for ch in channels:
            snap = Snapshot.query.filter_by(channel_id=ch.id)\
                .filter(Snapshot.fetched_at >= since)\
                .order_by(Snapshot.fetched_at.desc()).first()
            if not snap:
                snap = _latest_snap(ch)
            if snap:
                total_subs += snap.subscribers
                total_views += snap.total_views
                total_eng += snap.engagement_rate
                ch_count += 1
        avg_eng = round(total_eng / ch_count, 2) if ch_count else 0
        score = (total_subs * 0.4) + (total_views * 0.3) + (avg_eng * 10000) + (channels_added * 5000)
        leaderboard.append({
            "intern": intern,
            "channels": len(channels),
            "channels_added": channels_added,
            "subscribers": total_subs,
            "views": total_views,
            "avg_eng": avg_eng,
            "score": int(score)
        })
    leaderboard.sort(key=lambda x: x["score"], reverse=True)

    return render_template("reviewer/dashboard.html",
        interns=interns, all_channels=all_channels,
        top_channels=top_channels, stats=stats,
        leaderboard=leaderboard, period=period)

@app.route("/reviewer/channels")
@login_required
def reviewer_channels():
    if current_user.role != "reviewer":
        return "Forbidden", 403
    intern_id = request.args.get("intern_id", "all")
    category = request.args.get("category", "all")
    sort = request.args.get("sort", "subscribers")
    order = request.args.get("order", "desc")

    q = Channel.query
    if intern_id != "all":
        q = q.filter_by(user_id=int(intern_id))
    channels = q.all()

    # Enrich with latest snapshot
    enriched = []
    for ch in channels:
        snap = _latest_snap(ch)
        if snap:
            row = _snap_to_dict(ch, snap)
            if category != "all" and row["category"] != category:
                continue
            enriched.append(row)

    # Sort
    reverse = order == "desc"
    enriched.sort(key=lambda x: x.get(sort, 0) or 0, reverse=reverse)

    interns = User.query.filter_by(role="intern").all()
    categories = list(set(c.category for c in Channel.query.all() if c.category))

    return render_template("reviewer/channels.html",
        channels=enriched, interns=interns, categories=categories,
        filters={"intern_id":intern_id,"category":category,"sort":sort,"order":order})

@app.route("/reviewer/insights")
@login_required
def reviewer_insights():
    return redirect(url_for("reviewer_analytics"))

@app.route("/reviewer/analytics")
@login_required
def reviewer_analytics():
    if current_user.role != "reviewer":
        return "Forbidden", 403
    now = datetime.utcnow()
    date_from = request.args.get("from", "").strip() or (now - timedelta(days=30)).strftime("%Y-%m-%d")
    date_to   = request.args.get("to",   "").strip() or now.strftime("%Y-%m-%d")
    intern_id = request.args.get("intern_id", "all")
    dt_from = datetime.strptime(date_from, "%Y-%m-%d")
    dt_to   = datetime.strptime(date_to,   "%Y-%m-%d") + timedelta(days=1)
    q = Channel.query
    if intern_id != "all":
        q = q.filter_by(user_id=int(intern_id))
    channels = q.all()
    chart_data = _build_chart_data(channels, dt_from, dt_to)
    # Category breakdown (always all channels)
    all_channels = Channel.query.all()
    cat_data = {}
    for ch in all_channels:
        snap = _latest_snap(ch)
        cat = ch.category or "Uncategorized"
        if cat not in cat_data:
            cat_data[cat] = {"count": 0, "subscribers": 0, "views": 0, "engagement": []}
        cat_data[cat]["count"] += 1
        if snap:
            cat_data[cat]["subscribers"] += snap.subscribers
            cat_data[cat]["views"]       += snap.total_views
            cat_data[cat]["engagement"].append(snap.engagement_rate)
    cat_result = []
    for cat, d in cat_data.items():
        avg_eng = round(sum(d["engagement"]) / len(d["engagement"]), 2) if d["engagement"] else 0
        cat_result.append({"category": cat, "count": d["count"],
                           "subscribers": d["subscribers"], "views": d["views"], "avg_engagement": avg_eng})
    cat_result.sort(key=lambda x: x["subscribers"], reverse=True)
    cat_chart = {
        "labels":      [r["category"]      for r in cat_result],
        "counts":      [r["count"]          for r in cat_result],
        "subscribers": [r["subscribers"]    for r in cat_result],
        "views":       [r["views"]          for r in cat_result],
        "engagement":  [r["avg_engagement"] for r in cat_result],
    }
    total_subs  = sum(chart_data["subscribers"])
    total_views = sum(chart_data["views"])
    avg_eng     = round(sum(chart_data["engagement"]) / len(chart_data["engagement"]), 2) if chart_data["engagement"] else 0
    interns = User.query.filter_by(role="intern").all()
    return render_template("reviewer/analytics.html",
        chart_data=json.dumps(chart_data), cat_chart=json.dumps(cat_chart),
        cat_data=cat_result, interns=interns,
        filters={"from": date_from, "to": date_to, "intern_id": intern_id},
        range7 =(now - timedelta(days=7) ).strftime("%Y-%m-%d"),
        range30=(now - timedelta(days=30)).strftime("%Y-%m-%d"),
        range90=(now - timedelta(days=90)).strftime("%Y-%m-%d"),
        today=now.strftime("%Y-%m-%d"),
        summary={"total_subs": total_subs, "total_views": total_views,
                 "avg_eng": avg_eng, "channels": len(chart_data["labels"])})

@app.route("/reviewer/top-videos")
@login_required
def reviewer_top_videos():
    if current_user.role != "reviewer":
        return "Forbidden", 403
    intern_id = request.args.get("intern_id", "all")
    sort = request.args.get("sort", "views")
    q = Channel.query
    if intern_id != "all":
        q = q.filter_by(user_id=int(intern_id))
    channels = q.all()
    all_videos = []
    for ch in channels:
        snap = _latest_snap(ch)
        if snap:
            try:
                videos = json.loads(snap.top_videos or "[]")
                for v in videos:
                    v["channel_name"] = ch.channel_name
                    v["channel_url"] = ch.channel_url
                    v["added_by"] = ch.owner.full_name
                    all_videos.append(v)
            except:
                pass
    all_videos.sort(key=lambda x: x.get(sort, 0) or 0, reverse=True)
    interns = User.query.filter_by(role="intern").all()
    return render_template("reviewer/top_videos.html", videos=all_videos[:100],
        interns=interns, filters={"intern_id": intern_id, "sort": sort})

@app.route("/reviewer/quick-open")
@login_required
def quick_open():
    if current_user.role != "reviewer":
        return "Forbidden", 403
    category = request.args.get("category", "all")
    intern_id = request.args.get("intern_id", "all")

    q = Channel.query
    if intern_id != "all":
        q = q.filter_by(user_id=int(intern_id))
    channels = q.all()

    enriched = []
    for ch in channels:
        snap = _latest_snap(ch)
        if category != "all" and ch.category != category:
            continue
        enriched.append({
            "id": ch.id,
            "name": ch.channel_name or ch.channel_id,
            "url": ch.channel_url,
            "category": ch.category,
            "thumbnail": snap.thumbnail if snap else "",
            "subscribers": snap.subscribers if snap else 0,
            "total_views": snap.total_views if snap else 0,
            "engagement_rate": snap.engagement_rate if snap else 0,
            "added_by": ch.owner.full_name if ch.owner else "",
        })
    enriched.sort(key=lambda x: x["subscribers"], reverse=True)

    interns = User.query.filter_by(role="intern").all()
    return render_template("reviewer/quick_open.html",
        channels=enriched, interns=interns,
        filters={"category": category, "intern_id": intern_id})

@app.route("/reviewer/reports")
@login_required
def reviewer_reports():
    if current_user.role != "reviewer":
        return "Forbidden", 403
    interns = User.query.filter_by(role="intern").all()
    return render_template("reviewer/reports.html", interns=interns)

# ── Saved Reels ───────────────────────────────────────────────────────────────
@app.route("/reviewer/saved-reels")
@login_required
def saved_reels():
    if current_user.role != "reviewer":
        return "Forbidden", 403
    tag = request.args.get("tag", "all")
    q = SavedReel.query.filter_by(saved_by=current_user.id)
    if tag != "all":
        q = q.filter_by(tag=tag)
    reels = q.order_by(SavedReel.saved_at.desc()).all()
    tags = db.session.query(SavedReel.tag).filter(SavedReel.tag != "").distinct().all()
    tags = [t[0] for t in tags]
    return render_template("reviewer/saved_reels.html", reels=reels, tags=tags, active_tag=tag)

@app.route("/reviewer/save-reel", methods=["POST"])
@login_required
def save_reel():
    if current_user.role != "reviewer":
        return "Forbidden", 403
    data = request.get_json()
    existing = SavedReel.query.filter_by(saved_by=current_user.id, video_id=data["video_id"]).first()
    if existing:
        return jsonify({"status": "already_saved"})
    reel = SavedReel(
        saved_by=current_user.id,
        video_id=data["video_id"],
        title=data.get("title",""),
        channel_name=data.get("channel_name",""),
        thumbnail=data.get("thumbnail",""),
        url=data.get("url",""),
        views=data.get("views",0),
        likes=data.get("likes",0),
        comments=data.get("comments",0),
        published=data.get("published",""),
        tag=data.get("tag",""),
        reviewer_note=data.get("note","")
    )
    db.session.add(reel)
    db.session.commit()
    return jsonify({"status": "saved"})

@app.route("/reviewer/saved-reels/<int:rid>/note", methods=["POST"])
@login_required
def update_reel_note(rid):
    reel = SavedReel.query.get_or_404(rid)
    reel.reviewer_note = request.form.get("note", "")
    reel.tag = request.form.get("tag", reel.tag)
    db.session.commit()
    return redirect(url_for("saved_reels"))

@app.route("/reviewer/saved-reels/<int:rid>/delete", methods=["POST"])
@login_required
def delete_reel(rid):
    reel = SavedReel.query.get_or_404(rid)
    db.session.delete(reel)
    db.session.commit()
    return redirect(url_for("saved_reels"))

# ── Intern Notes ──────────────────────────────────────────────────────────────
@app.route("/reviewer/intern/<int:iid>/notes", methods=["GET","POST"])
@login_required
def intern_notes(iid):
    if current_user.role != "reviewer":
        return "Forbidden", 403
    intern = User.query.get_or_404(iid)
    if request.method == "POST":
        note_text = request.form.get("note","").strip()
        if note_text:
            db.session.add(InternNote(reviewer_id=current_user.id, intern_id=iid, note=note_text))
            db.session.commit()
            flash("Note added!")
    notes = InternNote.query.filter_by(intern_id=iid).order_by(InternNote.created_at.desc()).all()
    channels = Channel.query.filter_by(user_id=iid).all()
    channel_stats = []
    for ch in channels:
        snap = _latest_snap(ch)
        if snap:
            channel_stats.append(_snap_to_dict(ch, snap))
    return render_template("reviewer/intern_detail.html", intern=intern, notes=notes, channel_stats=channel_stats)

@app.route("/reviewer/intern-note/<int:nid>/delete", methods=["POST"])
@login_required
def delete_intern_note(nid):
    note = InternNote.query.get_or_404(nid)
    db.session.delete(note)
    db.session.commit()
    return redirect(request.referrer)

# ── Category Insights ─────────────────────────────────────────────────────────
@app.route("/reviewer/category-insights")
@login_required
def category_insights():
    return redirect(url_for("reviewer_analytics"))

@app.route("/reviewer/download-excel")
@login_required
def download_excel():
    if current_user.role != "reviewer":
        return "Forbidden", 403
    intern_id = request.args.get("intern_id", "all")
    q = Channel.query
    if intern_id != "all":
        q = q.filter_by(user_id=int(intern_id))
    channels = q.all()
    data = []
    for ch in channels:
        snap = _latest_snap(ch)
        if snap:
            row = _snap_to_dict(ch, snap)
            row["added_by"] = ch.owner.full_name
            try:
                row["top_videos"] = json.loads(snap.top_videos or "[]")
            except:
                row["top_videos"] = []
            data.append(row)
    output = generate_excel(data, title="YouTube Shorts Full Report")
    return send_file(output,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True, download_name="youtube_report.xlsx")

# ── OAuth & Studio Analytics ─────────────────────────────────────────────────
@app.route("/intern/channel/<int:cid>/connect-google")
@login_required
def connect_google(cid):
    ch = Channel.query.get_or_404(cid)
    if ch.user_id != current_user.id:
        return "Forbidden", 403
    flow = Flow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes=SCOPES,
        redirect_uri=url_for("oauth2callback", _external=True))
    auth_url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent")
    session["oauth_state"] = state
    session["oauth_channel_id"] = cid
    return redirect(auth_url)

@app.route("/oauth2callback")
@login_required
def oauth2callback():
    state = session.get("oauth_state")
    cid = session.get("oauth_channel_id")
    if not state or not cid:
        flash("OAuth session expired. Try again.")
        return redirect(url_for("intern_channels"))
    flow = Flow.from_client_secrets_file(CLIENT_SECRETS_FILE, scopes=SCOPES,
        state=state, redirect_uri=url_for("oauth2callback", _external=True))
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    token_json = creds.to_json()
    existing = OAuthToken.query.filter_by(channel_id=cid).first()
    if existing:
        existing.token_json = token_json
        existing.connected_at = datetime.utcnow()
    else:
        db.session.add(OAuthToken(channel_id=cid, token_json=token_json))
    db.session.commit()
    flash("Google account connected! Studio analytics are now available.")
    return redirect(url_for("studio_analytics", cid=cid))

@app.route("/intern/channel/<int:cid>/studio-analytics")
@login_required
def studio_analytics(cid):
    ch = Channel.query.get_or_404(cid)
    if ch.user_id != current_user.id and current_user.role != "reviewer":
        return "Forbidden", 403
    token = OAuthToken.query.filter_by(channel_id=cid).first()
    if not token:
        flash("Connect your Google account first to see Studio analytics.")
        return redirect(url_for("intern_channels"))

    end = datetime.utcnow().strftime("%Y-%m-%d")
    start = request.args.get("from", (datetime.utcnow() - timedelta(days=28)).strftime("%Y-%m-%d"))
    end = request.args.get("to", end)

    data = fetch_studio_analytics(token.token_json, ch.channel_id, start, end)
    if "error" in data:
        flash(f"Analytics error: {data['error']}")
        return redirect(url_for("intern_channels"))

    now = datetime.utcnow()
    return render_template("intern/studio_analytics.html", ch=ch, data=data,
        filters={"from": start, "to": end},
        range7 =(now - timedelta(days=7) ).strftime("%Y-%m-%d"),
        range28=(now - timedelta(days=28)).strftime("%Y-%m-%d"),
        range90=(now - timedelta(days=90)).strftime("%Y-%m-%d"),
        today=now.strftime("%Y-%m-%d"))

@app.route("/intern/channel/<int:cid>/disconnect-google", methods=["POST"])
@login_required
def disconnect_google(cid):
    token = OAuthToken.query.filter_by(channel_id=cid).first()
    if token:
        db.session.delete(token)
        db.session.commit()
    flash("Google account disconnected.")
    return redirect(url_for("intern_channels"))

# ── API ───────────────────────────────────────────────────────────────────────
@app.route("/api/stats")
@login_required
def api_stats():
    channels = Channel.query.filter_by(user_id=current_user.id).all() if current_user.role == "intern" else Channel.query.all()
    data = []
    for ch in channels:
        snap = _latest_snap(ch)
        if snap:
            data.append(_snap_to_dict(ch, snap))
    return jsonify(data)

@app.route("/api/refresh-all", methods=["POST"])
@login_required
def refresh_all():
    if current_user.role == "intern":
        channels = Channel.query.filter_by(user_id=current_user.id).all()
    else:
        channels = Channel.query.all()
    for ch in channels:
        _refresh_channel(ch)
    return jsonify({"status": "ok", "count": len(channels)})

# ── Helpers ───────────────────────────────────────────────────────────────────
def _refresh_channel(ch):
    stats = fetch_channel_stats(ch.channel_id)
    if stats and "error" not in stats:
        ch.channel_name = stats["channel_name"]
        ch.country = stats["country"]
        snap = Snapshot(
            channel_id=ch.id,
            subscribers=stats["subscribers"],
            total_views=stats["total_views"],
            video_count=stats["video_count"],
            avg_views=stats["avg_views_per_video"],
            engagement_rate=stats["engagement_rate"],
            description=stats.get("description",""),
            thumbnail=stats.get("thumbnail",""),
            top_videos=json.dumps(stats.get("top_videos",[]))
        )
        db.session.add(snap)
        db.session.commit()

def _latest_snap(ch):
    return Snapshot.query.filter_by(channel_id=ch.id).order_by(Snapshot.fetched_at.desc()).first()

def _snap_to_dict(ch, snap):
    return {
        "id": ch.id,
        "channel_id": ch.channel_id,
        "channel_name": ch.channel_name,
        "url": ch.channel_url,
        "country": ch.country,
        "category": ch.category,
        "notes": ch.notes,
        "thumbnail": snap.thumbnail,
        "subscribers": snap.subscribers,
        "total_views": snap.total_views,
        "video_count": snap.video_count,
        "avg_views_per_video": snap.avg_views,
        "engagement_rate": snap.engagement_rate,
        "description": snap.description,
        "fetched_at": snap.fetched_at.strftime("%Y-%m-%d %H:%M"),
        "added_by": ch.owner.full_name if ch.owner else ""
    }

def _intern_stats(channels):
    snaps = [_latest_snap(c) for c in channels]
    snaps = [s for s in snaps if s]
    if not snaps:
        return {"total": 0, "subscribers": 0, "views": 0, "avg_eng": 0}
    return {
        "total": len(channels),
        "subscribers": sum(s.subscribers for s in snaps),
        "views": sum(s.total_views for s in snaps),
        "avg_eng": round(sum(s.engagement_rate for s in snaps) / len(snaps), 2)
    }

def _global_stats(channels):
    snaps = [_latest_snap(c) for c in channels]
    snaps = [s for s in snaps if s]
    if not snaps:
        return {"total": 0, "subscribers": 0, "views": 0, "avg_eng": 0, "interns": 0}
    return {
        "total": len(channels),
        "subscribers": sum(s.subscribers for s in snaps),
        "views": sum(s.total_views for s in snaps),
        "avg_eng": round(sum(s.engagement_rate for s in snaps) / len(snaps), 2),
        "interns": User.query.filter_by(role="intern").count()
    }

def _build_chart_data(channels, dt_from, dt_to):
    labels, subs_data, views_data, eng_data = [], [], [], []
    for ch in channels:
        snap = Snapshot.query.filter_by(channel_id=ch.id)\
            .filter(Snapshot.fetched_at >= dt_from, Snapshot.fetched_at <= dt_to)\
            .order_by(Snapshot.fetched_at.desc()).first()
        if snap:
            labels.append(ch.channel_name[:20])
            subs_data.append(snap.subscribers)
            views_data.append(snap.total_views)
            eng_data.append(snap.engagement_rate)
    return {"labels": labels, "subscribers": subs_data, "views": views_data, "engagement": eng_data}

if __name__ == "__main__":
    app.run(debug=True)
