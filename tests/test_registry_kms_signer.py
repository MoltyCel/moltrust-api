"""ERC-8004 identity writes are signed by a KMS key, never by BASE_WRITE_KEY.

Ops job S5. `app/chain_keys.py registry_write_signer` returns a signer whose
private key stays in AWS KMS. KMS is replaced here by a stand-in that holds a
throwaway secp256k1 key generated per test and answers like the real service:
GetPublicKey returns a DER SubjectPublicKeyInfo, Sign returns a DER ECDSA
signature over the digest as given, with s in either half of the group order.
Each test that matters runs with both halves.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import boto3
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    Prehashed,
    decode_dss_signature,
    encode_dss_signature,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from eth_account import Account
from eth_account.typed_transactions import TypedTransaction
from hexbytes import HexBytes

from app import chain_keys
from app.chain_keys import SECP256K1_N, ChainKeyNotConfigured, KmsSigner

# Publicly known throwaway key (Hardhat account 0). Set as BASE_WRITE_KEY to
# prove the identity write path does not pick it up.
HARDHAT_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
HARDHAT_ADDR = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"

KMS_ENV = {
    "SIGNER_AWS_ACCESS_KEY_ID": "test-access-key-id",
    "SIGNER_AWS_SECRET_ACCESS_KEY": "test-secret",
    "SIGNER_AWS_REGION": "eu-central-1",
    "SIGNER_KMS_KEY_ID": "test-key-id",
}


class FakeKms:
    """KMS GetPublicKey/Sign over a local key, with the s half chosen by the test."""

    def __init__(self, curve=None, high_s: bool | None = None):
        self.key = ec.generate_private_key(curve or ec.SECP256K1())
        self.high_s = high_s  # True: always the upper half, False: lower, None: as produced
        self.sign_calls = 0
        self.seen_s_high = []

    @property
    def eth_address(self) -> str:
        secret = self.key.private_numbers().private_value.to_bytes(32, "big")
        return Account.from_key(secret).address

    def get_public_key(self, KeyId):
        der = self.key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        return {"KeyId": KeyId, "PublicKey": der, "KeySpec": "ECC_SECG_P256K1"}

    def sign(self, KeyId, Message, MessageType, SigningAlgorithm):
        assert MessageType == "DIGEST" and SigningAlgorithm == "ECDSA_SHA_256"
        assert len(Message) == 32
        self.sign_calls += 1
        der = self.key.sign(Message, ec.ECDSA(Prehashed(hashes.SHA256())))
        r, s = decode_dss_signature(der)
        if self.high_s is True and s <= SECP256K1_N // 2 or self.high_s is False and s > SECP256K1_N // 2:
            s = SECP256K1_N - s
        self.seen_s_high.append(s > SECP256K1_N // 2)
        return {"KeyId": KeyId, "Signature": encode_dss_signature(r, s),
                "SigningAlgorithm": SigningAlgorithm}


@pytest.fixture
def kms_env(monkeypatch):
    for name, value in KMS_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("SIGNER_ADDRESS", raising=False)
    monkeypatch.setenv("BASE_WRITE_KEY", HARDHAT_KEY)


def _install(monkeypatch, fake):
    calls = []

    def client(service, **kwargs):
        calls.append((service, kwargs))
        return fake

    monkeypatch.setattr(boto3, "client", client)
    return calls


def _tx(address, nonce=7):
    return {
        "from": address,
        "to": "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",
        "data": "0x" + "f2c298be" + "00" * 64,
        "value": 0,
        "nonce": nonce,
        "chainId": 8453,
        "gas": 300000,
        "maxFeePerGas": 3_000_000_000,
        "maxPriorityFeePerGas": 1_000_000,
    }


# ---------------------------------------------------------------------------
# the accessor
# ---------------------------------------------------------------------------
def test_address_is_derived_from_the_kms_public_key(kms_env, monkeypatch):
    fake = FakeKms()
    calls = _install(monkeypatch, fake)
    signer = chain_keys.registry_write_signer()
    assert signer.address == fake.eth_address
    assert signer.address != HARDHAT_ADDR
    (service, kwargs), = calls
    assert service == "kms"
    assert kwargs["aws_access_key_id"] == KMS_ENV["SIGNER_AWS_ACCESS_KEY_ID"]
    assert kwargs["aws_secret_access_key"] == KMS_ENV["SIGNER_AWS_SECRET_ACCESS_KEY"]
    assert kwargs["region_name"] == KMS_ENV["SIGNER_AWS_REGION"]


def test_the_variable_list_is_the_one_the_accessor_reads():
    assert set(chain_keys.KMS_SIGNER_VARIABLES) == set(KMS_ENV)
    src = inspect.getsource(chain_keys.registry_write_signer)
    for name in chain_keys.KMS_SIGNER_VARIABLES:
        assert f'os.getenv("{name}"' in src, name


@pytest.mark.parametrize("name", sorted(KMS_ENV))
def test_a_missing_variable_raises_and_does_not_fall_back(kms_env, monkeypatch, name):
    """BASE_WRITE_KEY is set and usable; the accessor still refuses."""
    calls = _install(monkeypatch, FakeKms())
    monkeypatch.delenv(name)
    with pytest.raises(ChainKeyNotConfigured, match=name):
        chain_keys.registry_write_signer()
    assert calls == [], "no KMS client may be built without its full configuration"


def test_a_declared_address_must_match(kms_env, monkeypatch):
    fake = FakeKms()
    _install(monkeypatch, fake)
    monkeypatch.setenv("SIGNER_ADDRESS", fake.eth_address.lower())
    assert chain_keys.registry_write_signer().address == fake.eth_address
    monkeypatch.setenv("SIGNER_ADDRESS", HARDHAT_ADDR)
    with pytest.raises(ChainKeyNotConfigured, match="SIGNER_ADDRESS"):
        chain_keys.registry_write_signer()


def test_a_key_on_another_curve_is_refused(kms_env, monkeypatch):
    _install(monkeypatch, FakeKms(curve=ec.SECP256R1()))
    with pytest.raises(ChainKeyNotConfigured, match="secp256k1"):
        chain_keys.registry_write_signer()


def test_a_kms_error_becomes_not_configured(kms_env, monkeypatch):
    class Refusing(FakeKms):
        def get_public_key(self, KeyId):
            raise PermissionError("AccessDenied")

    _install(monkeypatch, Refusing())
    with pytest.raises(ChainKeyNotConfigured, match="PermissionError"):
        chain_keys.registry_write_signer()


# ---------------------------------------------------------------------------
# signatures
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("high_s", [True, False])
def test_digest_signature_is_low_s_and_recovers(high_s):
    from eth_keys import keys
    fake = FakeKms(high_s=high_s)
    signer = KmsSigner(fake, "k")
    digest = bytes(range(32))
    v, r, s = signer.sign_digest(digest)
    assert fake.seen_s_high == [high_s], "the stand-in must have produced the half under test"
    assert 0 < s <= SECP256K1_N // 2
    assert v in (0, 1)
    pk = keys.Signature(vrs=(v, r, s)).recover_public_key_from_msg_hash(digest)
    assert pk.to_checksum_address() == fake.eth_address


@pytest.mark.parametrize("high_s", [True, False])
def test_eip1559_raw_transaction_recovers_to_the_signer(high_s):
    fake = FakeKms(high_s=high_s)
    signer = KmsSigner(fake, "k")
    raw = signer.sign_transaction(_tx(signer.address))
    assert raw[0] == 2, "EIP-1559 envelope"
    assert Account.recover_transaction(raw) == fake.eth_address
    decoded = TypedTransaction.from_bytes(HexBytes(raw)).as_dict()
    assert decoded["s"] <= SECP256K1_N // 2
    assert decoded["nonce"] == 7 and decoded["chainId"] == 8453


def test_kms_signature_matches_a_local_signature_of_the_same_transaction():
    """Same key, same unsigned transaction: same transaction hash as eth_account."""
    fake = FakeKms()
    signer = KmsSigner(fake, "k")
    tx = _tx(signer.address)
    raw = signer.sign_transaction(tx)
    secret = fake.key.private_numbers().private_value.to_bytes(32, "big")
    local = dict(tx)
    local.pop("from")
    local["type"] = 2
    expected = Account.sign_transaction(local, secret)
    # ECDSA here is randomised and RFC 6979 in eth_account, so compare what
    # must be equal: the signed payload and the sender, not the signature bytes.
    ours = TypedTransaction.from_bytes(HexBytes(raw)).as_dict()
    theirs = TypedTransaction.from_bytes(HexBytes(expected.raw_transaction)).as_dict()
    for field in ("nonce", "to", "data", "value", "gas", "maxFeePerGas",
                  "maxPriorityFeePerGas", "chainId"):
        assert ours[field] == theirs[field], field
    assert Account.recover_transaction(expected.raw_transaction) == Account.recover_transaction(raw)


def test_a_transaction_from_another_address_is_refused():
    signer = KmsSigner(FakeKms(), "k")
    with pytest.raises(ValueError, match="signer is"):
        signer.sign_transaction(_tx(HARDHAT_ADDR))


def test_a_signature_by_another_key_is_refused():
    """A key id that names a different key than the one the address came from."""
    fake = FakeKms()
    signer = KmsSigner(fake, "k")
    fake.key = ec.generate_private_key(ec.SECP256K1())
    with pytest.raises(ChainKeyNotConfigured, match="does not recover"):
        signer.sign_digest(bytes(32))


# ---------------------------------------------------------------------------
# the identity write path
# ---------------------------------------------------------------------------
class _Eth:
    def __init__(self):
        self.nonce_for = []
        self.sent = []
        self.gas_price = 1_000_000_000

    def get_transaction_count(self, address, block):
        self.nonce_for.append((address, block))
        return 11

    def send_raw_transaction(self, raw):
        self.sent.append(bytes(raw))
        return HexBytes(b"\x11" * 32)

    def wait_for_transaction_receipt(self, tx_hash, timeout):
        from app.erc8004 import IDENTITY_REGISTRY

        class Log:
            address = IDENTITY_REGISTRY
            topics = [HexBytes(b"\x00" * 32)] * 3 + [HexBytes((95999).to_bytes(32, "big"))]

        class Receipt:
            status = 1
            logs = [Log()]

        return Receipt()


class _W3:
    def __init__(self):
        self.eth = _Eth()

    @staticmethod
    def to_wei(value, unit):
        assert unit == "gwei"
        return int(value * 10**9)

    @staticmethod
    def to_hex(value):
        return "0x" + bytes(value).hex()


class _Contract:
    class functions:
        @staticmethod
        def register(uri):
            class Call:
                @staticmethod
                def build_transaction(params):
                    return {**params, "to": "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",
                            "data": "0xf2c298be" + uri.encode().hex(), "value": 0}
            return Call()


def _wire(monkeypatch):
    from app import erc8004
    w3 = _W3()
    monkeypatch.setattr(erc8004, "_get_w3", lambda: w3)
    monkeypatch.setattr(erc8004, "_get_identity_write_contract", lambda: _Contract())
    # Whatever the module-level giveFeedback key holds, register must not use it.
    monkeypatch.setattr(erc8004, "_WRITE_KEY", HARDHAT_KEY)
    monkeypatch.setattr(erc8004, "_WRITE_ADDR", HARDHAT_ADDR)
    return erc8004, w3


@pytest.mark.parametrize("high_s", [True, False])
def test_register_is_signed_by_the_kms_signer(kms_env, monkeypatch, high_s):
    fake = FakeKms(high_s=high_s)
    _install(monkeypatch, fake)
    erc8004, w3 = _wire(monkeypatch)
    out = erc8004.register_onchain_agent("did:moltrust:0123456789abcdef")
    assert out.get("agent_id") == 95999, out
    assert w3.eth.nonce_for == [(fake.eth_address, "pending")]
    (raw,) = w3.eth.sent
    assert Account.recover_transaction(raw) == fake.eth_address
    assert fake.sign_calls == 1


def test_register_without_kms_configuration_sends_nothing(kms_env, monkeypatch):
    """BASE_WRITE_KEY is set; without SIGNER_* the registration does not happen."""
    _install(monkeypatch, FakeKms())
    monkeypatch.delenv("SIGNER_KMS_KEY_ID")
    erc8004, w3 = _wire(monkeypatch)
    out = erc8004.register_onchain_agent("did:moltrust:0123456789abcdef")
    assert "SIGNER_KMS_KEY_ID" in out.get("error", ""), out
    assert w3.eth.sent == [] and w3.eth.nonce_for == []


def test_register_does_not_reference_the_raw_write_key():
    """Static: the identity write path names neither the variable nor its module copy."""
    from app import erc8004
    tree = ast.parse(textwrap.dedent(inspect.getsource(erc8004.register_onchain_agent)))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert not names & {"_WRITE_KEY", "_WRITE_ADDR"}, names & {"_WRITE_KEY", "_WRITE_ADDR"}
    assert "BASE_WRITE_KEY" not in strings
    assert "registry_write_signer" in {a.name for n in ast.walk(tree)
                                       if isinstance(n, ast.ImportFrom) for a in n.names}


def test_the_accessor_reads_no_base_write_key():
    """Static: registry_write_signer and KmsSigner never read BASE_WRITE_KEY."""
    for obj in (chain_keys.registry_write_signer, chain_keys.KmsSigner):
        assert "BASE_WRITE_KEY" not in inspect.getsource(obj).split('"""', 2)[-1], obj


