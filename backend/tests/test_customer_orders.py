from app.services.customer_orders import eligible_total


def test_unknown_and_legacy_orders():
    assert eligible_total({}, ["manual"]) is None
    assert eligible_total({"a": {"realizations": ["123"]}}, []) is None
    assert (
        eligible_total({"a": {"realizations": ["123"], "orders": [{"number": "123"}]}}, []) is None
    )
    assert eligible_total({"a": {"realizations": []}}, []) == 0


def test_dedup_orders_returns_and_cancellations():
    def snapshot(cancelled=False, returned=False):
        return {
            "realizations": ["X"],
            "orders": [
                {
                    "number": "X",
                    "order_id": "same",
                    "cancelled": cancelled,
                    "fully_returned": returned,
                }
            ],
        }

    assert eligible_total({"a": snapshot(), "b": snapshot()}, ["X", "Y", "Y"]) == 2
    assert eligible_total({"a": snapshot(cancelled=True)}, ["X"]) == 0
    assert eligible_total({"b": snapshot(returned=True)}, ["X"]) == 0
