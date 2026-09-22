from __future__ import annotations

import pytest

from etl_framework.reconciliation.file_transfer_spec import (
    file_transfer_param_errors,
    parse_file_transfer_params,
)


def _params(**overrides):
    params = {
        "source": {"kind": "local", "root": "/data/in", "pattern": "SALES_*.csv"},
        "destination": {"kind": "local", "root": "/data/out"},
    }
    params.update(overrides)
    return params


def _fields(errors):
    return {field for field, _ in errors}


def test_valid_local_params_have_no_errors():
    assert file_transfer_param_errors(_params()) == []


def test_requires_source_and_destination_objects():
    errors = file_transfer_param_errors({})
    assert _fields(errors) == {"params.source", "params.destination"}


def test_requires_known_kinds():
    errors = file_transfer_param_errors(_params(
        source={"kind": "ftp", "root": "/a", "pattern": "*"},
        destination={"kind": "webdav", "root": "/b"},
    ))
    assert {"params.source.kind", "params.destination.kind"} <= _fields(errors)


def test_source_requires_root_and_pattern_destination_requires_root():
    errors = file_transfer_param_errors(_params(
        source={"kind": "local"},
        destination={"kind": "local"},
    ))
    assert {"params.source.root", "params.source.pattern", "params.destination.root"} <= _fields(errors)


@pytest.mark.parametrize("kind", ["s3", "sftp", "scp"])
def test_remote_kinds_require_credentials_ref_on_each_side(kind):
    errors = file_transfer_param_errors(_params(
        source={"kind": kind, "root": "x", "pattern": "*"},
        destination={"kind": kind, "root": "y"},
    ))
    assert {"params.source.credentials_ref", "params.destination.credentials_ref"} <= _fields(errors)


def test_on_exists_must_be_a_known_value():
    assert "params.on_exists" in _fields(file_transfer_param_errors(_params(on_exists="merge")))
    for value in ("fail", "overwrite", "skip"):
        assert file_transfer_param_errors(_params(on_exists=value)) == []


def test_flags_must_be_booleans():
    errors = file_transfer_param_errors(_params(recursive="yes", preserve_structure=1))
    assert {"params.recursive", "params.preserve_structure"} <= _fields(errors)


def test_preserve_structure_requires_recursive():
    errors = file_transfer_param_errors(_params(preserve_structure=True))
    assert "params.preserve_structure" in _fields(errors)
    assert file_transfer_param_errors(_params(preserve_structure=True, recursive=True)) == []


def test_parse_normalizes_scp_to_sftp_and_applies_defaults():
    spec = parse_file_transfer_params(_params(
        source={"kind": "scp", "root": "/in", "pattern": "*.csv", "credentials_ref": "vendor"},
        destination={"kind": "s3", "root": "s3://bkt/out", "credentials_ref": "aws"},
    ))
    assert spec.source.kind == "sftp"
    assert spec.source.credentials_ref == "vendor"
    assert spec.destination.kind == "s3"
    assert spec.destination.pattern == "*"
    assert (spec.on_exists, spec.recursive, spec.preserve_structure) == ("fail", False, False)


def test_parse_raises_value_error_with_first_message():
    with pytest.raises(ValueError, match="'source' object"):
        parse_file_transfer_params({})


@pytest.mark.parametrize("field", ["root", "pattern", "credentials_ref"])
@pytest.mark.parametrize("bad_value", [5, ["/a"], {"path": "/a"}])
def test_source_location_fields_must_be_strings(field, bad_value):
    source = {"kind": "sftp", "root": "/in", "pattern": "*.csv", "credentials_ref": "vendor"}
    source[field] = bad_value
    errors = file_transfer_param_errors(_params(
        source=source,
        destination={"kind": "s3", "root": "s3://bkt/out", "credentials_ref": "aws"},
    ))
    assert f"params.source.{field}" in _fields(errors)


def test_destination_root_must_be_a_string():
    errors = file_transfer_param_errors(_params(destination={"kind": "local", "root": 5}))
    assert "params.destination.root" in _fields(errors)


def test_destination_pattern_must_be_a_string_when_present():
    errors = file_transfer_param_errors(_params(destination={"kind": "local", "root": "/out", "pattern": 123}))
    assert "params.destination.pattern" in _fields(errors)


def test_invalid_regex_token_pattern_is_reported_on_source_pattern():
    errors = file_transfer_param_errors(_params(
        source={"kind": "local", "root": "/in", "pattern": "{a:regex(}"},
    ))
    assert "params.source.pattern" in _fields(errors)


def test_parse_normalizes_scp_destination_to_sftp():
    spec = parse_file_transfer_params(_params(
        destination={"kind": "scp", "root": "/out", "credentials_ref": "vendor"},
    ))
    assert spec.destination.kind == "sftp"
    assert spec.destination.credentials_ref == "vendor"


def test_smb_source_and_destination_are_valid_with_credentials_ref():
    params = {
        "source": {"kind": "smb", "root": r"\\fileserver01\vendor\inbound", "pattern": "sales_{region}.csv", "credentials_ref": "vendor-share"},
        "destination": {"kind": "smb", "root": r"\\fileserver01\staging\sales", "credentials_ref": "vendor-share"},
    }
    assert file_transfer_param_errors(params) == []
    spec = parse_file_transfer_params(params)
    assert spec.source.kind == "smb"
    assert spec.destination.kind == "smb"


def test_smb_requires_credentials_ref():
    params = {
        "source": {"kind": "smb", "root": r"\\fileserver01\vendor\inbound", "pattern": "*.csv"},
        "destination": {"kind": "local", "root": "/tmp/out"},
    }
    errors = file_transfer_param_errors(params)
    assert any(field == "params.source.credentials_ref" for field, _ in errors)
