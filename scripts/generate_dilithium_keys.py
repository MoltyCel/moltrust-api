#!/usr/bin/env python3
"""Generate a Dilithium3 (ML-DSA-65) keypair for MolTrust.

Usage:
    pip install liboqs-python
    python scripts/generate_dilithium_keys.py

Output:
    Prints the secret key and public key as hex strings.
    Store the secret key securely (KMS or env var).
    Set DILITHIUM_PUBLIC_KEY_HEX in the environment.
"""

try:
    import oqs
except ImportError:
    print("Error: liboqs-python not installed.")
    print("Install with: pip install liboqs-python")
    print("See: https://github.com/open-quantum-safe/liboqs-python")
    raise SystemExit(1)

signer = oqs.Signature("ML-DSA-65")
public_key = signer.generate_keypair()
secret_key = signer.export_secret_key()

print(f"Algorithm: ML-DSA-65 (Dilithium3)")
print(f"Secret key length: {len(secret_key)} bytes")
print(f"Public key length: {len(public_key)} bytes")
print()
print(f"DILITHIUM_PRIVATE_KEY_HEX={secret_key.hex()}")
print()
print(f"DILITHIUM_PUBLIC_KEY_HEX={public_key.hex()}")
print()
print("IMPORTANT: Store the secret key in AWS KMS or a secrets manager.")
print("Never commit it to version control.")
