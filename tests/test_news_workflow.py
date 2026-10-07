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
    assert 'if: hashFiles(\'state/state.json\') == \'\'' in workflow
