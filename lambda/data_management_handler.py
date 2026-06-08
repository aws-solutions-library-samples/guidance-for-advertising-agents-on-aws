"""A4A Data Management MCP Handler.

Provides MCP tools for uploading, deploying, listing, and removing
customer-specific demo data. Runs as a Lambda target on the A4A MCP Gateway.

Tools:
  - upload_customer_data: Upload a CSV/JSON file to S3
  - deploy_customer: Trigger KB ingestion and/or Lambda rebundle
  - list_customer_datasets: List all customer overlay files
  - remove_customer_data: Remove a customer's overlay data

Environment Variables:
  DATA_BUCKET: S3 bucket for all data (default: a4a-data-{unique_id})
  STACK_PREFIX: Stack prefix (default: a4a)
  UNIQUE_ID: Deployment unique ID
  AWS_REGION: Region
"""

import json
import logging
import os
from typing import Any, Dict

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ============================================================================
# Configuration
# ============================================================================

DATA_BUCKET = os.environ.get("DATA_BUCKET", "a4a-data-omixaj")
STACK_PREFIX = os.environ.get("STACK_PREFIX", "a4a")
UNIQUE_ID = os.environ.get("UNIQUE_ID", "omixaj")
REGION = os.environ.get("AWS_REGION", "us-west-2")

# S3 path conventions
ADCP_PREFIX = "mcp_mocks/"          # AdCP Lambda reads from here
AAMP_PREFIX = "seller-data/"        # Seller agent reads from here
KB_PREFIX = "advertising-data/"     # Bedrock KB indexes from here

# File type → S3 prefix mapping
FILE_TYPE_PATHS = {
    "adcp_products": ADCP_PREFIX,
    "adcp_signals": ADCP_PREFIX,
    "adcp_campaigns": ADCP_PREFIX,
    "adcp_advertisers": ADCP_PREFIX,
    "aamp_inventory": AAMP_PREFIX,
    "aamp_audiences": AAMP_PREFIX,
    "kb_doc": KB_PREFIX,  # appended with customer_id/
}

# ============================================================================
# Tool Schema (for MCP Gateway registration)
# ============================================================================

TOOL_SCHEMA = {
    "tools": [
        {
            "name": "upload_customer_data",
            "description": (
                "Upload a customer-specific data file (CSV or JSON) to the A4A demo environment. "
                "The file is stored in S3 and made available to the appropriate system "
                "(AdCP Lambda, AAMP Seller, or Bedrock KB)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "Customer identifier (e.g. 'nineseven', 'prosiebensat1')",
                    },
                    "file_type": {
                        "type": "string",
                        "enum": [
                            "adcp_products", "adcp_signals", "adcp_campaigns",
                            "adcp_advertisers", "aamp_inventory", "aamp_audiences", "kb_doc",
                        ],
                        "description": "Type of data being uploaded",
                    },
                    "file_name": {
                        "type": "string",
                        "description": "Filename (e.g. 'products_nineseven.csv', 'au-sports-viewer-profiles.json')",
                    },
                    "content": {
                        "type": "string",
                        "description": "File content as text (CSV rows or JSON string)",
                    },
                },
                "required": ["customer_id", "file_type", "file_name", "content"],
            },
        },
        {
            "name": "deploy_customer",
            "description": (
                "Trigger deployment of customer data that has been uploaded. "
                "Triggers KB ingestion for knowledge base grounding. "
                "AdCP and Seller agents read from S3 at runtime — no explicit redeploy needed."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "Customer identifier",
                    },
                    "targets": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["kb", "adcp", "seller"]},
                        "description": "Which systems to trigger. Default: ['kb'] (KB ingestion only — AdCP/seller read from S3 automatically).",
                    },
                },
                "required": ["customer_id"],
            },
        },
        {
            "name": "list_customer_datasets",
            "description": "List all customer overlay datasets currently in S3.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "Optional: filter by customer ID. Omit to list all.",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "remove_customer_data",
            "description": (
                "Remove a customer's overlay data from S3. "
                "After removal, agents will serve only base (Meridian) data."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "Customer identifier to remove",
                    },
                },
                "required": ["customer_id"],
            },
        },
    ]
}

