"""Create the S3 bucket holding multi-turn conversation state.

    python .venv/bin/python deploy_session_bucket.py            # dry run
    python .venv/bin/python deploy_session_bucket.py --apply

`runtime_env.py` does `os.environ["SESSION_STORAGE_BUCKET"]`, so the buyer runtime will not configure
without this bucket's name, and Strands' `S3SessionManager` writes conversation history into it
(`agent.py`). Nothing created it: it was made by hand in the original account, which is invisible for
as long as you only ever deploy to that account and a hard stop the first time you deploy anywhere
else.

Private and encrypted, because it holds conversation content. Bucket names are globally unique; the
instance prefix (an auto-generated unique id) now provides that uniqueness, so the name is just
`<prefix>-buyer-agent-sessions` -- account/region are tracked in the local deployment manifest, not
baked into the name. Same scheme `deploy_ui.py` uses.
"""

from __future__ import annotations

from aws_region import region

import argparse
import functools
import os
import sys

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

REGION = region()

#: Instance prefix (see deploy_all.resolve_prefix): the auto-generated unique id every resource name
#: derives from. Defaults to 'adcp' (the original instance) for a standalone run; deploy_all.py
#: exports INSTANCE_PREFIX so orchestrated runs inherit the right one.
INSTANCE_PREFIX = os.environ.get("INSTANCE_PREFIX", "adcp").strip() or "adcp"


@functools.lru_cache(maxsize=1)
def account_id() -> str:
    return os.environ.get("AWS_ACCOUNT_ID", "").strip() or boto3.client(
        "sts"
    ).get_caller_identity()["Account"]


def bucket_name() -> str:
    return f"{INSTANCE_PREFIX}-buyer-agent-sessions"


def create_bucket(s3, name: str) -> bool:
    """Returns True if created, False if it already existed."""
    try:
        s3.head_bucket(Bucket=name)
        print(f"  bucket already exists: {name}")
        return False
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("404", "403", "NoSuchBucket"):
            raise

    # us-east-1 is the one region where CreateBucketConfiguration must be omitted rather than set --
    # passing it there fails with InvalidLocationConstraint.
    kwargs: dict = {"Bucket": name}
    if REGION != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": REGION}
    s3.create_bucket(**kwargs)
    print(f"  created bucket: {name}")
    return True


def harden_bucket(s3, name: str) -> None:
    s3.put_public_access_block(
        Bucket=name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    print("  public access blocked")

    s3.put_bucket_encryption(
        Bucket=name,
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        },
    )
    print("  default encryption: AES256")

    # Conversation history is recoverable state, not a record to keep: the runtime rewrites a session's
    # object on every turn, so versioning would accumulate a copy per turn indefinitely.
    s3.put_bucket_versioning(Bucket=name, VersioningConfiguration={"Status": "Suspended"})
    print("  versioning: suspended")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="create it; otherwise report only")
    args = parser.parse_args()

    name = bucket_name()
    print(f"account : {account_id()}")
    print(f"region  : {REGION}")
    print(f"bucket  : {name}")
    print()

    if not args.apply:
        print("Dry run. Nothing created. Re-run with --apply.")
        return 0

    s3 = boto3.client("s3", region_name=REGION)
    create_bucket(s3, name)
    harden_bucket(s3, name)

    print()
    print("=== Done ===")
    print(f"Set SESSION_STORAGE_BUCKET={name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
