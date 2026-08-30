from api.services.contract_tester import CheckResult, ContractTestReport


def test_check_result_model():
    check = CheckResult(
        id="schema_001",
        category="schema",
        name="Column type validation",
        status="PASS",
        target="order_id",
        expected="VARCHAR",
        actual="VARCHAR",
        message="Column type matches expected"
    )
    assert check.status == "PASS"
    assert check.category == "schema"
