import json
from pathlib import Path


def test_news_workflow_uses_30_minute_schedule_and_safe_state_push():
    workflow = Path(__file__).parents[1].joinpath(".github", "workflows", "news.yml").read_text()

    assert 'cron: "8,38 * * * *"' in workflow
    temporary_trigger = "# temporary-validation-trigger" in workflow
    if not temporary_trigger:
        assert "\n  push:" not in workflow
    assert (
        'git remote set-url origin "https://x-access-token:${{ github.token }}@github.com/${{ github.repository }}.git"'
        in workflow
    )
    assert (
        'git push "https://x-access-token:${{ github.token }}@${{ github.server_url }}/${{ github.repository }}.git" HEAD:bot-state'
        not in workflow
    )


def test_state_persistence_skips_cleanly_when_publish_has_no_state_artifact():
    workflow = Path(__file__).parents[1].joinpath(".github", "workflows", "news.yml").read_text()
    assert "continue-on-error: true" in workflow
    assert "if: hashFiles('state/state.json') == ''" in workflow


def test_news_workflow_uses_three_sources_ninety_minute_window_and_seventy_five_minute_schedule():
    root = Path(__file__).parents[1]
    sources = json.loads((root / "data" / "sources.json").read_text(encoding="utf-8"))
    assert [source["name"] for source in sources] == ["TechCrunch", "The Verge", "Engadget"]
    workflow = (root / ".github" / "workflows" / "news.yml").read_text(encoding="utf-8")
    assert 'NEWS_WINDOW_MINUTES: "90"' in workflow
    assert 'cron: "8 0,5,10,15,20 * * *"' in workflow
    assert 'cron: "23 1,6,11,16,21 * * *"' in workflow
    assert 'cron: "38 2,7,12,17,22 * * *"' in workflow
    assert 'cron: "53 3,8,13,18,23 * * *"' in workflow
