"""
Deploys the buyer chat UI to its own S3 bucket fronted by CloudFront, so it's
reachable at a real HTTPS URL instead of only via `python3 app.py` on a
developer's laptop. Idempotent — safe to re-run after any change to the UI or
the seller registry to republish.

The published UI is the React app in `ui/`, and **this script builds it** before
publishing (`npm run build`, which runs `tsc -b` first, so a type error fails the
deploy rather than shipping). Pass `--skip-build` only to republish a build you
already know is current.

It used to require you to have built it by hand, and checked only that `ui/dist`
EXISTED — never that it was CURRENT. So a stale build published silently and
reported success. See `build_react()` for the incident.

The older single-file UI in `static/index.html` is no longer deployed. It is
kept as a frozen reference and is still served locally by app.py at /legacy;
see .kiro/steering/buyer-ui-react-is-canonical.md. Objects belonging to it,
and to the transitional `react/` prefix the build used while both UIs ran side
by side, are removed on every run (STALE_KEY_PREFIXES).

Run after deploy_launch.py (the UI needs AGENT_RUNTIME_ARN and the Cognito/
seller-registry values already in .env to build config.json):

    source .venv/bin/activate
    python3 deploy_ui.py

What this creates/updates, all real AWS resources (nothing here is
simulated or dry-run):
  1. A private S3 bucket (Block Public Access fully on) holding the built
     UI and a generated config.json.
  2. A CloudFront Origin Access Control (OAC) scoped to that bucket, so the
     bucket is reachable only through this distribution, never directly.
  3. A CloudFront distribution using the bucket as its origin over OAC,
     HTTPS-only, default root object index.html.
  4. A bucket policy granting cloudfront.amazonaws.com read access scoped
     to this specific distribution ARN (AWS:SourceArn + AWS:SourceAccount),
     not a broad public grant.

The browser UI itself does not change behavior: it still calls Cognito and
the deployed AgentCore Runtime invoke endpoint directly from the client,
exactly as it does when served locally by app.py (see app.py's module
docstring and README.md's "Architecture"). The only difference is where
index.html and its config.json come from — a CDN URL instead of localhost.

config.json (generated fresh on every run of this script, uploaded next to
index.html, never committed) holds exactly the same fields app.py's local
`/config` route returns: model id, Cognito pool/client, the deployed
runtime's ARN/region, and the seller-agent registry (id/name/url/transport
only — no auth tokens, see seller_agents.py::list_seller_agents_public).
The UI fetches '/config' first and falls back to './config.json' when that
is not a route, so one build works unmodified against either the local dev
server or this static-hosted deployment.
"""


from aws_region import CLOUDFRONT_API_REGION, region
import argparse
import json
import os
import subprocess
import time

import boto3
from dotenv import load_dotenv

from agents_registry import AgentRegistryError, get_default_agent_id, list_agents_public
from seller_agents import SellerAgentError, get_default_seller_agent_id, list_seller_agents_public

load_dotenv()

REGION = region()
ACCOUNT_ID = os.environ["AWS_ACCOUNT_ID"]

#: Instance prefix (see deploy_all.resolve_prefix). Every resource name derives from it so multiple
#: full instances coexist in one account. It replaces the old account+region suffix in the bucket
#: name: S3 names are globally unique, and the prefix is the auto-generated unique id that provides
#: that uniqueness now -- account/region no longer belong in the name. Defaults to 'adcp' (the
#: original instance) for a standalone run; deploy_all.py exports INSTANCE_PREFIX so orchestrated
#: runs inherit the right one.
INSTANCE_PREFIX = os.environ.get("INSTANCE_PREFIX", "adcp").strip() or "adcp"
BUCKET_NAME = f"{INSTANCE_PREFIX}-buyer-agent-ui"
OAC_NAME = f"{INSTANCE_PREFIX}-buyer-agent-ui-oac"
# The distribution is reused by EXACT Comment match (see find_existing_distribution). The prefix MUST
# be in the Comment, or a second instance would adopt the first instance's distribution and bucket.
DISTRIBUTION_COMMENT = f"AdCP buyer agent chat UI ({INSTANCE_PREFIX})"

#: The frozen single-file UI. Kept for reference and served locally by app.py at /legacy; nothing
#: here publishes it any more. See .kiro/steering/buyer-ui-react-is-canonical.md.
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