# ============================================================================
# S3 Client
# ============================================================================

s3_client = boto3.client("s3", region_name=REGION)
bedrock_agent_client = boto3.client("bedrock-agent", region_name=REGION)


# ============================================================================
# Tool Implementations
# ============================================================================


def handle_upload_customer_data(args: Dict[str, Any]) -> Dict:
    """Upload a customer data file to the appropriate S3 location."""
    customer_id = args["customer_id"]
    file_type = args["file_type"]
    file_name = args["file_name"]
    content = args["content"]

    # Determine S3 path
    base_prefix = FILE_TYPE_PATHS.get(file_type)
    if not base_prefix:
        return {"status": "error", "message": f"Unknown file_type: {file_type}"}

    if file_type == "kb_doc":
        # KB docs go under advertising-data/{customer_id}/
        s3_key = f"{base_prefix}{customer_id}/{file_name}"
    else:
        # CSVs go directly in the prefix with customer suffix in filename
        s3_key = f"{base_prefix}{file_name}"

    # Upload to S3
    try:
        s3_client.put_object(
            Bucket=DATA_BUCKET,
            Key=s3_key,
            Body=content.encode("utf-8"),
            ContentType="text/csv" if file_name.endswith(".csv") else "application/json",
        )
        logger.info(f"Uploaded: s3://{DATA_BUCKET}/{s3_key} ({len(content)} bytes)")
        return {
            "status": "success",
            "message": f"Uploaded {file_name} to s3://{DATA_BUCKET}/{s3_key}",
            "s3_uri": f"s3://{DATA_BUCKET}/{s3_key}",
            "size_bytes": len(content),
        }
    except ClientError as e:
        logger.error(f"Upload failed: {e}")
        return {"status": "error", "message": f"Upload failed: {e}"}


def handle_deploy_customer(args: Dict[str, Any]) -> Dict:
    """Trigger deployment/ingestion for uploaded customer data."""
    customer_id = args["customer_id"]
    targets = args.get("targets", ["kb"])

    results = {}

    if "kb" in targets:
        # Trigger KB ingestion for all matching KBs
        try:
            kbs = bedrock_agent_client.list_knowledge_bases(maxResults=100)
            matching = [
                kb for kb in kbs["knowledgeBaseSummaries"]
                if kb["name"].startswith(f"{STACK_PREFIX}-")
            ]

            ingestion_jobs = []
            for kb in matching:
                kb_id = kb["knowledgeBaseId"]
                ds_list = bedrock_agent_client.list_data_sources(knowledgeBaseId=kb_id)
                for ds in ds_list["dataSourceSummaries"]:
                    try:
                        job = bedrock_agent_client.start_ingestion_job(
                            knowledgeBaseId=kb_id, dataSourceId=ds["dataSourceId"]
                        )
                        ingestion_jobs.append({
                            "kb": kb["name"],
                            "job_id": job["ingestionJob"]["ingestionJobId"],
                        })
                    except Exception as e:
                        ingestion_jobs.append({"kb": kb["name"], "error": str(e)})

            results["kb"] = {
                "status": "success",
                "message": f"Started {len(ingestion_jobs)} ingestion job(s)",
                "jobs": ingestion_jobs,
            }
        except Exception as e:
            results["kb"] = {"status": "error", "message": str(e)}

    if "adcp" in targets:
        # AdCP reads from S3 at runtime (Option B) — just confirm files exist
        try:
            response = s3_client.list_objects_v2(
                Bucket=DATA_BUCKET, Prefix=f"{ADCP_PREFIX}",
            )
            adcp_files = [
                obj["Key"].split("/")[-1]
                for obj in response.get("Contents", [])
                if customer_id in obj["Key"]
            ]
            results["adcp"] = {
                "status": "success",
                "message": f"AdCP reads from S3 at runtime. {len(adcp_files)} file(s) for {customer_id} found.",
                "files": adcp_files,
            }
        except Exception as e:
            results["adcp"] = {"status": "error", "message": str(e)}

    if "seller" in targets:
        # Seller reads from S3 at runtime — confirm files exist
        try:
            response = s3_client.list_objects_v2(
                Bucket=DATA_BUCKET, Prefix=f"{AAMP_PREFIX}",
            )
            seller_files = [
                obj["Key"].split("/")[-1]
                for obj in response.get("Contents", [])
                if customer_id in obj["Key"]
            ]
            results["seller"] = {
                "status": "success",
                "message": f"Seller reads from S3 at runtime. {len(seller_files)} file(s) for {customer_id} found.",
                "files": seller_files,
            }
        except Exception as e:
            results["seller"] = {"status": "error", "message": str(e)}

    return {
        "status": "success",
        "customer_id": customer_id,
        "results": results,
    }


