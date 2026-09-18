from chains.evm import evm_adapter
from chains.solana import solana_adapter
from wallets.import_service import classify_secret


def test_evm_generate_and_roundtrip_import():
    keys = evm_adapter.generate()
    assert keys.address.startswith("0x") and len(keys.address) == 42
    reimported = evm_adapter.from_secret(keys.secret)
    assert reimported.address == keys.address


def test_solana_generate_and_roundtrip_import():
    keys = solana_adapter.generate()
    reimported = solana_adapter.from_secret(keys.secret)
    assert reimported.address == keys.address


def test_evm_address_validation():
    keys = evm_adapter.generate()
    assert evm_adapter.validate_address(keys.address)
    assert not evm_adapter.validate_address("0x123")
    assert not evm_adapter.validate_address("not-an-address")


def test_solana_address_validation():
    keys = solana_adapter.generate()
    assert solana_adapter.validate_address(keys.address)
    assert not solana_adapter.validate_address("0x0000000000000000000000000000000000000000")


def test_wrong_network_addresses_rejected():
    evm = evm_adapter.generate()
    sol = solana_adapter.generate()
    # EVM address must not validate as Solana and vice versa
    assert not solana_adapter.validate_address(evm.address)
    assert not evm_adapter.validate_address(sol.address)


def test_invalid_secret_raises():
    import pytest
    with pytest.raises(ValueError):
        evm_adapter.from_secret("clearly not a key")
    with pytest.raises(ValueError):
        solana_adapter.from_secret("clearly not a key")


def test_classify_secret_labels():
    assert classify_secret("0x" + "ab" * 32) == "evm_private_key"
    assert classify_secret(" ".join(["word"] * 12)) == "mnemonic"
    assert classify_secret("garbage") == "unknown"
