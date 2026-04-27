from regstack.auth.password import PasswordHasher


def test_hash_then_verify_round_trip() -> None:
    hasher = PasswordHasher()
    h = hasher.hash("hunter2hunter2")
    assert h != "hunter2hunter2"
    assert hasher.verify("hunter2hunter2", h)
    assert not hasher.verify("wrong-password", h)


def test_each_hash_is_unique() -> None:
    hasher = PasswordHasher()
    a = hasher.hash("same-input")
    b = hasher.hash("same-input")
    assert a != b
