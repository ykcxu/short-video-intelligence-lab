from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, utcnow


class HomepageTarget(Base, TimestampMixin):
    __tablename__ = "homepage_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), default="douyin", nullable=False)
    homepage_url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category_lv1: Mapped[str | None] = mapped_column(String(128), nullable=True)
    category_lv2: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tags_json: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    crawl_jobs: Mapped[list[CrawlJob]] = relationship(back_populates="target")
    videos: Mapped[list[Video]] = relationship(back_populates="target")


class CrawlSession(Base, TimestampMixin):
    __tablename__ = "crawl_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    state_file_path: Mapped[str] = mapped_column(Text, nullable=False)
    cookie_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    login_status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    last_manual_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_validation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)

    crawl_jobs: Mapped[list[CrawlJob]] = relationship(back_populates="session")


class CrawlJob(Base, TimestampMixin):
    __tablename__ = "crawl_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("homepage_targets.id"), nullable=False, index=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("crawl_sessions.id"), nullable=True, index=True)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), default="incremental", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    priority: Mapped[int] = mapped_column(default=0, nullable=False)
    attempt_count: Mapped[int] = mapped_column(default=0, nullable=False)
    needs_manual_help: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    target: Mapped[HomepageTarget] = relationship(back_populates="crawl_jobs")
    session: Mapped[CrawlSession | None] = relationship(back_populates="crawl_jobs")

    __table_args__ = (
        Index("ix_crawl_jobs_target_status", "target_id", "status"),
        Index("ix_crawl_jobs_session_status", "session_id", "status"),
    )


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), default="douyin", nullable=False)
    target_id: Mapped[int] = mapped_column(ForeignKey("homepage_targets.id"), nullable=False, index=True)
    video_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    video_url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cover_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_json_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )

    target: Mapped[HomepageTarget] = relationship(back_populates="videos")
    snapshots: Mapped[list[VideoSnapshot]] = relationship(
        back_populates="video",
        cascade="all, delete-orphan",
    )
    comments: Mapped[list[Comment]] = relationship(
        back_populates="video",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_videos_target_video_id", "target_id", "video_id"),
    )


class VideoSnapshot(Base):
    __tablename__ = "video_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id_fk: Mapped[int] = mapped_column(ForeignKey("videos.id"), nullable=False, index=True)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    view_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    like_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    comment_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    share_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    bookmark_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    capture_source: Mapped[str] = mapped_column(String(64), default="browser", nullable=False)
    is_estimated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    raw_json_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    video: Mapped[Video] = relationship(back_populates="snapshots")

    __table_args__ = (
        Index("ix_video_snapshots_video_snapshot_at", "video_id_fk", "snapshot_at"),
    )


class Comment(Base, TimestampMixin):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    video_id_fk: Mapped[int] = mapped_column(ForeignKey("videos.id"), nullable=False, index=True)
    comment_platform_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    nickname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    like_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reply_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    comment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_author: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )
    raw_json_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    unique_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    video: Mapped[Video] = relationship(back_populates="comments")
    replies: Mapped[list[CommentReply]] = relationship(
        back_populates="comment",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_comments_video_comment_platform_id", "video_id_fk", "comment_platform_id"),
        Index("ix_comments_video_unique_hash", "video_id_fk", "unique_hash"),
    )


class CommentReply(Base, TimestampMixin):
    __tablename__ = "comment_replies"

    id: Mapped[int] = mapped_column(primary_key=True)
    comment_id_fk: Mapped[int] = mapped_column(ForeignKey("comments.id"), nullable=False, index=True)
    reply_platform_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    nickname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    like_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )
    raw_json_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    unique_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    comment: Mapped[Comment] = relationship(back_populates="replies")

    __table_args__ = (
        Index("ix_comment_replies_comment_reply_platform_id", "comment_id_fk", "reply_platform_id"),
        Index("ix_comment_replies_comment_unique_hash", "comment_id_fk", "unique_hash"),
    )
