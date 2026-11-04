#!/usr/bin/env python3
"""Helper & end-to-end verification script for Direction 1 (Inbound 3P -> GCP via WIF).

Modes:
1. Generate stable RSA OIDC keys, JWKS JSON, and an inbound Bearer token for the 3P Space:
     python3 scripts/test_inbound_wif_to_gcp.py --generate-keys-only --out-dir /tmp/3p_oidc_keys
2. Trigger the live 3P Agent on Hugging Face Spaces to mint an OIDC JWT, exchange it
   at https://sts.googleapis.com/v1/token (WIF), and invoke GCP Agent Runtime:
     python3 scripts/test_inbound_wif_to_gcp.py \
       --three-p-url "https://<user>-external-a2a-lms-agent.hf.space" \
       --wif-provider "projects/<NUM>/locations/global/workloadIdentityPools/<POOL>/providers/<PROV>" \
       --reasoning-engine "projects/<PROJECT>/locations/us-central1/reasoningEngines/<ID>"
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import secrets
import urllib.request

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _int_to_base64url(val: int) -> str:
    byte_length = (val.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(val.to_bytes(byte_length, "big")).rstrip(b"=").decode("ascii")


def generate_keys(out_dir: Path, key_id: str = "3p-agent-oidc-key-1") -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    b64_pem = base64.b64encode(pem_bytes).decode("ascii")
    pub_numbers = private_key.public_key().public_numbers()
    jwks = {
        "keys": [
            {
                "kty": "RSA",
                "alg": "RS256",
                "use": "sig",
                "kid": key_id,
                "n": _int_to_base64url(pub_numbers.n),
                "e": _int_to_base64url(pub_numbers.e),
            }
        ]
    }
    inbound_token = f"3p-a2a-{secrets.token_urlsafe(24)}"

    (out_dir / "private_key.pem").write_bytes(pem_bytes)
    (out_dir / "oidc_private_key.b64").write_text(b64_pem + "\n", encoding="utf-8")
    (out_dir / "jwks.json").write_text(json.dumps(jwks, indent=2) + "\n", encoding="utf-8")
    (out_dir / "a2a_inbound_token.txt").write_text(inbound_token + "\n", encoding="utf-8")

    print(f"Generated 3P OIDC & A2A credentials in: {out_dir}")
    print("-" * 72)
    print(f"1. Set HF Space Secret A2A_INBOUND_TOKEN   = {inbound_token}")
    print(f"2. Set HF Space Secret OIDC_PRIVATE_KEY_B64 = (saved in {out_dir / 'oidc_private_key.b64'})")
    print(f"3. Public JWKS JSON saved to               = {out_dir / 'jwks.json'}")
    print("-" * 72)


def trigger_inbound_wif(
    three_p_url: str,
    wif_provider: str,
    reasoning_engine: str,
    prompt: str,
) -> None:
    endpoint = f"{three_p_url.rstrip('/')}/delegate-to-gcp"
    payload = {
        "wif_provider_resource_name": wif_provider,
        "reasoning_engine_resource_name": reasoning_engine,
        "prompt": prompt,
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        print(json.dumps(body, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify Inbound 3P -> GCP WIF Token Exchange & Agent Runtime Call.")
    parser.add_argument("--generate-keys-only", action="store_true", help="Generate RSA keypair, JWKS, and Bearer token.")
    parser.add_argument("--out-dir", default="/tmp/3p_oidc_keys", help="Output directory for generated keys.")
    parser.add_argument("--three-p-url", help="Base HTTPS URL of the deployed 3P Hugging Face Space.")
    parser.add_argument("--wif-provider", help="Full GCP WIF provider resource name.")
    parser.add_argument("--reasoning-engine", help="Full GCP ReasoningEngine resource name.")
    parser.add_argument(
        "--prompt",
        default="What is our institutional late_submission policy and how does it apply to CS-401?",
        help="Prompt sent by the 3P agent into GCP Agent Runtime.",
    )
    args = parser.parse_args()

    if args.generate_keys_only:
        generate_keys(Path(args.out_dir))
        return

    if not (args.three_p_url and args.wif_provider and args.reasoning_engine):
        parser.error("--three-p-url, --wif-provider, and --reasoning-engine are required unless --generate-keys-only is set.")

    trigger_inbound_wif(
        three_p_url=args.three_p_url,
        wif_provider=args.wif_provider,
        reasoning_engine=args.reasoning_engine,
        prompt=args.prompt,
    )


if __name__ == "__main__":
    main()
