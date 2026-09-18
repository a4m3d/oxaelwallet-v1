from assets import catalog
from assets.capabilities import Family


def test_registry_has_full_and_routed():
    eth = catalog.get_network("ethereum")
    assert eth.capabilities.direct_send_supported is True
    assert eth.capabilities.balance_supported is True
    btc = catalog.get_network("bitcoin")
    assert btc.capabilities.direct_send_supported is False
    assert btc.capabilities.near_intents_supported is True


def test_networks_with_filter():
    sendable = catalog.networks_with("direct_send_supported")
    keys = {n.key for n in sendable}
    assert "ethereum" in keys and "solana" in keys
    assert "bitcoin" not in keys


def test_all_evm_networks_present():
    evm = {n.key for n in catalog.evm_networks()}
    for k in ["ethereum", "base", "arbitrum", "optimism", "polygon", "bnb", "avalanche"]:
        assert k in evm


def test_explorer_links_no_fabrication():
    eth = catalog.get_network("ethereum")
    assert eth.explorer_tx("0xabc") == "https://etherscan.io/tx/0xabc"
    # network without explorer returns None rather than a fake url
    from assets.capabilities import Network, Capabilities
    ghost = Network("ghost", "Ghost", Family.OTHER, "GHO", explorer=None, capabilities=Capabilities())
    assert ghost.explorer_tx("0xabc") is None


def test_no_fixed_network_count_hardcoded():
    # The catalog is a mapping, not a hardcoded "35 networks" claim.
    assert isinstance(catalog.NETWORKS, dict)
    assert len(catalog.NETWORKS) >= 8
