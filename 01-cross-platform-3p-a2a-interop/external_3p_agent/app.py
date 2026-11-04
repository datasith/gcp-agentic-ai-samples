"""External 3P A2A Agent + OIDC WIF Issuer for Cross-Platform GCP Interop Demo.

Exposes:
1. GET  /.well-known/agent-card.json     -> A2A v0.3.0 Agent Card
2. POST /a2a                             -> A2A JSON-RPC 2.0 (message/send)
3. GET  /.well-known/openid-configuration -> OIDC Discovery metadata (for GCP WIF)
4. GET  /.well-known/jwks.json           -> Public RSA JWKS (for GCP WIF)
5. POST /delegate-to-gcp                 -> Mints 3P OIDC JWT -> exchanges via
                                            GCP STS (RFC 8693 WIF) -> invokes
                                            target GCP Agent Runtime instance
"""

from __future__ import annotations

import base64
import hmac
import os
import time
import uuid
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
import httpx
import jwt
from pydantic import BaseModel, Field

app = FastAPI(
    title="External 3P A2A Course & Support Agent",
    version="1.0.0",
    description="Representative 3P SaaS/LMS A2A Agent and OIDC Workload Identity Federation Issuer.",
)

# ------------------------------------------------------------------------------
# Representative Course Catalog (Labeled explicitly as representative data)
# ------------------------------------------------------------------------------
REPRESENTATIVE_COURSES: dict[str, dict[str, Any]] = {
    "CS-401": {
        "title": "Distributed Agentic Systems",
        "instructor": "Dr. Elena Vance",
        "grading_policy": "40% Labs, 30% Midterm Architecture Review, 30% Capstone A2A Project",
        "next_deadline": "2026-11-15T23:59:00Z (Lab 3: Workload Identity Federation & A2A)",
        "lms_platform": "Representative 3P LMS",
    },
    "AI-302": {
        "title": "Enterprise AI Governance & Security",
        "instructor": "Prof. Marcus Sterling",
        "grading_policy": "50% Case Studies, 25% Policy Audits, 25% Final Exam",
        "next_deadline": "2026-11-12T23:59:00Z (Module 4: Model Armor & Egress UAPs)",
        "lms_platform": "Representative 3P LMS",
    },
}


# ------------------------------------------------------------------------------
# OIDC RSA Keypair Initialization (Supports stable env secret or ephemeral key)
# ------------------------------------------------------------------------------
def _load_or_create_rsa_key() -> rsa.RSAPrivateKey:
    b64_pem = os.environ.get("OIDC_PRIVATE_KEY_B64", "").strip()
    if b64_pem:
        pem_bytes = base64.b64decode(b64_pem)
        loaded = serialization.load_pem_private_key(pem_bytes, password=None)
        if isinstance(loaded, rsa.RSAPrivateKey):
            return loaded
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


_PRIVATE_KEY = _load_or_create_rsa_key()
_KEY_ID = os.environ.get("OIDC_KEY_ID", "3p-agent-oidc-key-1")


def _int_to_base64url(val: int) -> str:
    byte_length = (val.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(val.to_bytes(byte_length, "big")).rstrip(b"=").decode("ascii")


def _get_public_jwk() -> dict[str, str]:
    pub_numbers = _PRIVATE_KEY.public_key().public_numbers()
    return {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": _KEY_ID,
        "n": _int_to_base64url(pub_numbers.n),
        "e": _int_to_base64url(pub_numbers.e),
    }


def _resolve_base_url(request: Request) -> str:
    configured = os.environ.get("PUBLIC_ISSUER_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost:7860"))
    return f"{proto}://{host}".rstrip("/")


