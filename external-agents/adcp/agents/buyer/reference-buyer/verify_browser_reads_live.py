"""Does the Identity Pool actually let the browser read sessions — and only read them?

Run before trusting any UI code against it, and after any change to `deploy_identity_pool.py`.

    .venv/bin/python3 verify_browser_reads_live.py

Six things are checked against real AWS, in the order that a failure is most informative:

  1. a real user-pool ID token exchanges for temporary IAM credentials
  2. those credentials can read one session's steps        (the 1s poll's hot path)
  3. those credentials can read the session list via gsi1  (the 5s poll)
  4. those credentials are REFUSED a write                 (read-only actually holds)
  5. no token yields no credentials                        (unauthenticated is closed)
  6. how long the direct read takes vs the /invocations baseline it replaces

(4) and (5) are the ones worth having. A policy that grants more than intended still passes (1)-(3),
so a verifier that stopped there would report success for a table the browser could rewrite.

No credential material is ever printed — only whether it was obtained, and the identity id, which is
not a secret.
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from pathlib import Path

import boto3
import requests
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from aws_region import region  # noqa: E402

TABLE = os.environ["SESSIONS_TABLE_NAME"]
POOL_ID = os.environ.get("IDENTITY_POOL_ID", "")
USER_POOL_ID = os.environ["COGNITO_USER_POOL_ID"]

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}" + (f" -- {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def id_token() -> str:
    """An ID token, not an access token. Cognito Identity's `Logins` map wants the ID token; an
    access token is rejected with `NotAuthorizedException: Invalid login token`, which reads like a
    credential problem rather than a wrong-token-type problem."""
    resp = requests.post(
        f"https://cognito-idp.{os.environ['COGNITO_REGION']}.amazonaws.com/",
        json={
            "AuthFlow": "USER_PASSWORD_AUTH",
            "ClientId": os.environ["COGNITO_CLIENT_ID"],
            "AuthParameters": {
                "USERNAME": os.environ["COGNITO_TEST_USERNAME"],
                "PASSWORD": os.environ["COGNITO_TEST_USER_PASSWORD"],
            },
        },
        headers={
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["AuthenticationResult"]["IdToken"]


def main() -> None:
    if not POOL_ID:
        raise SystemExit("IDENTITY_POOL_ID is not in .env. Run deploy_identity_pool.py --apply.")

    print(f"identity pool : {POOL_ID}")
    print(f"table         : {TABLE}\n")

    provider = f"cognito-idp.{region()}.amazonaws.com/{USER_POOL_ID}"
    identity = boto3.client("cognito-identity", region_name=region())

    print("=== 1. Exchange an ID token for temporary credentials ===")
    token = id_token()
    started = time.perf_counter()
    ident = identity.get_id(IdentityPoolId=POOL_ID, Logins={provider: token})
    creds_response = identity.get_credentials_for_identity(
        IdentityId=ident["IdentityId"], Logins={provider: token}
    )
    exchange_ms = (time.perf_counter() - started) * 1000.0
    creds = creds_response["Credentials"]
    check(
        "an ID token exchanges for temporary IAM credentials",
        bool(creds.get("AccessKeyId")) and bool(creds.get("SessionToken")),
        f"identity {ident['IdentityId']}, exchange took {exchange_ms:.0f} ms",
    )
    print(f"        expires: {creds.get('Expiration')}  (short-lived, re-fetched by the browser)")

    # A client with exactly the browser's authority. Named `browser` throughout so no test can
    # accidentally prove something using this process's own admin credentials instead.
    browser = boto3.client(
        "dynamodb",
        region_name=region(),
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretKey"],
        aws_session_token=creds["SessionToken"],
    )

    print("\n=== 2. Read one session's steps (the 1s poll) ===")
    # Find a real session using this process's own credentials, so a discovery failure is not
    # mistaken for a permissions failure.
    admin = boto3.client("dynamodb", region_name=region())
    listed = admin.query(
        TableName=TABLE,
        IndexName="gsi1",
        KeyConditionExpression="gsi1pk = :p",
        ExpressionAttributeValues={":p": {"S": "SESSION"}},
        Limit=5,
        ScanIndexForward=False,
    )
    items = listed.get("Items", [])
    if not items:
        raise SystemExit("No sessions recorded; run a journey first so there is something to read.")
    session_pk = items[0]["pk"]["S"]
    session_id = session_pk.removeprefix("SESSION#")
    print(f"  session under test: {session_id}")

    steps = browser.query(
        TableName=TABLE,
        KeyConditionExpression="pk = :pk AND begins_with(sk, :sk)",
        ExpressionAttributeValues={":pk": {"S": session_pk}, ":sk": {"S": "STEP#"}},
    )
    check(
        "the browser's credentials can read a session's steps",
        "Items" in steps,
        f"{len(steps.get('Items', []))} steps",
    )

    print("\n=== 3. Read the session list via gsi1 (the 5s poll) ===")
    listed_as_browser = browser.query(
        TableName=TABLE,
        IndexName="gsi1",
        KeyConditionExpression="gsi1pk = :p",
        ExpressionAttributeValues={":p": {"S": "SESSION"}},
        Limit=20,
        ScanIndexForward=False,
    )
    check(
        "the browser's credentials can read the session list",
        len(listed_as_browser.get("Items", [])) > 0,
        f"{len(listed_as_browser.get('Items', []))} sessions",
    )

    print("\n=== 4. Writes must be REFUSED ===")
    # The check that makes the other three meaningful. A policy granting more than intended would
    # pass 1-3 identically.
    try:
        browser.put_item(
            TableName=TABLE,
            Item={"pk": {"S": "SESSION#verifier-must-not-write"}, "sk": {"S": "META"}},
        )
        check("a write is refused", False, "the write SUCCEEDED — the policy is too permissive")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        check("a write is refused", code == "AccessDeniedException", f"got {code}")

    print("\n=== 4b. Scan must be REFUSED (no walking the table) ===")
    try:
        browser.scan(TableName=TABLE, Limit=1)
        check("a scan is refused", False, "the scan SUCCEEDED — the policy is too permissive")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        check("a scan is refused", code == "AccessDeniedException", f"got {code}")

    print("\n=== 5. No token yields no credentials ===")
    try:
        anon = identity.get_id(IdentityPoolId=POOL_ID)
        identity.get_credentials_for_identity(IdentityId=anon["IdentityId"])
        check("an unauthenticated caller gets no credentials", False, "it got credentials")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        check(
            "an unauthenticated caller gets no credentials",
            code in ("NotAuthorizedException", "InvalidIdentityPoolConfigurationException"),
            f"got {code}",
        )

    print("\n=== 6. Latency, direct vs the /invocations baseline ===")
    samples: list[float] = []
    for _ in range(10):
        start = time.perf_counter()
        browser.query(
            TableName=TABLE,
            KeyConditionExpression="pk = :pk AND begins_with(sk, :sk)",
            ExpressionAttributeValues={":pk": {"S": session_pk}, ":sk": {"S": "STEP#"}},
        )
        samples.append((time.perf_counter() - start) * 1000.0)
    ordered = sorted(samples)
    p50 = ordered[len(ordered) // 2]
    print(
        f"  direct query   min {ordered[0]:6.1f}  p50 {p50:6.1f}  max {ordered[-1]:6.1f}  (ms)"
    )
    print(f"  mean {statistics.fmean(ordered):.1f} ms")
    print("  baseline, measured before this change: /invocations poll p50 1089 ms, ~843 ms of it transport")
    print(f"  credential exchange is a one-off per session: {exchange_ms:.0f} ms")
    # Measured from a laptop, so this includes internet RTT the browser also pays. Not a claim about
    # in-region latency.
    check(
        "the direct read beats the /invocations poll it replaces",
        p50 < 1089.0,
        f"p50 {p50:.1f} ms vs 1089 ms",
    )

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for item in failures:
            print(f"  - {item}")
        raise SystemExit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
