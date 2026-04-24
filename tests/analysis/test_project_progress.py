from pathlib import Path

from short_video_intel.analysis.reporting import build_project_progress_dashboard


def test_build_project_progress_dashboard(tmp_path: Path) -> None:
    workspace = tmp_path
    (workspace / "inputs").mkdir(parents=True, exist_ok=True)
    seed = workspace / "inputs" / "douyin_homepages_seed.tsv"
    seed.write_text(
        "\n".join(
            [
                "平台\t分类\t部门\t账号类型\t账号名\t账号uid\t直播间链接\t主页链接",
                "抖音\t内部\t小语\tIP号\t账号A\t1\t\thttps://www.douyin.com/user/a",
                "抖音\t内部\t小语\tIP号\t账号B\t2\t\thttps://www.douyin.com/user/b",
            ]
        ),
        encoding="utf-8",
    )

    download_root = workspace / "downloads" / "artifact"
    (download_root / "账号A").mkdir(parents=True, exist_ok=True)
    (download_root / "账号B").mkdir(parents=True, exist_ok=True)
    (download_root / "账号A" / "账号A_111.mp4").write_bytes(b"0")
    (download_root / "账号A" / "账号A_222.mp4").write_bytes(b"0")
    (download_root / "账号B" / "账号B_333.mp4").write_bytes(b"0")

    video_dir = workspace / "artifacts" / "collector" / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    (video_dir / "video_detail_x.json").write_text(
        '{"video_url":"https://www.douyin.com/video/111","metrics":{"view_count":1,"like_count":2,"comment_count":3,"share_count":4}}',
        encoding="utf-8",
    )

    comments_dir = workspace / "artifacts" / "collector" / "comments"
    comments_dir.mkdir(parents=True, exist_ok=True)
    (comments_dir / "video_comments_x.json").write_text(
        '{"video_url":"https://www.douyin.com/video/111","comments":[{"content":"ok"}],"replies":[],"scan_meta":{"stop_reason":"body_text_comments_captured"}}',
        encoding="utf-8",
    )

    result = build_project_progress_dashboard(
        workspace=workspace,
        artifacts_dir=workspace / "artifacts",
        download_target_per_account=2,
    )

    assert result["ok"] is True
    assert result["progress"]["download_goal_completed_accounts"] == 1
    assert result["progress"]["download_goal_downloaded_videos"] == 3
    assert result["progress"]["detail_covered_videos"] == 1
    assert result["progress"]["comment_videos_with_nonempty_comments"] == 1
    assert "账号A" in result["markdown"]


def test_build_project_progress_dashboard_matches_question_mark_account_name(tmp_path: Path) -> None:
    workspace = tmp_path
    (workspace / "inputs").mkdir(parents=True, exist_ok=True)
    seed = workspace / "inputs" / "douyin_homepages_seed.tsv"
    seed.write_text(
        "\n".join(
            [
                "平台\t分类\t部门\t账号类型\t账号名\t账号uid\t直播间链接\t主页链接",
                "抖音\t内部\t小英\tIP号\t希望学小学英语@航航老师????\t1\t\thttps://www.douyin.com/user/a",
            ]
        ),
        encoding="utf-8",
    )

    download_dir = workspace / "downloads" / "artifact" / "希望学小学英语@航航老师"
    download_dir.mkdir(parents=True, exist_ok=True)
    (download_dir / "希望学小学英语@航航老师_111.mp4").write_bytes(b"0")

    result = build_project_progress_dashboard(
        workspace=workspace,
        artifacts_dir=workspace / "artifacts",
        download_target_per_account=1,
    )

    assert result["ok"] is True
    assert result["accounts"][0]["downloaded"] == 1
    assert result["progress"]["download_goal_completed_accounts"] == 1