# ------------------------------------------------------------------------------
# UI & Health Endpoints
# ------------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def root(request: Request) -> str:
    base_url = _resolve_base_url(request)
    auth_configured = bool(os.environ.get("A2A_INBOUND_TOKEN", "").strip())
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>External 3P A2A Agent &amp; OIDC Issuer</title>
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; background: #f8f9fa; color: #202124; margin: 2rem auto; max-width: 880px; padding: 0 1rem; }}
    .card {{ background: #ffffff; border: 1px solid #dadce0; border-radius: 8px; padding: 1.25rem 1.5rem; margin-bottom: 1rem; }}
    h1 {{ color: #1a73e8; margin-top: 0; font-size: 1.5rem; }}
    h2 {{ color: #202124; font-size: 1.1rem; margin-top: 0; }}
    code {{ background: #f1f3f4; padding: 0.15rem 0.35rem; border-radius: 4px; font-size: 0.9rem; }}
    .badge {{ display: inline-block; padding: 0.2rem 0.6rem; border-radius: 999px; font-size: 0.8rem; font-weight: 600; background: #e6f4ea; color: #137333; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>External 3P A2A Course &amp; Support Agent</h1>
    <p><span class="badge">LIVE</span> Base URL: <code>{base_url}</code> | Inbound Bearer Auth Enforced: <code>{auth_configured}</code></p>
    <p>Representative third-party SaaS / LMS agent for demonstrating bidirectional interoperability with Google Cloud Agent Runtime.</p>
  </div>
  <div class="card">
    <h2>Direction 2 (Outbound from GCP -> This 3P Agent via A2A)</h2>
    <ul>
      <li>Agent Card: <a href="{base_url}/.well-known/agent-card.json"><code>{base_url}/.well-known/agent-card.json</code></a></li>
      <li>A2A JSON-RPC Endpoint: <code>POST {base_url}/a2a</code> (method: <code>message/send</code>)</li>
    </ul>
  </div>
  <div class="card">
    <h2>Direction 1 (Inbound from This 3P Agent -> GCP Agent Runtime via WIF)</h2>
    <ul>
      <li>OIDC Discovery: <a href="{base_url}/.well-known/openid-configuration"><code>{base_url}/.well-known/openid-configuration</code></a></li>
      <li>OIDC Public JWKS: <a href="{base_url}/.well-known/jwks.json"><code>{base_url}/.well-known/jwks.json</code></a></li>
      <li>Trigger WIF Call to GCP: <code>POST {base_url}/delegate-to-gcp</code></li>
    </ul>
  </div>
</body>
</html>"""


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "agent": "external-3p-lms-agent"}


# ------------------------------------------------------------------------------
# Direction 2: A2A Discovery & JSON-RPC 2.0 Execution (GCP -> 3P)
# ------------------------------------------------------------------------------
@app.get("/.well-known/agent-card.json")
async def agent_card(request: Request) -> dict[str, Any]:
    base_url = _resolve_base_url(request)
    return {
        "name": "external_3p_lms_agent",
        "description": "Representative external 3P LMS agent that looks up course syllabi/deadlines and manages student support tickets.",
        "url": f"{base_url}/a2a",
        "version": "1.0.0",
        "protocolVersion": "0.3.0",
        "preferredTransport": "JSONRPC",
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "capabilities": {"streaming": False},
        "supportsAuthenticatedExtendedCard": False,
        "skills": [
            {
                "id": "course_syllabus_lookup",
                "name": "course_syllabus_lookup",
                "description": "Returns representative syllabus, grading policy, and next deadline for CS-401 or AI-302.",
                "tags": ["lms", "course", "syllabus"],
            },
            {
                "id": "student_support_ticket",
                "name": "student_support_ticket",
                "description": "Opens a representative student support ticket in the external 3P LMS.",
                "tags": ["lms", "support", "ticket"],
            },
        ],
    }


def _verify_inbound_auth(authorization: str | None, x_api_key: str | None) -> None:
    expected = os.environ.get("A2A_INBOUND_TOKEN", "").strip()
    if not expected:
        return
    candidate = ""
    if authorization and authorization.lower().startswith("bearer "):
        candidate = authorization[7:].strip()
    elif x_api_key:
        candidate = x_api_key.strip()
    if not candidate or not hmac.compare_digest(candidate, expected):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: missing or invalid Bearer token / X-API-Key from GCP Auth Manager binding.",
        )


def _handle_lms_prompt(user_text: str) -> str:
    upper = user_text.upper()
    for code, info in REPRESENTATIVE_COURSES.items():
        if code in upper or code.replace("-", "") in upper:
            return (
                f"[Representative 3P LMS Record - {code}: {info['title']}] "
                f"Instructor: {info['instructor']} | "
                f"Grading Policy: {info['grading_policy']} | "
                f"Next Deadline: {info['next_deadline']}."
            )
    if "TICKET" in upper or "HELP" in upper or "SUPPORT" in upper:
        ticket_id = f"LMS-{uuid.uuid4().hex[:6].upper()}"
        return (
            f"[Representative 3P LMS Support] Created ticket {ticket_id} "
            f"for request: '{user_text[:120]}'. Status: ASSIGNED_TO_ACADEMIC_ADVISOR."
        )
    available = ", ".join( f"{k} ({v['title']})" for k, v in REPRESENTATIVE_COURSES.items() )
    return (
        f"[Representative 3P LMS Agent] Available courses: {available}. "
        f"Ask about CS-401, AI-302, or request a student support ticket."
    )


@app.post("/a2a")
async def a2a_endpoint(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict[str, Any]:
    _verify_inbound_auth(authorization, x_api_key)

    rpc_id = payload.get("id", str(uuid.uuid4()))
    method = payload.get("method", "")
    params = payload.get("params") or {}

    if method not in ("message/send", "tasks/send", "SendMessage"):
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {"code": -32601, "message": f"Method '{method}' not supported. Use 'message/send'."},
        }

    message = params.get("message") or {}
    parts = message.get("parts") or []
    texts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")]
    user_text = " ".join(texts).strip() or "List available courses"

    reply_text = _handle_lms_prompt(user_text)
    task_id = params.get("id") or f"task-{uuid.uuid4().hex[:10]}"
    context_id = (
        message.get("contextId")
        or message.get("context_id")
        or params.get("contextId")
        or f"ctx-{uuid.uuid4().hex[:10]}"
    )

    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": {
            "id": task_id,
            "contextId": context_id,
            "kind": "task",
            "status": {"state": "completed"},
            "artifacts": [
                {
                    "artifactId": f"art-{uuid.uuid4().hex[:8]}",
                    "name": "lms_response",
                    "parts": [{"kind": "text", "text": reply_text}],
                }
            ],
        },
    }


# ------------------------------------------------------------------------------
# Direction 1: Built-in OIDC Discovery & JWKS + WIF Delegation to GCP
# ------------------------------------------------------------------------------
@app.get("/.well-known/openid-configuration")
async def openid_configuration(request: Request) -> dict[str, Any]:
    issuer = _resolve_base_url(request)
    return {
        "issuer": issuer,
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
        "authorization_endpoint": f"{issuer}/oauth/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        "response_types_supported": ["id_token"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }


@app.get("/.well-known/jwks.json")
async def jwks() -> dict[str, Any]:
    return {"keys": [_get_public_jwk()]}


class DelegateToGcpRequest(BaseModel):
    wif_provider_resource_name: str = Field(
        ...,
        description="Full WIF provider path: projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/<POOL>/providers/<PROVIDER>",
    )
    reasoning_engine_resource_name: str = Field(
        ...,
        description="Full ReasoningEngine path: projects/<PROJECT_ID_OR_NUMBER>/locations/<REGION>/reasoningEngines/<ID>",
    )
    prompt: str = Field(
        default="Summarize the syllabus and next deadline for CS-401.",
        description="Prompt to send from the 3P agent into the GCP Agent Runtime instance.",
    )
    subject: str = Field(
        default="agent:external-3p-lms-agent",
        description="OIDC 'sub' claim matching the WIF attribute condition.",
    )


def mint_3p_oidc_jwt(issuer: str, audience: str, subject: str) -> str:
    now = int(time.time())
    claims = {
        "iss": issuer,
        "sub": subject,
        "aud": audience,
        "iat": now - 10,
        "exp": now + 600,
        "agent_role": "external_lms_specialist",
    }
    pem_private = _PRIVATE_KEY.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(claims, pem_private, algorithm="RS256", headers={"kid": _KEY_ID})


@app.post("/delegate-to-gcp")
async def delegate_to_gcp(req: DelegateToGcpRequest, request: Request) -> dict[str, Any]:
    issuer = _resolve_base_url(request)
    wif_audience = f"//iam.googleapis.com/{req.wif_provider_resource_name.lstrip('/')}"
    subject_token = mint_3p_oidc_jwt(issuer=issuer, audience=wif_audience, subject=req.subject)

    # Step 1: Exchange 3P OIDC JWT at Google STS (RFC 8693 Workload Identity Federation)
    sts_payload = {
        "audience": wif_audience,
        "grantType": "urn:ietf:params:oauth:grant-type:token-exchange",
        "requestedTokenType": "urn:ietf:params:oauth:token-type:access_token",
        "scope": "https://www.googleapis.com/auth/cloud-platform",
        "subjectTokenType": "urn:ietf:params:oauth:token-type:jwt",
        "subjectToken": subject_token,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        sts_resp = await client.post("https://sts.googleapis.com/v1/token", json=sts_payload)
        if sts_resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail={
                    "stage": "sts_token_exchange",
                    "status_code": sts_resp.status_code,
                    "error": sts_resp.text,
                    "issuer_used": issuer,
                    "audience_used": wif_audience,
                },
            )
        gcp_access_token = sts_resp.json()["access_token"]

        # Step 2: Invoke the target GCP Agent Runtime (ReasoningEngine)
        parts = req.reasoning_engine_resource_name.strip("/").split("/")
        if len(parts) < 6 or parts[2] != "locations":
            raise HTTPException(
                status_code=400,
                detail="reasoning_engine_resource_name must match projects/<PROJECT>/locations/<REGION>/reasoningEngines/<ID>",
            )
        region = parts[3]
        re_path = "/".join(parts[:6])
        query_url = f"https://{region}-aiplatform.googleapis.com/v1beta1/{re_path}:streamQuery?alt=sse"
        query_body = {
            "class_method": "stream_query",
            "input": {
                "user_id": req.subject,
                "message": req.prompt,
            },
        }
        re_resp = await client.post(
            query_url,
            headers={
                "Authorization": f"Bearer {gcp_access_token}",
                "Content-Type": "application/json",
            },
            json=query_body,
        )

    return {
        "direction": "INBOUND_3P_TO_GCP_AGENT_RUNTIME",
        "auth_mechanism": "IAM_WORKLOAD_IDENTITY_FEDERATION_RFC8693",
        "oidc_issuer": issuer,
        "oidc_subject": req.subject,
        "sts_exchange": "SUCCESS",
        "reasoning_engine": re_path,
        "runtime_status_code": re_resp.status_code,
        "runtime_response": re_resp.text[:4000],
    }
