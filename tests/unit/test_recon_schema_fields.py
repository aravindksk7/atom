from api.schemas import ReconFileCompareRequest


def test_file_names_accepted():
    req = ReconFileCompareRequest(
        stored_run_id="run-a",
        file_b_content_b64="abc",
        file_a_name="source.csv",
        file_b_name="target.csv",
    )
    assert req.file_a_name == "source.csv"
    assert req.file_b_name == "target.csv"


def test_key_columns_accepted():
    req = ReconFileCompareRequest(
        file_a_content_b64="abc",
        file_b_content_b64="xyz",
        file_a_name="a.csv",
        file_b_name="b.csv",
        key_columns=["id", "order_id"],
        exclude_columns=["created_at"],
    )
    assert req.key_columns == ["id", "order_id"]
    assert req.exclude_columns == ["created_at"]


def test_defaults():
    req = ReconFileCompareRequest(stored_run_id="x", stored_run_id_b="y")
    assert req.file_a_name is None
    assert req.file_b_name is None
    assert req.key_columns is None
    assert req.exclude_columns == []
