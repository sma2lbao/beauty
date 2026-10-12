from common.errors import AppError, NotFoundError


def test_to_dict_roundtrip() -> None:
    err = AppError("demo.failed", "出错了", details={"key": 1})
    assert err.to_dict() == {
        "code": "demo.failed",
        "message": "出错了",
        "details": {"key": 1},
    }


def test_subclass_defaults() -> None:
    err = NotFoundError("account.not_found", "用户不存在")
    assert err.status == 404
    assert err.details == {}
    assert str(err) == "用户不存在"
