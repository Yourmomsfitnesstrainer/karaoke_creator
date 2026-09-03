from karaoke_generator.pipeline import _stage


def test_pipeline_stage_reports_start_and_finish_percentages() -> None:
    events: list[tuple[int, str]] = []

    with _stage(3, 5, "Aligning exact lyrics", lambda progress, label: events.append((progress, label))):
        pass

    assert events == [(40, "Aligning exact lyrics"), (60, "Aligning exact lyrics")]
