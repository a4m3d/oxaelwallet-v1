from transactions import state_machine as sm


def test_valid_transitions():
    assert sm.can_transition(sm.CREATED, sm.SIGNING)
    assert sm.can_transition(sm.SIGNING, sm.BROADCASTING)
    assert sm.can_transition(sm.BROADCASTING, sm.BROADCASTED)
    assert sm.can_transition(sm.BROADCASTED, sm.CONFIRMED)


def test_invalid_transitions():
    assert not sm.can_transition(sm.CONFIRMED, sm.SIGNING)
    assert not sm.can_transition(sm.CREATED, sm.CONFIRMED)
    assert not sm.can_transition(sm.FAILED, sm.BROADCASTED)


def test_terminal_states():
    for s in (sm.CONFIRMED, sm.FAILED, sm.CANCELLED, sm.EXPIRED):
        assert sm.is_terminal(s)
    assert not sm.is_terminal(sm.PENDING)


def test_idempotency_key_deterministic():
    from transactions.idempotency import make_key
    a = make_key(1, "w1", "ethereum", "ETH", "0xABC", "1.5", "nonce1")
    b = make_key(1, "w1", "ethereum", "ETH", "0xabc", "1.5", "nonce1")
    c = make_key(1, "w1", "ethereum", "ETH", "0xABC", "1.5", "nonce2")
    assert a == b       # case-insensitive address, same nonce => same key
    assert a != c       # different session nonce => distinct key
