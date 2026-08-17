"""The AWS Marketplace client is constructed for real here — deliberately unmocked.

Every other test around this endpoint replaces _resolve_customer with a stub, so
the client it would have built was never exercised. That is how
boto3.client("marketplacemetering") — a service name that does not exist —
survived from #265 until a real AWS review purchase hit it on 2026-08-17 and got
an error page. botocore raises UnknownServiceError, which inherits from
BotoCoreError, so the handler translated it into exactly the same HTTP 400 as an
expired token: the failure was indistinguishable from normal buyer error.

These tests need no credentials and make no AWS call — constructing a client only
reads the service model shipped inside botocore.
"""
import pytest

import app.aws_marketplace as awsmp


def test_marketplace_client_targets_the_real_service(monkeypatch):
    """Constructing the client at all is half the assertion."""
    monkeypatch.setenv("AWS_MP_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
    monkeypatch.setenv("AWS_MP_SECRET_ACCESS_KEY", "wJalrXUtnFEMI-EXAMPLE-KEY")

    client = awsmp._marketplace_client()

    assert client.meta.service_model.service_name == "meteringmarketplace"
    assert client.meta.region_name == awsmp.AWS_MP_REGION
    # ResolveCustomer has to exist on the modelled service, not just the name.
    assert hasattr(client, "resolve_customer")


def test_missing_credentials_still_raise_runtime_error(monkeypatch):
    """The 503 branch depends on this being a RuntimeError, not a boto error."""
    monkeypatch.delenv("AWS_MP_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_MP_SECRET_ACCESS_KEY", raising=False)

    with pytest.raises(RuntimeError):
        awsmp._marketplace_client()