s3 = boto3.client("s3", region_name=REGION)
# CloudFront's control plane is global but only answers in us-east-1, whatever region the bucket is in.
cloudfront = boto3.client("cloudfront", region_name=CLOUDFRONT_API_REGION)


def get_or_create_bucket() -> str:
    try:
        s3.head_bucket(Bucket=BUCKET_NAME)
        print(f"Using existing bucket: {BUCKET_NAME}")
    except Exception:
        print(f"Creating bucket: {BUCKET_NAME}")
        if REGION == "us-east-1":
            s3.create_bucket(Bucket=BUCKET_NAME)
        else:
            s3.create_bucket(
                Bucket=BUCKET_NAME,
                CreateBucketConfiguration={"LocationConstraint": REGION},
            )
        s3.put_public_access_block(
            Bucket=BUCKET_NAME,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        s3.put_bucket_encryption(
            Bucket=BUCKET_NAME,
            ServerSideEncryptionConfiguration={
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
            },
        )
    return BUCKET_NAME


def get_or_create_oac() -> str:
    paginator = cloudfront.get_paginator("list_origin_access_controls")
    for page in paginator.paginate():
        for item in page["OriginAccessControlList"]["Items"]:
            if item["Name"] == OAC_NAME:
                print(f"Using existing OAC: {item['Id']}")
                return item["Id"]

    print(f"Creating OAC: {OAC_NAME}")
    response = cloudfront.create_origin_access_control(
        OriginAccessControlConfig={
            "Name": OAC_NAME,
            "Description": "OAC for the AdCP buyer agent chat UI bucket",
            "SigningProtocol": "sigv4",
            "SigningBehavior": "always",
            "OriginAccessControlOriginType": "s3",
        }
    )
    oac_id = response["OriginAccessControl"]["Id"]
    print(f"Created OAC: {oac_id}")
    return oac_id


def find_existing_distribution() -> dict | None:
    paginator = cloudfront.get_paginator("list_distributions")
    for page in paginator.paginate():
        items = page.get("DistributionList", {}).get("Items", [])
        for item in items:
            if item.get("Comment") == DISTRIBUTION_COMMENT:
                return item
    return None


def get_or_create_distribution(bucket_name: str, oac_id: str) -> tuple[str, str]:
    """Returns (distribution_id, domain_name)."""
    existing = find_existing_distribution()
    if existing:
        print(f"Using existing distribution: {existing['Id']} ({existing['DomainName']})")
        return existing["Id"], existing["DomainName"]

    origin_domain = f"{bucket_name}.s3.{REGION}.amazonaws.com"
    print(f"Creating CloudFront distribution for origin {origin_domain}")
    response = cloudfront.create_distribution(
        DistributionConfig={
            "CallerReference": f"{INSTANCE_PREFIX}-buyer-agent-ui-{int(time.time())}",
            "Comment": DISTRIBUTION_COMMENT,
            "Enabled": True,
            "DefaultRootObject": "index.html",
            "Origins": {
                "Quantity": 1,
                "Items": [
                    {
                        "Id": "ui-bucket-origin",
                        "DomainName": origin_domain,
                        "OriginAccessControlId": oac_id,
                        "S3OriginConfig": {"OriginAccessIdentity": ""},
                    }
                ],
            },
            "DefaultCacheBehavior": {
                "TargetOriginId": "ui-bucket-origin",
                "ViewerProtocolPolicy": "redirect-to-https",
                "AllowedMethods": {
                    "Quantity": 2,
                    "Items": ["GET", "HEAD"],
                    "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
                },
                # Managed-CachingOptimized: safe default for a static site;
                # config.json is fetched fresh via a short TTL override
                # below-equivalent behavior isn't set per-path here, so a
                # CloudFront invalidation (this script does one) is what
                # makes a republish visible immediately instead of waiting
                # out the cache.
                "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6",
                "Compress": True,
            },
            "CustomErrorResponses": SPA_ERROR_RESPONSES,
            "PriceClass": "PriceClass_100",
        }
    )
    dist = response["Distribution"]
    print(f"Created distribution: {dist['Id']} ({dist['DomainName']}) — status Deploying")
    return dist["Id"], dist["DomainName"]


#: Maps a missing object to the app shell, so the React router's own routes survive a refresh.
#:
#: **403 as well as 404, and this is the part that is easy to get wrong.** The origin is a private S3
#: bucket reached over OAC, and the bucket policy grants `s3:GetObject` only. Without `s3:ListBucket`,
#: S3 answers a request for a key that does not exist with **403 AccessDenied**, not 404 NoSuchKey. The
#: usual SPA recipe maps only 404, which would leave `/chat` broken while looking correctly configured.
#:
#: **Response code 200, not 404.** Returning the shell with a 404 status renders fine but tells the
#: browser, any monitor and any crawler that a real route failed. `/chat` is a real route.
#:
#: The cost, stated because it is real: a genuinely forbidden request also returns the shell. That is
#: acceptable here because everything in the bucket is meant to be served and there is no per-object
#: authorization at the edge — the app authenticates its own API calls.
#:
#: TTL 0 so a fix to a genuinely missing asset is visible without waiting out an error cache.
SPA_ERROR_RESPONSES: dict = {
    "Quantity": 2,
    "Items": [
        {
            "ErrorCode": 403,
            "ResponsePagePath": "/index.html",
            "ResponseCode": "200",
            "ErrorCachingMinTTL": 0,
        },
        {
            "ErrorCode": 404,
            "ResponsePagePath": "/index.html",
            "ResponseCode": "200",
            "ErrorCachingMinTTL": 0,
        },
    ],
}


def converge_spa_error_responses(distribution_id: str) -> None:
    """Ensure the distribution maps 403/404 to the app shell. Idempotent, run every deploy.

    This exists as its own step rather than as a field on `create_distribution` because
    `get_or_create_distribution` **returns early** when a distribution already exists. The create path
    runs once, and it has already run, so a change made only there would never reach the deployed
    distribution: the setting would look present in the code and be absent in production.

    The config is read, mutated and written back **whole**. `update_distribution` takes a complete
    `DistributionConfig` and silently drops whatever is omitted, including the OAC association that is
    the only thing letting CloudFront read the bucket. Building a fresh config here would break the
    site while reporting success.
    """
    current = cloudfront.get_distribution_config(Id=distribution_id)
    etag = current["ETag"]
    config = current["DistributionConfig"]

    existing = config.get("CustomErrorResponses", {})
    existing_items = {
        (item.get("ErrorCode"), item.get("ResponsePagePath"), str(item.get("ResponseCode")))
        for item in existing.get("Items", [])
    }
    wanted_items = {
        (item["ErrorCode"], item["ResponsePagePath"], item["ResponseCode"])
        for item in SPA_ERROR_RESPONSES["Items"]
    }
    if wanted_items.issubset(existing_items):
        print("Client-side routing already configured (403/404 -> /index.html, 200)")
        return

    print("Configuring client-side routing: 403 and 404 -> /index.html with 200")
    config["CustomErrorResponses"] = SPA_ERROR_RESPONSES
    cloudfront.update_distribution(
        Id=distribution_id,
        IfMatch=etag,
        DistributionConfig=config,
    )
    print("  updated. CloudFront will redeploy the change.")


def apply_bucket_policy(bucket_name: str, distribution_id: str) -> None:
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowCloudFrontServicePrincipalReadOnly",
                "Effect": "Allow",
                "Principal": {"Service": "cloudfront.amazonaws.com"},
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket_name}/*",
                "Condition": {
                    "StringEquals": {
                        "AWS:SourceArn": f"arn:aws:cloudfront::{ACCOUNT_ID}:distribution/{distribution_id}",
                        "AWS:SourceAccount": ACCOUNT_ID,
                    }
                },
            }
        ],
    }
    print(f"Applying bucket policy scoped to distribution {distribution_id}")
    s3.put_bucket_policy(Bucket=bucket_name, Policy=json.dumps(policy))


def build_config_json() -> dict:
    """Same shape as app.py's /config route, built from the same .env this
    process already loads — see that route's docstring for field meanings.
    """
    try:
        seller_agents = list_seller_agents_public()
        default_seller_agent_id = get_default_seller_agent_id()
    except SellerAgentError as exc:
        raise SystemExit(
            f"Cannot build config.json: {exc}\nFix SELLER_AGENTS_JSON in .env and re-run."
        ) from exc

    # The chat-selectable A2A agents, mirroring app.py's /config exactly. The
    # CloudFront-hosted UI has no /config route to call, so this file is the
    # only place it can learn them from — omit them and the agent selector is
    # empty however healthy the runtime is. Fails loudly rather than publishing
    # a UI with no agents.
    try:
        agents = list_agents_public()
        default_agent_id = get_default_agent_id()
    except AgentRegistryError as exc:
        raise SystemExit(
            f"Cannot build config.json: {exc}\n"
            "Run deploy_agents_registry.py --apply to generate AGENTS_JSON, then re-run."
        ) from exc

    # Seller display order for the journey view. Absent means "no configured preference", which is a
    # legitimate state rather than an error: sellers then keep the order the fan-out returned them in.
    # Mirrors app.py's seller_priority() so the two config shapes stay identical.
    seller_priority = [
        part.strip() for part in os.environ.get("SELLER_PRIORITY", "").split(",") if part.strip()
    ]

    required = ["AGENT_RUNTIME_ARN", "COGNITO_REGION", "COGNITO_CLIENT_ID"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise SystemExit(
            f"Cannot build config.json: missing {', '.join(missing)} in .env. "
            "Run deploy_cognito_setup.py and deploy_launch.py first."
        )

    return {
        "model_id": os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-5"),
        "cognito_region": os.environ["COGNITO_REGION"],
        "cognito_client_id": os.environ["COGNITO_CLIENT_ID"],
        "agent_runtime_arn": os.environ["AGENT_RUNTIME_ARN"],
        "aws_region": region(),
        # Shape-parity with app.py's /config. `.get` rather than `[...]`: a stack deployed before the
        # identity pool existed should publish an empty value, which the UI reads as "use the
        # /invocations actions", not fail the whole publish.
        "identity_pool_id": os.environ.get("IDENTITY_POOL_ID", ""),
        "sessions_table_name": os.environ.get("SESSIONS_TABLE_NAME", ""),
        "seller_agents": seller_agents,
        "default_seller_agent_id": default_seller_agent_id,
        "agents": agents,
        "default_agent_id": default_agent_id,
        # Which seller's journey the journey view shows first. Empty means no configured preference.
        "seller_priority": seller_priority,
        # Present and empty for shape-parity with /config, where it carries a
        # registry error the UI surfaces in the selector.
        "agents_error": "",
        # When this config was generated, which is when the site was published.
        #
        # Shown in the UI so "is the browser running the build I just deployed" is answerable by
        # looking, rather than inferred. Two rounds of debugging were spent on a stale tab
        # reported as a code fault, and a served page cannot tell you what the browser kept in
        # memory. `index.html` is uploaded `no-cache`, so this travels with a real reload.
        #
        # A real timestamp from the deploy, not a build number anyone maintains by hand.
        "ui_published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


#: Vendored browser assets served alongside `index.html` (see `static/vendor/README.md`).
#:
#: Keys this script used to publish and no longer does, removed on every run.
#:
#: The React build replaced the static UI at the site root, so the static shell's `index.html` is now
#: written from `ui/dist` and the files below belong to no deployed page:
#:
#:   * `vendor/*` was Motion vendored as a UMD bundle for the static UI's script tag. The React app
#:     takes Motion from npm through the bundler, so nothing loads these.
#:   * `react/*` was where the React build sat while the two UIs ran side by side. Leaving it would
#:     keep serving a second, diverging copy of the same app at a URL people had already bookmarked.
#:
#: Pruned rather than left orphaned: an old app still answering on a live URL is worse than a 404,
#: because it looks like the current one and reports stale behaviour as fact. Everything here is
#: regenerable build output owned by this script, so a mistaken removal is repaired by redeploying.
STALE_KEY_PREFIXES = ("vendor/", "react/")

#: The Vite build, published at the site root.
#:
#: `ui/vite.config.ts` sets `base: './'`, which is what lets the same build resolve its assets at the
#: root here and from disk under app.py, with no per-target build.

#: The Vite build output that gets published.
REACT_DIST_DIR = os.path.join(os.path.dirname(__file__), "ui", "dist")
REACT_UI_DIR = os.path.join(os.path.dirname(__file__), "ui")


def build_react() -> None:
    """Build the UI, then publish. Not the other way round, and not optional.

    This script used to publish whatever happened to be in `ui/dist`. It checked the directory
    EXISTED and never that it was CURRENT, so a stale build published silently and reported success --
    the deploy log lists the old hashed filenames, which looks entirely normal unless you already know
    which hash you expected. Caught once by comparing the served bundle's hash against a fresh local
    build; the two disagreed and the "successful" deploy had shipped the previous UI.

    That is the same failure family as a green status over a broken component: a success signal that is
    not evidence of success. The fix is for the build to be part of the deploy rather than a step a
    human is trusted to have run.

    `npm run build` runs `tsc -b` before `vite build`, so a type error fails the deploy here rather
    than shipping. Output is streamed, not captured -- a build failure's message is the whole point.
    """
    print("=== Building the React UI (npm run build) ===")
    if not os.path.isdir(os.path.join(REACT_UI_DIR, "node_modules")):
        raise SystemExit(
            f"!!! {REACT_UI_DIR}/node_modules is missing.\n"
            "    Run `npm install` in agents/buyer/reference-buyer/ui first.\n"
            "    Refusing to guess: installing dependencies mid-deploy would change the lockfile's\n"
            "    resolved tree without anyone reviewing it."
        )

    result = subprocess.run(["npm", "run", "build"], cwd=REACT_UI_DIR, check=False)
    if result.returncode != 0:
        raise SystemExit(
            f"!!! the UI build failed (exit {result.returncode}); nothing was published.\n"
            "    The previously deployed build is still live and untouched."
        )
    print("  build succeeded\n")

#: Content types for the built files, by extension. Anything not listed is uploaded as
#: `application/octet-stream`, which is wrong for a browser but visible: a missing entry shows up as
#: a file the browser refuses to execute, rather than as a silent behaviour change.
REACT_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".json": "application/json; charset=utf-8",
}


def upload_site(bucket_name: str) -> None:
    """Publishes the React build at the site root, then removes what it replaced.

    Everything under `assets/` carries a content hash in its filename, so those are cached immutably
    for a year: a new build writes new keys rather than replacing bytes at existing ones. `index.html`
    is the one file whose name never changes and whose contents point at those hashed keys, so it
    stays `no-cache`. A cached shell referring to assets that no longer exist is the one combination
    that fails silently.

    The static UI is no longer published. It stays in the repo as a frozen reference (see
    .kiro/steering/buyer-ui-react-is-canonical.md) and app.py still serves it locally at /legacy, but
    it is not part of the deployed site.
    """
    if not os.path.isdir(REACT_DIST_DIR):
        # Loud rather than skipped. Publishing nothing would leave the previous build live while
        # reporting success, which is indistinguishable from a deploy that changed nothing.
        #
        # `main()` builds before reaching here, so this now only fires under `--skip-build`.
        raise SystemExit(
            f"!!! no React build found at {REACT_DIST_DIR}.\n"
            "    Run `npm run build` in agents/buyer/reference-buyer/ui first, or drop --skip-build.\n"
            "    The React app is the deployed UI, so there is nothing to publish without it."
        )

    written: list[str] = []
    for root, _dirs, files in os.walk(REACT_DIST_DIR):
        for name in sorted(files):
            path = os.path.join(root, name)
            key = os.path.relpath(path, REACT_DIST_DIR).replace(os.sep, "/")
            extension = os.path.splitext(name)[1].lower()
            content_type = REACT_CONTENT_TYPES.get(extension, "application/octet-stream")
            # Only the hashed asset filenames are safe to cache immutably; anything whose name is
            # stable has to be revalidated or a deploy is invisible to a returning browser.
            cache_control = (
                "public, max-age=31536000, immutable"
                if key.startswith("assets/")
                else "no-cache"
            )
            print(f"Uploading {key}")
            s3.upload_file(
                path,
                bucket_name,
                key,
                ExtraArgs={"ContentType": content_type, "CacheControl": cache_control},
            )
            written.append(key)

    # Generated fresh on every publish, and read by the app as `./config.json` relative to the shell.
    # It also carries `ui_published_at`, which is how the running page can report which build it is.
    print("Uploading config.json")
    s3.put_object(
        Bucket=bucket_name,
        Key="config.json",
        Body=json.dumps(build_config_json(), indent=2).encode("utf-8"),
        ContentType="application/json",
        CacheControl="no-cache",
    )
    written.append("config.json")

    written.extend(prune_stale_keys(bucket_name))

    global _published_keys
    _published_keys = written


def prune_stale_keys(bucket_name: str) -> list[str]:
    """Deletes objects this script used to publish, returning the keys removed.

    See STALE_KEY_PREFIXES for what and why. Returned so the caller can invalidate them: a deleted
    object still answers from an edge cache until the cached copy expires, so removing it from S3
    without invalidating leaves the old app reachable for as long as the TTL.
    """
    removed: list[str] = []
    for prefix in STALE_KEY_PREFIXES:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket_name, Prefix=prefix):
            keys = [item["Key"] for item in page.get("Contents", [])]
            if not keys:
                continue
            for key in keys:
                print(f"Removing superseded object {key}")
            s3.delete_objects(
                Bucket=bucket_name,
                Delete={"Objects": [{"Key": key} for key in keys], "Quiet": True},
            )
            removed.extend(keys)
    if not removed:
        print("No superseded objects to remove")
    return removed


#: Keys written or removed by the last publish, so the invalidation covers exactly what moved.
#:
#: Module state rather than a threaded return value, because `upload_site` sits in a sequence this
#: script does not otherwise pass values along. A wildcard invalidation would cost the same and tell
#: you nothing about what changed.
_published_keys: list[str] = []


def invalidate_distribution(distribution_id: str) -> None:
    # Built from what this run actually published and removed, rather than a hardcoded list.
    #
    # The hashed assets under `assets/` do not strictly need invalidating, since a new build writes
    # new keys. `index.html` and `config.json` do: their names are stable and their contents change
    # on every deploy. Deleted keys need it most of all, because an object removed from S3 keeps
    # answering from an edge cache until its cached copy expires.
    paths = sorted({f"/{key}" for key in _published_keys} | {"/", "/index.html", "/config.json"})
    print(f"Invalidating CloudFront cache for distribution {distribution_id}")
    for path in paths:
        print(f"  {path}")
    cloudfront.create_invalidation(
        DistributionId=distribution_id,
        InvalidationBatch={
            "Paths": {"Quantity": len(paths), "Items": paths},
            "CallerReference": f"deploy-ui-{int(time.time())}",
        },
    )


def wait_for_deployed(distribution_id: str, timeout_seconds: int = 600) -> None:
    print("Waiting for distribution to reach Deployed status (this can take several minutes)...")
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        status = cloudfront.get_distribution(Id=distribution_id)["Distribution"]["Status"]
        if status == "Deployed":
            print("Distribution is Deployed.")
            return
        time.sleep(15)
    print(
        f"Timed out after {timeout_seconds}s waiting for Deployed status. "
        "It will finish deploying in the background; check the console."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish the buyer agent UI to S3 + CloudFront.")
    parser.add_argument(
        "--origin-only",
        action="store_true",
        help=(
            "create/converge the bucket, OAC and distribution, then stop without publishing. "
            "The origin depends on nothing, while publishing needs AGENT_RUNTIME_ARN for config.json "
            "-- so the origin can be created early and its URL handed to everything that references it."
        ),
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help=(
            "publish whatever is already in ui/dist instead of rebuilding. Only for republishing a "
            "known-good build; a stale dist/ publishes silently and reports success."
        ),
    )
    args = parser.parse_args()

    # Built here rather than relied upon, unless explicitly skipped. See build_react().
    if not args.origin_only and not args.skip_build:
        build_react()

    bucket_name = get_or_create_bucket()
    oac_id = get_or_create_oac()
    distribution_id, domain_name = get_or_create_distribution(bucket_name, oac_id)
    # Converged on every run, not only at creation: get_or_create_distribution returns early for an
    # existing distribution, so this is the only path that reaches one that already exists.
    converge_spa_error_responses(distribution_id)
    apply_bucket_policy(bucket_name, distribution_id)

    if args.origin_only:
        print("\n=== UI origin ready (nothing published) ===")
        print(f"Bucket:          {bucket_name}")
        print(f"Distribution ID: {distribution_id}")
        print(f"Origin:          https://{domain_name}")
        return

    upload_site(bucket_name)
    invalidate_distribution(distribution_id)
    wait_for_deployed(distribution_id)

    print("\n=== UI deployed ===")
    print(f"Bucket:              {bucket_name}")
    print(f"Distribution ID:     {distribution_id}")
    print(f"URL:                 https://{domain_name}/")
    print(f"Journey view:        https://{domain_name}/")
    print(f"Chat view:           https://{domain_name}/chat")
    print(
        "\nVerify client-side routing by REFRESHING on /chat, not by clicking through to it.\n"
        "  A first navigation is handled in the browser and never reaches CloudFront, so clicking\n"
        "  works even when the distribution is misconfigured. Only a refresh or a pasted link\n"
        "  exercises the 403-to-index.html rewrite this script converges."
    )


if __name__ == "__main__":
    main()