# ---------------------------------------------------------------------------
# the daily live check (c-schluessel-nach-zweck) for a KMS purpose
# ---------------------------------------------------------------------------
def _live_config(address):
    return {"zwecke": {"registry-write": {
        "variablen": sorted(KMS_ENV), "signierer": "kms", "adresse": address,
        "zugang": "app/chain_keys.py:registry_write_signer"}}}


def test_live_check_derives_the_kms_address(kms_env, monkeypatch, capsys):
    from tests.test_key_purposes import checker
    fake = FakeKms()
    _install(monkeypatch, fake)
    assert checker.live_findings(_live_config(fake.eth_address)) == []
    assert f"kms -> {fake.eth_address}" in capsys.readouterr().out
    out = checker.live_findings(_live_config(HARDHAT_ADDR))
    assert len(out) == 1 and "the list says" in out[0], out
    assert fake.sign_calls == 0, "the live check reads the public key and signs nothing"


def test_live_check_reports_an_unset_kms_variable(kms_env, monkeypatch):
    from tests.test_key_purposes import checker
    _install(monkeypatch, FakeKms())
    monkeypatch.delenv("SIGNER_AWS_REGION")
    out = checker.live_findings(_live_config(HARDHAT_ADDR))
    assert out == ["registry-write: SIGNER_AWS_REGION ist in dieser Umgebung nicht gesetzt"], out
