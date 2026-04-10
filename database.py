from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    full_name = db.Column(db.String(120), default="")
    avatar_color = db.Column(db.String(10), default="#ff0000")
    bio = db.Column(db.String(300), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    channels = db.relationship('Channel', backref='owner', lazy=True)
    saved_reels = db.relationship('SavedReel', backref='saved_by_user', lazy=True)

class Channel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    channel_id = db.Column(db.String(100), nullable=False)
    channel_name = db.Column(db.String(200), default="")
    channel_url = db.Column(db.String(300), default="")
    country = db.Column(db.String(50), default="")
    category = db.Column(db.String(80), default="")
    notes = db.Column(db.String(500), default="")
    added_at = db.Column(db.DateTime, default=datetime.utcnow)
    snapshots = db.relationship('Snapshot', backref='channel', lazy=True, cascade="all, delete-orphan")

class Snapshot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    channel_id = db.Column(db.Integer, db.ForeignKey('channel.id'), nullable=False)
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow)
    subscribers = db.Column(db.BigInteger, default=0)
    total_views = db.Column(db.BigInteger, default=0)
    video_count = db.Column(db.Integer, default=0)
    avg_views = db.Column(db.BigInteger, default=0)
    engagement_rate = db.Column(db.Float, default=0.0)
    description = db.Column(db.String(500), default="")
    thumbnail = db.Column(db.String(300), default="")
    top_videos = db.Column(db.Text, default="[]")

class SavedReel(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    saved_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    video_id = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(300), default="")
    channel_name = db.Column(db.String(200), default="")
    thumbnail = db.Column(db.String(300), default="")
    url = db.Column(db.String(300), default="")
    views = db.Column(db.BigInteger, default=0)
    likes = db.Column(db.BigInteger, default=0)
    comments = db.Column(db.BigInteger, default=0)
    published = db.Column(db.String(20), default="")
    reviewer_note = db.Column(db.String(500), default="")
    tag = db.Column(db.String(80), default="")
    saved_at = db.Column(db.DateTime, default=datetime.utcnow)

class InternNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    intern_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    note = db.Column(db.String(1000), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewer = db.relationship('User', foreign_keys=[reviewer_id])
    intern = db.relationship('User', foreign_keys=[intern_id])
