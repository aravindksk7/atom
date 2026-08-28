"""A run's identity is a bare uuid4; these pin the derived human-readable label.

The label is computed once server-side and shipped on the run DTO, so the live UI
and the downloadable HTML report show the same string for the same run.
"""
from datetime import datetime, timezone
import types

from api.services.run_label import run_display_label, run_display_label_for, short_run_id

RUN_ID = "00a638ef-5743-401c-9324-182b9427c914"


class TestRunDisplayLabel:
    def test_uses_the_specific_compare_kind_not_the_generic_run_type(self):
        # run_type is "reconciliation" for every compare -- the kind that actually
        # distinguishes them lives in config_snapshot.
        label = run_display_label(
            run_id=RUN_ID,
            run_type="reconciliation",
            source_env="dev",
            target_env="prod",
            config_snapshot={"compare_request_type": "recon_file"},
        )
        assert label == "file compare · dev → prod · 00a638ef"

    def test_falls_back_to_run_type_when_the_snapshot_says_nothing(self):
        assert run_display_label(
            run_id=RUN_ID, run_type="reconciliation", source_env="dev", target_env="prod",
        ) == "recon · dev → prod · 00a638ef"

    def test_unknown_request_type_does_not_win_over_run_type(self):
        assert run_display_label(
            run_id=RUN_ID,
            run_type="test_suite",
            config_snapshot={"compare_request_type": "unknown"},
        ) == "test suite · 00a638ef"

    def test_unmapped_request_type_is_still_preferred_and_readable(self):
        assert run_display_label(
            run_id=RUN_ID,
            run_type="reconciliation",
            config_snapshot={"compare_request_type": "brand_new_kind"},
        ) == "brand new kind · 00a638ef"

    def test_missing_environments_are_omitted_rather_than_rendered_as_none(self):
        label = run_display_label(run_id=RUN_ID, run_type="test_suite")
        assert label == "test suite · 00a638ef"
        assert "None" not in label

    def test_one_sided_environment_still_shows(self):
        assert run_display_label(
            run_id=RUN_ID, run_type="reconciliation", source_env="dev",
        ) == "recon · dev · 00a638ef"

    def test_survives_a_run_with_nothing_set(self):
        assert run_display_label(run_id=None) == "run"

    def test_reads_off_a_run_object(self):
        run = types.SimpleNamespace(
            run_id=RUN_ID,
            run_type="reconciliation",
            source_env="dev",
            target_env="prod",
            config_snapshot={"compare_request_type": "sql_compare"},
        )
        assert run_display_label_for(run) == "SQL compare · dev → prod · 00a638ef"

    def test_short_id_is_the_first_uuid_segment(self):
        assert short_run_id(RUN_ID) == "00a638ef"
        assert short_run_id(None) == ""


def test_snapshot_exposes_the_label_for_the_report_template():
    from api.services.run_report import build_run_report_snapshot

    run = types.SimpleNamespace(
        run_id=RUN_ID,
        status="FAILED",
        started_at=None,
        completed_at=None,
        source_env="dev",
        target_env="prod",
        config_snapshot={"compare_request_type": "bo_compare"},
        run_type="reconciliation",
        pair_id=None,
        results=[],
        total_tests=0, passed=0, failed=0, slow=0, error=0,
    )
    snapshot = build_run_report_snapshot(run)

    assert snapshot.run_label == "BO compare · dev → prod · 00a638ef"
    assert snapshot.short_run_id == "00a638ef"


class TestReportNameBase:
    STARTED = datetime(2026, 8, 28, 14, 30, 5, tzinfo=timezone.utc)

    def test_uses_config_name_when_present(self):
        from api.services.run_label import report_name_base

        name = report_name_base(
            started_at=self.STARTED,
            source_env="dev",
            target_env="prod",
            config_snapshot={"config_name": "Nightly Recon"},
        )
        assert name == "nightly_recon_2026-08-28_14-30-05"

    def test_falls_back_to_env_pair_when_no_config_name(self):
        from api.services.run_label import report_name_base

        name = report_name_base(started_at=self.STARTED, source_env="dev", target_env="prod")
        assert name == "dev_to_prod_2026-08-28_14-30-05"

    def test_one_sided_environment_still_shows(self):
        from api.services.run_label import report_name_base

        name = report_name_base(started_at=self.STARTED, source_env="dev")
        assert name == "dev_2026-08-28_14-30-05"

    def test_falls_back_to_run_when_nothing_identifies_it(self):
        from api.services.run_label import report_name_base

        assert report_name_base(started_at=self.STARTED) == "run_2026-08-28_14-30-05"

    def test_missing_started_at_still_produces_a_name(self):
        from api.services.run_label import report_name_base

        name = report_name_base(started_at=None, source_env="dev", target_env="prod")
        assert name == "dev_to_prod_unscheduled"

    def test_sanitizes_special_characters_in_config_name(self):
        from api.services.run_label import report_name_base

        name = report_name_base(
            started_at=self.STARTED,
            config_snapshot={"config_name": "Q3 Recon / Sales!!"},
        )
        assert name == "q3_recon_sales_2026-08-28_14-30-05"

    def test_reads_off_a_run_object(self):
        import types
        from api.services.run_label import report_name_base_for

        run = types.SimpleNamespace(
            started_at=self.STARTED,
            source_env="dev",
            target_env="prod",
            config_snapshot={"config_name": "Nightly Recon"},
        )
        assert report_name_base_for(run) == "nightly_recon_2026-08-28_14-30-05"

    def test_caps_the_length_of_a_very_long_config_name(self):
        from api.services.run_label import report_name_base

        name = report_name_base(
            started_at=self.STARTED,
            config_snapshot={"config_name": "x" * 300},
        )
        stem = name.rsplit("_", 6)[0]  # strip the "_YYYY-MM-DD_HH-MM-SS" timestamp suffix
        assert len(stem) <= 80


def test_snapshot_exposes_the_report_name_for_downloads():
    from api.services.run_report import build_run_report_snapshot

    run = types.SimpleNamespace(
        run_id=RUN_ID,
        status="FAILED",
        started_at=datetime(2026, 8, 28, 14, 30, 5, tzinfo=timezone.utc),
        completed_at=None,
        source_env="dev",
        target_env="prod",
        config_snapshot={"config_name": "Nightly Recon"},
        run_type="reconciliation",
        pair_id=None,
        results=[],
        total_tests=0, passed=0, failed=0, slow=0, error=0,
    )
    snapshot = build_run_report_snapshot(run)

    assert snapshot.report_name == "nightly_recon_2026-08-28_14-30-05"