def handle_list_customer_datasets(args: Dict[str, Any]) -> Dict:
    """List customer overlay datasets in S3."""
    customer_id = args.get("customer_id", "")

    datasets = {"adcp": [], "seller": [], "kb": []}

    # Scan AdCP prefix
    try:
        response = s3_client.list_objects_v2(Bucket=DATA_BUCKET, Prefix=ADCP_PREFIX)
        for obj in response.get("Contents", []):
            fname = obj["Key"].split("/")[-1]
            if "_" in fname and fname != "products.csv":
                # It's an overlay file (has underscore = customer suffix)
                if not customer_id or customer_id in fname:
                    datasets["adcp"].append({
                        "file": fname,
                        "size": obj["Size"],
                        "last_modified": obj["LastModified"].isoformat(),
                    })
    except Exception as e:
        logger.warning(f"Error listing AdCP files: {e}")

    # Scan Seller prefix
    try:
        response = s3_client.list_objects_v2(Bucket=DATA_BUCKET, Prefix=AAMP_PREFIX)
        for obj in response.get("Contents", []):
            fname = obj["Key"].split("/")[-1]
            if "_" in fname and fname not in ("inventory.csv", "audiences.csv"):
                if not customer_id or customer_id in fname:
                    datasets["seller"].append({
                        "file": fname,
                        "size": obj["Size"],
                        "last_modified": obj["LastModified"].isoformat(),
                    })
    except Exception as e:
        logger.warning(f"Error listing seller files: {e}")

    # Scan KB prefix for customer subdirectories
    try:
        response = s3_client.list_objects_v2(Bucket=DATA_BUCKET, Prefix=KB_PREFIX)
        for obj in response.get("Contents", []):
            key = obj["Key"]
            # KB customer docs are at advertising-data/{customer_id}/filename.json
            parts = key[len(KB_PREFIX):].split("/")
            if len(parts) == 2 and parts[1].endswith(".json"):
                cust = parts[0]
                if not customer_id or customer_id == cust:
                    datasets["kb"].append({
                        "customer": cust,
                        "file": parts[1],
                        "size": obj["Size"],
                        "last_modified": obj["LastModified"].isoformat(),
                    })
    except Exception as e:
        logger.warning(f"Error listing KB files: {e}")

    # Summarize customers
    all_customers = set()
    for section in datasets.values():
        for item in section:
            if "customer" in item:
                all_customers.add(item["customer"])
            else:
                # Extract customer from filename (e.g. products_nineseven.csv → nineseven)
                fname = item["file"]
                parts = fname.rsplit(".", 1)[0].split("_", 1)
                if len(parts) > 1:
                    all_customers.add(parts[1])

    return {
        "status": "success",
        "customers": sorted(all_customers),
        "datasets": datasets,
        "total_files": sum(len(v) for v in datasets.values()),
    }


