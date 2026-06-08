#!/usr/bin/env python3
"""
Upload customer KB docs to S3 and trigger Bedrock KB re-ingestion.

Usage:
    python scripts/deploy_customer_kb.py nineseven --profile genai --region us-west-2
    python scripts/deploy_customer_kb.py nineseven --revert --profile genai --region us-west-2

The script:
  Deploy mode:
    1. Uploads customers/{id}/kb/*.json → s3://{bucket}/advertising-data/{id}/
    2. Triggers ingestion for all KBs with prefix matching the stack

  Revert mode (--revert):
    1. Deletes s3://{bucket}/advertising-data/{id}/ prefix
    2. Re-triggers ingestion (KB re-indexes without removed docs)
"""

import argparse
import json
import os
import sys
from pathlib import Path

import boto3


def get_config():
    """Load deployment config from environment or defaults."""
    return {
        "s3_bucket": os.environ.get("A4A_S3_BUCKET", "a4a-data-omixaj"),
        "stack_prefix": os.environ.get("STACK_PREFIX", "a4a"),
        "unique_id": os.environ.get("UNIQUE_ID", "omixaj"),
    }


def upload_kb_docs(customer_id: str, session, config: dict):
    """Upload KB docs from customers/{id}/kb/ to S3."""
    s3 = session.client("s3")
    bucket = config["s3_bucket"]

    # Find KB docs
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    kb_dir = project_root / "synthetic_data" / "customers" / customer_id / "kb"

    if not kb_dir.exists():
        print(f"❌ KB directory not found: {kb_dir}")
        sys.exit(1)

    kb_files = list(kb_dir.glob("*.json"))
    if not kb_files:
        print(f"⚠️  No JSON files found in {kb_dir}")
        return 0

    s3_prefix = f"advertising-data/{customer_id}/"
    uploaded = 0

    for f in kb_files:
        s3_key = f"{s3_prefix}{f.name}"
        print(f"  Uploading: {f.name} → s3://{bucket}/{s3_key}")
        s3.upload_file(str(f), bucket, s3_key)
        uploaded += 1

    print(f"✅ Uploaded {uploaded} KB doc(s) to s3://{bucket}/{s3_prefix}")
    return uploaded


def delete_kb_docs(customer_id: str, session, config: dict):
    """Delete customer KB docs from S3."""
    s3 = session.client("s3")
    bucket = config["s3_bucket"]
    s3_prefix = f"advertising-data/{customer_id}/"

    # List objects
    response = s3.list_objects_v2(Bucket=bucket, Prefix=s3_prefix)
    objects = response.get("Contents", [])

    if not objects:
        print(f"⚠️  No objects found at s3://{bucket}/{s3_prefix}")
        return

    # Delete all
    delete_keys = [{"Key": obj["Key"]} for obj in objects]
    s3.delete_objects(Bucket=bucket, Delete={"Objects": delete_keys})
    print(f"✅ Deleted {len(delete_keys)} object(s) from s3://{bucket}/{s3_prefix}")


def trigger_ingestion(session, config: dict):
    """Start ingestion for all KBs matching the stack prefix."""
    client = session.client("bedrock-agent")
    prefix = config["stack_prefix"]

    kbs = client.list_knowledge_bases(maxResults=100)["knowledgeBaseSummaries"]
    matching = [kb for kb in kbs if kb["name"].startswith(f"{prefix}-")]

    if not matching:
        print(f"⚠️  No knowledge bases found with prefix '{prefix}-'")
        return

    print(f"Found {len(matching)} knowledge base(s):")
    for kb in matching:
        kb_id = kb["knowledgeBaseId"]
        print(f"  {kb['name']} ({kb_id})")

        ds_list = client.list_data_sources(knowledgeBaseId=kb_id)["dataSourceSummaries"]
        for ds in ds_list:
            ds_id = ds["dataSourceId"]
            try:
                job = client.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)
                job_id = job["ingestionJob"]["ingestionJobId"]
                print(f"    ✅ Ingestion started: {ds['name']} → {job_id}")
            except Exception as e:
                print(f"    ⚠️  {ds['name']}: {e}")

    print("\n⏳ Ingestion running in background (~5-15 min)")


def main():
    parser = argparse.ArgumentParser(description="Deploy/revert customer KB data for A4A agents")
    parser.add_argument("customer_id", help="Customer identifier (e.g. nineseven)")
    parser.add_argument("--revert", action="store_true", help="Remove customer KB data and re-ingest")
    parser.add_argument("--profile", default="genai", help="AWS profile (default: genai)")
    parser.add_argument("--region", default="us-west-2", help="AWS region (default: us-west-2)")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    config = get_config()

    print(f"{'🗑️  REVERT' if args.revert else '🚀 DEPLOY'} — Customer: {args.customer_id}")
    print(f"  Bucket: {config['s3_bucket']}")
    print(f"  Profile: {args.profile} | Region: {args.region}")
    print()

    if args.revert:
        delete_kb_docs(args.customer_id, session, config)
    else:
        upload_kb_docs(args.customer_id, session, config)

    print()
    trigger_ingestion(session, config)


if __name__ == "__main__":
    main()
