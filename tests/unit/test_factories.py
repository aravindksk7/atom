from __future__ import annotations

from api.schemas import JobDefinition, RunSettings
from etl_framework.config.models import EnvironmentConfig
from etl_framework.reconciliation.models import MismatchRecord, ReconciliationResult
from tests.helpers.factories import (
    make_environment_config,
    make_job_definition,
    make_mismatch_record,
    make_reconciliation_result,
    make_run_settings,
    make_source_target_frames,
)


def test_factories_return_expected_types():
    assert isinstance(make_job_definition(), JobDefinition)
    assert isinstance(make_run_settings(), RunSettings)
    assert isinstance(make_mismatch_record(), MismatchRecord)
    assert isinstance(make_reconciliation_result(), ReconciliationResult)
    assert isinstance(make_environment_config(), EnvironmentConfig)


def test_source_target_frames_include_one_value_difference():
    source, target = make_source_target_frames()
    assert list(source.columns) == ["id", "amount"]
    assert list(target.columns) == ["id", "amount"]
    assert source.loc[1, "amount"] != target.loc[1, "amount"]