def handle_remove_customer_data(args: Dict[str, Any]) -> Dict:
    """Remove all overlay data for a customer."""
    customer_id = args["customer_id"]

    if not customer_id:
        return {"status": "error", "message": "customer_id is required"}

    deleted = {"adcp": [], "seller": [], "kb": []}

    # Delete from AdCP prefix
    try:
        response = s3_client.list_objects_v2(Bucket=DATA_BUCKET, Prefix=ADCP_PREFIX)
        for obj in response.get("Contents", []):
            if customer_id in obj["Key"]:
                s3_client.delete_object(Bucket=DATA_BUCKET, Key=obj["Key"])
                deleted["adcp"].append(obj["Key"].split("/")[-1])
    except Exception as e:
        logger.error(f"Error deleting AdCP files: {e}")

    # Delete from Seller prefix
    try:
        response = s3_client.list_objects_v2(Bucket=DATA_BUCKET, Prefix=AAMP_PREFIX)
        for obj in response.get("Contents", []):
            if customer_id in obj["Key"]:
                s3_client.delete_object(Bucket=DATA_BUCKET, Key=obj["Key"])
                deleted["seller"].append(obj["Key"].split("/")[-1])
    except Exception as e:
        logger.error(f"Error deleting seller files: {e}")

    # Delete from KB prefix
    try:
        kb_customer_prefix = f"{KB_PREFIX}{customer_id}/"
        response = s3_client.list_objects_v2(Bucket=DATA_BUCKET, Prefix=kb_customer_prefix)
        for obj in response.get("Contents", []):
            s3_client.delete_object(Bucket=DATA_BUCKET, Key=obj["Key"])
            deleted["kb"].append(obj["Key"].split("/")[-1])
    except Exception as e:
        logger.error(f"Error deleting KB files: {e}")

    total_deleted = sum(len(v) for v in deleted.values())

    # Trigger KB re-ingestion to remove stale embeddings
    if deleted["kb"]:
        try:
            kbs = bedrock_agent_client.list_knowledge_bases(maxResults=100)
            for kb in kbs["knowledgeBaseSummaries"]:
                if kb["name"].startswith(f"{STACK_PREFIX}-"):
                    ds_list = bedrock_agent_client.list_data_sources(
                        knowledgeBaseId=kb["knowledgeBaseId"]
                    )
                    for ds in ds_list["dataSourceSummaries"]:
                        bedrock_agent_client.start_ingestion_job(
                            knowledgeBaseId=kb["knowledgeBaseId"],
                            dataSourceId=ds["dataSourceId"],
                        )
        except Exception as e:
            logger.warning(f"KB re-ingestion trigger failed: {e}")

    return {
        "status": "success",
        "customer_id": customer_id,
        "deleted_files": total_deleted,
        "details": deleted,
        "message": f"Removed {total_deleted} file(s) for {customer_id}. KB re-ingestion triggered.",
    }


# ============================================================================
# Lambda Handler (MCP Gateway contract)
# ============================================================================

TOOL_HANDLERS = {
    "upload_customer_data": handle_upload_customer_data,
    "deploy_customer": handle_deploy_customer,
    "list_customer_datasets": handle_list_customer_datasets,
    "remove_customer_data": handle_remove_customer_data,
}


def lambda_handler(event: Dict, context: Any) -> Dict:
    """MCP Gateway Lambda handler.

    Follows the same contract as adcp_mcp_handler.py:
    - event["toolName"] or event["name"]: tool to invoke
    - event["arguments"] or event["input"]: tool parameters
    - Returns: {"content": [{"text": JSON string}]}
    """
    logger.info(f"Event: {json.dumps(event)[:500]}")

    # Extract tool name and arguments (handle multiple event formats)
    tool_name = event.get("toolName") or event.get("name", "")
    arguments = event.get("arguments") or event.get("input", {})

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            arguments = {}

    # Handle tools/list request (MCP protocol)
    if tool_name == "tools/list" or event.get("method") == "tools/list":
        return {
            "content": [{"text": json.dumps(TOOL_SCHEMA)}]
        }

    # Dispatch to handler
    handler = TOOL_HANDLERS.get(tool_name)
    if not handler:
        return {
            "content": [{"text": json.dumps({
                "status": "error",
                "message": f"Unknown tool: {tool_name}. Available: {list(TOOL_HANDLERS.keys())}",
            })}]
        }

    try:
        result = handler(arguments)
        return {"content": [{"text": json.dumps(result, default=str)}]}
    except Exception as e:
        logger.error(f"Tool {tool_name} failed: {e}", exc_info=True)
        return {
            "content": [{"text": json.dumps({
                "status": "error",
                "message": f"Tool execution failed: {e}",
            })}]
        }
