#!/usr/bin/env python3
"""Tear down one AdCP reference-agent instance deployed by Phase 9 of deploy-ecosystem.sh.

Phase 9 runs ``external-agents/adcp/deploy_all.py --prefix <instance-prefix>``. That driver provisions
its own fleet, every name derived from the instance prefix, and ships no teardown of its own. This
script is that teardown. It removes, for one instance prefix ``P``:

  CloudFormation   AgentCore-<P>RefSeller-default, AgentCore-<P>RefGovernance-default
                   (each owns a runtime, an ECR repo, a CodeBuild project, Lambdas and roles)
  AgentCore        runtimes <P>_buyer_agent and <P>_buyer_agent_a2a (toolkit-launched, no stack),
                   OAuth2 credential provider <P>-agents-m2m
  ECR              bedrock-agentcore-<P>_buyer_agent*, <p>refseller/refseller, <p>refgovernance/...
  CodeBuild        every project whose name contains <P>
  CloudFront       the two distributions whose comment ends in "(<P>)", then the OACs
                   <P>-buyer-agent-ui-oac and <P>-reference-governance-oac
  S3               <P>-buyer-agent-ui, <P>-buyer-agent-sessions, <P>-reference-governance
  DynamoDB         <P>-buyer-agent-sessions, <P>-reference-seller-state, <P>-reference-governance-state
  Cognito          identity pool <P>_buyer_agent_ui; user pool <P>-buyer-agent-users and its domain
  IAM              <P>-buyer-agent-agentcore-execution, <P>-buyer-agent-ui-reader
  Secrets Manager  adcp/cognito/<P>-agents-m2m
  SSM              /<stack-prefix>/a2a-inbound-tokens/<unique-id>/AdCPBuyer and /AdCPBuyerAgent
                   (written by wire_adcp_agents.py; needs --stack-prefix and --unique-id)
  CloudWatch Logs  runtime, CodeBuild and Lambda log groups whose name contains <P>
  KMS              alias/<P>-governance-signing -- ONLY with --delete-kms-key, because scheduling a
                   key for deletion is irreversible after the waiting period
  Local files      the instance's entry in external-agents/adcp/deployments.local.json, and the
                   AdCPBuyer / AdCPBuyerAgent entries in global_configuration.json (--config)

Resource names are taken from the deploy scripts under external-agents/adcp/agents/*/deploy_*.py.
If one of those renames something, update the matching constant here.

Every step is independent: a failure is recorded and the run continues, so one stuck resource does
not leave the rest billing. The exit code is non-zero if anything failed.

Usage:
    cleanup_adcp_agents.py --prefix a4avn39cj --region us-east-1 --dry-run
    cleanup_adcp_agents.py --prefix a4avn39cj --region us-east-1 --stack-prefix a4a --unique-id vn39cj --yes
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, List, Optional

try:
    import boto3
    from botocore.exceptions import ClientError, WaiterError
except ImportError:  # pragma: no cover
    print("boto3 is required: pip install boto3", file=sys.stderr)
    sys.exit(2)


# Same rule as deploy_all.resolve_instance_prefix: lowercase alphanumeric, leading letter, <=20.
_PREFIX_RE = re.compile(r"^[a-z][a-z0-9]{0,19}$")

# Names written by scripts/wire_adcp_agents.py.
WIRE_TOP_LEVEL_ID = "AdCPBuyer"
WIRE_EXTERNAL_ENTRY = "AdCPBuyerAgent"
WIRE_CONSUMER_AGENT = "AgencyAgent"

KMS_PENDING_WINDOW_DAYS = 7

# Error codes that mean the resource is already gone, which is the outcome we wanted.
_NOT_FOUND_CODES = {
    "ResourceNotFoundException", "NoSuchEntity", "NoSuchBucket", "NoSuchDistribution",
    "NoSuchOriginAccessControl", "NotFoundException", "RepositoryNotFoundException",
    "ParameterNotFound",
}


def _log(msg: str) -> None:
    print(msg, flush=True)


class Cleanup:
    def __init__(self, prefix: str, region: str, profile: Optional[str], dry_run: bool) -> None:
        self.p = prefix
        self.region = region
        self.dry_run = dry_run
        self.session = boto3.Session(profile_name=profile) if profile else boto3.Session()
        self.failures: List[str] = []
        self.removed: List[str] = []
        self.account = self.session.client("sts").get_caller_identity()["Account"]

    # ----------------------------------------------------------------------------------------- utils
    def client(self, name: str, **kw):
        return self.session.client(name, region_name=self.region, **kw)

    def _do(self, label: str, fn: Callable[[], None]) -> bool:
        """Run one deletion, recording success or failure. Returns True on success."""
        if self.dry_run:
            _log(f"  [dry-run] would delete {label}")
            return True
        try:
            fn()
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in _NOT_FOUND_CODES or (code == "ValidationError" and "does not exist" in str(exc)):
                _log(f"  - {label}: already gone")
                return True
            self.failures.append(f"{label}: {code or exc}")
            _log(f"  ❌ {label}: {exc}")
            return False
        except Exception as exc:  # noqa: BLE001
            self.failures.append(f"{label}: {exc}")
            _log(f"  ❌ {label}: {exc}")
            return False
        self.removed.append(label)
        _log(f"  ✅ deleted {label}")
        return True

    @staticmethod
    def _paginate(client, op: str, key: str, **kw) -> Iterable[dict]:
        if client.can_paginate(op):
            for page in client.get_paginator(op).paginate(**kw):
                yield from page.get(key, [])
            return
        # Manual NextToken loop for APIs without a boto3 paginator.
        token: Optional[str] = None
        while True:
            args = dict(kw)
            if token:
                args["NextToken"] = token
            page = getattr(client, op)(**args)
            yield from page.get(key, [])
            token = page.get("NextToken")
            if not token:
                return

    # ------------------------------------------------------------------------- 1. CloudFormation stacks
    def stack_names(self) -> List[str]:
        return [f"AgentCore-{self.p}RefSeller-default", f"AgentCore-{self.p}RefGovernance-default"]

    def stack_ecr_repos(self) -> List[str]:
        low = self.p.lower()
        return [f"{low}refseller/refseller", f"{low}refgovernance/refgovernance"]

    def delete_stacks(self) -> None:
        _log("\n1️⃣  CloudFormation stacks (seller + governance runtimes)")
        cfn = self.client("cloudformation")
        ecr = self.client("ecr")

        # The stacks own their ECR repos without EmptyOnDelete, so a repo with images blocks deletion.
        for repo in self.stack_ecr_repos():
            self._empty_ecr_repo(ecr, repo)

        present: List[str] = []
        for name in self.stack_names():
            try:
                status = cfn.describe_stacks(StackName=name)["Stacks"][0]["StackStatus"]
            except ClientError as exc:
                if "does not exist" in str(exc):
                    _log(f"  - stack {name}: already gone")
                    continue
                raise
            if status == "DELETE_IN_PROGRESS":
                _log(f"  stack {name} is already deleting; will wait")
                present.append(name)
                continue
            mode = "FORCE_DELETE_STACK" if status == "DELETE_FAILED" else None

            def _delete(n=name, m=mode):
                kw = {"StackName": n}
                if m:
                    kw["DeletionMode"] = m
                cfn.delete_stack(**kw)

            if self._do(f"stack {name} ({status})", _delete):
                present.append(name)

        if self.dry_run:
            return
        for name in present:
            _log(f"  waiting for {name} to delete...")
            try:
                cfn.get_waiter("stack_delete_complete").wait(
                    StackName=name, WaiterConfig={"Delay": 15, "MaxAttempts": 120}
                )
                _log(f"  ✅ stack {name} deleted")
            except WaiterError:
                # One retry in force mode: it skips resources CloudFormation cannot delete itself.
                try:
                    status = cfn.describe_stacks(StackName=name)["Stacks"][0]["StackStatus"]
                except ClientError:
                    _log(f"  ✅ stack {name} deleted")
                    continue
                if status == "DELETE_FAILED":
                    _log(f"  stack {name} is DELETE_FAILED; retrying with FORCE_DELETE_STACK")
                    try:
                        cfn.delete_stack(StackName=name, DeletionMode="FORCE_DELETE_STACK")
                        cfn.get_waiter("stack_delete_complete").wait(
                            StackName=name, WaiterConfig={"Delay": 15, "MaxAttempts": 80}
                        )
                        _log(f"  ✅ stack {name} force-deleted")
                        continue
                    except (ClientError, WaiterError) as exc:
                        self.failures.append(f"stack {name}: {exc}")
                        _log(f"  ❌ stack {name} still present: {exc}")
                        continue
                self.failures.append(f"stack {name}: ended in {status}")
                _log(f"  ❌ stack {name} ended in {status}")

    def _empty_ecr_repo(self, ecr, repo: str) -> None:
        try:
            ids = []
            for page in ecr.get_paginator("list_images").paginate(repositoryName=repo):
                ids.extend(page.get("imageIds", []))
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "RepositoryNotFoundException":
                return
            raise
        if not ids:
            return
        if self.dry_run:
            _log(f"  [dry-run] would delete {len(ids)} images from ECR {repo}")
            return
        for i in range(0, len(ids), 100):
            ecr.batch_delete_image(repositoryName=repo, imageIds=ids[i:i + 100])
        _log(f"  emptied ECR {repo} ({len(ids)} images)")

    # ---------------------------------------------------------------------- 2. AgentCore runtimes etc.
    def delete_runtimes(self) -> None:
        _log("\n2️⃣  AgentCore runtimes and credential provider")
        ac = self.client("bedrock-agentcore-control")
        runtimes = [
            r for r in self._paginate(ac, "list_agent_runtimes", "agentRuntimes")
            if r["agentRuntimeName"].startswith(self.p)
        ]
        if not runtimes:
            _log("  - no runtimes found")
        for r in runtimes:
            rid, rname = r["agentRuntimeId"], r["agentRuntimeName"]
            self._do(f"runtime {rname}", lambda rid=rid: ac.delete_agent_runtime(agentRuntimeId=rid))

        provider = f"{self.p}-agents-m2m"
        try:
            names = {
                c["name"] for c in self._paginate(ac, "list_oauth2_credential_providers", "credentialProviders")
            }
        except ClientError as exc:
            self.failures.append(f"list credential providers: {exc}")
            names = set()
        if provider in names:
            self._do(
                f"OAuth2 credential provider {provider}",
                lambda: ac.delete_oauth2_credential_provider(name=provider),
            )
        else:
            _log(f"  - credential provider {provider}: already gone")

    def delete_ecr_and_codebuild(self) -> None:
        _log("\n3️⃣  ECR repositories and CodeBuild projects")
        ecr = self.client("ecr")
        low = self.p.lower()
        repos = [
            r["repositoryName"] for r in self._paginate(ecr, "describe_repositories", "repositories")
            if low in r["repositoryName"].lower()
        ]
        if not repos:
            _log("  - no ECR repositories found")
        for repo in repos:
            self._do(f"ECR repository {repo}", lambda repo=repo: ecr.delete_repository(repositoryName=repo, force=True))

        cb = self.client("codebuild")
        projects = [n for n in self._paginate(cb, "list_projects", "projects") if self.p in n]
        if not projects:
            _log("  - no CodeBuild projects found")
        for name in projects:
            self._do(f"CodeBuild project {name}", lambda name=name: cb.delete_project(name=name))

    # ------------------------------------------------------------------------------- 4. CloudFront
    def delete_cloudfront(self) -> None:
        _log("\n4️⃣  CloudFront distributions and origin access controls")
        cf = self.client("cloudfront")
        suffix = f"({self.p})"
        dists: List[dict] = []
        # list_distributions nests Items under DistributionList; walk pages by hand.
        for page in cf.get_paginator("list_distributions").paginate():
            for d in page.get("DistributionList", {}).get("Items", []):
                comment = d.get("Comment", "")
                if comment.startswith("AdCP") and comment.endswith(suffix):
                    dists.append(d)
        if not dists:
            _log("  - no distributions found")

        # Disable all first so the waits overlap, then delete.
        to_delete = []
        for d in dists:
            did = d["Id"]
            label = f"CloudFront distribution {did} '{d['Comment']}'"
            if self.dry_run:
                _log(f"  [dry-run] would disable and delete {label}")
                continue
            try:
                cfg = cf.get_distribution_config(Id=did)
                if cfg["DistributionConfig"]["Enabled"]:
                    cfg["DistributionConfig"]["Enabled"] = False
                    cf.update_distribution(Id=did, DistributionConfig=cfg["DistributionConfig"], IfMatch=cfg["ETag"])
                    _log(f"  disabling {label}")
                to_delete.append((did, label))
            except ClientError as exc:
                self.failures.append(f"{label}: {exc}")
                _log(f"  ❌ {label}: {exc}")
        for did, label in to_delete:
            _log(f"  waiting for {did} to finish deploying (this can take several minutes)...")
            try:
                cf.get_waiter("distribution_deployed").wait(Id=did, WaiterConfig={"Delay": 30, "MaxAttempts": 60})
                etag = cf.get_distribution_config(Id=did)["ETag"]
                self._do(label, lambda did=did, etag=etag: cf.delete_distribution(Id=did, IfMatch=etag))
            except (ClientError, WaiterError) as exc:
                self.failures.append(f"{label}: {exc}")
                _log(f"  ❌ {label}: {exc}")

        oac_names = {f"{self.p}-buyer-agent-ui-oac", f"{self.p}-reference-governance-oac"}
        found = False
        marker: Optional[str] = None
        while True:
            kw = {"Marker": marker} if marker else {}
            page = cf.list_origin_access_controls(**kw)["OriginAccessControlList"]
            for o in page.get("Items", []):
                if o["Name"] in oac_names:
                    found = True
                    oid = o["Id"]

                    def _del(oid=oid):
                        etag = cf.get_origin_access_control(Id=oid)["ETag"]
                        cf.delete_origin_access_control(Id=oid, IfMatch=etag)

                    self._do(f"origin access control {o['Name']}", _del)
            if not page.get("IsTruncated"):
                break
            marker = page.get("NextMarker")
        if not found:
            _log("  - no origin access controls found")

    # ------------------------------------------------------------------------------------- 5. S3
    def bucket_names(self) -> List[str]:
        return [f"{self.p}-buyer-agent-ui", f"{self.p}-buyer-agent-sessions", f"{self.p}-reference-governance"]

    def delete_buckets(self) -> None:
        _log("\n5️⃣  S3 buckets")
        s3 = self.client("s3")
        for name in self.bucket_names():
            try:
                s3.head_bucket(Bucket=name)
            except ClientError:
                _log(f"  - bucket {name}: already gone")
                continue

            def _del(name=name):
                # Versions and delete markers both have to go before the bucket will delete.
                paginator = s3.get_paginator("list_object_versions")
                for page in paginator.paginate(Bucket=name):
                    objs = [{"Key": v["Key"], "VersionId": v["VersionId"]}
                            for k in ("Versions", "DeleteMarkers") for v in page.get(k, [])]
                    for i in range(0, len(objs), 1000):
                        s3.delete_objects(Bucket=name, Delete={"Objects": objs[i:i + 1000], "Quiet": True})
                s3.delete_bucket(Bucket=name)

            self._do(f"bucket {name}", _del)

    # -------------------------------------------------------------------------------- 6. DynamoDB
    def table_names(self) -> List[str]:
        return [
            f"{self.p}-buyer-agent-sessions",
            f"{self.p}-reference-seller-state",
            f"{self.p}-reference-governance-state",
        ]

    def delete_tables(self) -> None:
        _log("\n6️⃣  DynamoDB tables")
        ddb = self.client("dynamodb")
        for name in self.table_names():
            try:
                desc = ddb.describe_table(TableName=name)["Table"]
            except ClientError:
                _log(f"  - table {name}: already gone")
                continue

            def _del(name=name, protected=desc.get("DeletionProtectionEnabled", False)):
                if protected:
                    ddb.update_table(TableName=name, DeletionProtectionEnabled=False)
                ddb.delete_table(TableName=name)

            self._do(f"table {name}", _del)

    # --------------------------------------------------------------------------------- 7. Cognito
    def delete_cognito(self) -> None:
        _log("\n7️⃣  Cognito identity pool and user pool")
        ci = self.client("cognito-identity")
        pool_name = f"{self.p}_buyer_agent_ui"
        ids = [
            p["IdentityPoolId"] for p in self._paginate(ci, "list_identity_pools", "IdentityPools", MaxResults=60)
            if p["IdentityPoolName"] == pool_name
        ]
        if not ids:
            _log(f"  - identity pool {pool_name}: already gone")
        for pid in ids:
            self._do(f"identity pool {pool_name} ({pid})", lambda pid=pid: ci.delete_identity_pool(IdentityPoolId=pid))

        cup = self.client("cognito-idp")
        up_name = f"{self.p}-buyer-agent-users"
        up_ids = [
            u["Id"] for u in self._paginate(cup, "list_user_pools", "UserPools", MaxResults=60)
            if u["Name"] == up_name
        ]
        if not up_ids:
            _log(f"  - user pool {up_name}: already gone")
        for uid in up_ids:
            def _del(uid=uid):
                pool = cup.describe_user_pool(UserPoolId=uid)["UserPool"]
                for key in ("CustomDomain", "Domain"):
                    domain = pool.get(key)
                    if domain:
                        cup.delete_user_pool_domain(Domain=domain, UserPoolId=uid)
                        _log(f"    removed hosted-UI domain {domain}")
                        # Domain deletion is asynchronous; the pool delete fails while it is pending.
                        # describe_user_pool_domain returns an empty description once it is gone.
                        for _ in range(30):
                            desc = cup.describe_user_pool_domain(Domain=domain).get("DomainDescription", {})
                            if not desc.get("UserPoolId"):
                                break
                            time.sleep(5)
                if pool.get("DeletionProtection") == "ACTIVE":
                    cup.update_user_pool(UserPoolId=uid, DeletionProtection="INACTIVE")
                cup.delete_user_pool(UserPoolId=uid)

            self._do(f"user pool {up_name} ({uid})", _del)

    # ------------------------------------------------------------------------------------- 8. IAM
    def role_names(self) -> List[str]:
        return [f"{self.p}-buyer-agent-agentcore-execution", f"{self.p}-buyer-agent-ui-reader"]

    def delete_roles(self) -> None:
        _log("\n8️⃣  IAM roles")
        iam = self.client("iam")
        for name in self.role_names():
            try:
                iam.get_role(RoleName=name)
            except ClientError:
                _log(f"  - role {name}: already gone")
                continue

            def _del(name=name):
                for pol in iam.list_attached_role_policies(RoleName=name).get("AttachedPolicies", []):
                    iam.detach_role_policy(RoleName=name, PolicyArn=pol["PolicyArn"])
                for pname in iam.list_role_policies(RoleName=name).get("PolicyNames", []):
                    iam.delete_role_policy(RoleName=name, PolicyName=pname)
                for prof in iam.list_instance_profiles_for_role(RoleName=name).get("InstanceProfiles", []):
                    iam.remove_role_from_instance_profile(
                        InstanceProfileName=prof["InstanceProfileName"], RoleName=name
                    )
                iam.delete_role(RoleName=name)

            self._do(f"role {name}", _del)

    # ------------------------------------------------------------------------ 9. Secrets and SSM
    def delete_secrets_and_ssm(self, stack_prefix: Optional[str], unique_id: Optional[str]) -> None:
        _log("\n9️⃣  Secrets Manager secret and SSM parameters")
        sm = self.client("secretsmanager")
        secret = f"adcp/cognito/{self.p}-agents-m2m"
        self._do(
            f"secret {secret}",
            lambda: sm.delete_secret(SecretId=secret, ForceDeleteWithoutRecovery=True),
        )

        if not (stack_prefix and unique_id):
            _log("  - SSM credential parameters: skipped (--stack-prefix/--unique-id not given)")
            return
        ssm = self.client("ssm")
        for agent in (WIRE_TOP_LEVEL_ID, WIRE_EXTERNAL_ENTRY):
            path = f"/{stack_prefix}/a2a-inbound-tokens/{unique_id}/{agent}"
            self._do(f"SSM parameter {path}", lambda path=path: ssm.delete_parameter(Name=path))

    # ------------------------------------------------------------------------------------ 10. Logs
    def delete_log_groups(self) -> None:
        _log("\n🔟 CloudWatch log groups")
        logs = self.client("logs")
        found = []
        for prefix in ("/aws/bedrock-agentcore/runtimes/", "/aws/codebuild/", "/aws/lambda/AgentCore-"):
            for g in self._paginate(logs, "describe_log_groups", "logGroups", logGroupNamePrefix=prefix):
                if self.p in g["logGroupName"]:
                    found.append(g["logGroupName"])
        if not found:
            _log("  - no log groups found")
        for name in found:
            self._do(f"log group {name}", lambda name=name: logs.delete_log_group(logGroupName=name))

    # ------------------------------------------------------------------------------------- 11. KMS
    def kms_alias(self) -> str:
        return f"alias/{self.p}-governance-signing"

    def handle_kms(self, delete_key: bool) -> None:
        _log("\n1️⃣1️⃣  KMS governance signing key")
        kms = self.client("kms")
        alias = self.kms_alias()
        try:
            meta = kms.describe_key(KeyId=alias)["KeyMetadata"]
        except ClientError:
            _log(f"  - {alias}: already gone")
            return
        key_id, state = meta["KeyId"], meta["KeyState"]
        if state == "PendingDeletion":
            _log(f"  - key {key_id} ({alias}) is already pending deletion")
            return
        if not delete_key:
            _log(f"  ⚠️  RETAINED: key {key_id} ({alias}). Scheduling a KMS key for deletion is")
            _log(f"      irreversible once the {KMS_PENDING_WINDOW_DAYS}-day window passes, so it is only removed")
            _log("      with --delete-kms-key. It costs about $1/month while it exists.")
            return

        def _del():
            kms.delete_alias(AliasName=alias)
            kms.schedule_key_deletion(KeyId=key_id, PendingWindowInDays=KMS_PENDING_WINDOW_DAYS)

        self._do(f"KMS key {key_id} ({alias}, {KMS_PENDING_WINDOW_DAYS}-day pending window)", _del)

    # ----------------------------------------------------------------------------- 12. Local files
    def unregister_local(self, manifest: Optional[Path], config: Optional[Path],
                         dynamodb_table: Optional[str]) -> None:
        _log("\n1️⃣2️⃣  Local records")
        if manifest and manifest.exists():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                before = len(data.get("instances", []))
                data["instances"] = [i for i in data.get("instances", []) if i.get("prefix") != self.p]
                if len(data["instances"]) != before:
                    if self.dry_run:
                        _log(f"  [dry-run] would remove '{self.p}' from {manifest}")
                    else:
                        manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                        _log(f"  ✅ removed '{self.p}' from {manifest.name}")
                else:
                    _log(f"  - {manifest.name}: no entry for '{self.p}'")
            except (OSError, ValueError) as exc:
                self.failures.append(f"{manifest}: {exc}")
                _log(f"  ❌ {manifest}: {exc}")

        if config and config.exists():
            try:
                data = json.loads(config.read_text(encoding="utf-8"))
                changed = self._unwire(data)
                if not changed:
                    _log(f"  - {config.name}: no AdCP entries to remove")
                elif self.dry_run:
                    _log(f"  [dry-run] would remove {WIRE_TOP_LEVEL_ID}/{WIRE_EXTERNAL_ENTRY} from {config}")
                else:
                    config.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
                    _log(f"  ✅ removed {WIRE_TOP_LEVEL_ID} and {WIRE_EXTERNAL_ENTRY} from {config.name}")
                    if dynamodb_table:
                        self._do(
                            f"AdCP entries in GLOBAL_CONFIG/v1 ({dynamodb_table})",
                            lambda: self._unwire_dynamodb(dynamodb_table),
                        )
            except (OSError, ValueError) as exc:
                self.failures.append(f"{config}: {exc}")
                _log(f"  ❌ {config}: {exc}")

    def _owns(self, entry: dict, *keys: str) -> bool:
        """True if the entry's runtime ARN names this instance's buyer runtime.

        The config holds one AdCP wiring at a time, but several AdCP instances can exist in an account.
        Cleaning an old instance must not strip the wiring that points at the live one.
        """
        marker = f"/{self.p}_buyer_agent"
        return any(marker in str(entry.get(k, "")) for k in keys)

    def _unwire(self, data: dict) -> bool:
        """Reverse wire_adcp_agents.py's two upserts for THIS prefix. Returns True if anything changed."""
        changed = False
        agents = data.get("agent_configs") or {}
        top = agents.get(WIRE_TOP_LEVEL_ID)
        if top is not None:
            if self._owns(top, "agent_endpoint", "runtime_arn"):
                agents.pop(WIRE_TOP_LEVEL_ID)
                changed = True
            else:
                _log(f"  - {WIRE_TOP_LEVEL_ID} entry points at a different instance; left in place")
        consumer = agents.get(WIRE_CONSUMER_AGENT) or {}
        entries = consumer.get("external_agent_configs") or []
        kept = []
        for e in entries:
            if str(e.get("name", "")).strip() != WIRE_EXTERNAL_ENTRY:
                kept.append(e)
            elif self._owns(e, "arn", "runtime_arn"):
                changed = True
            else:
                _log(f"  - {WIRE_EXTERNAL_ENTRY} entry points at a different instance; left in place")
                kept.append(e)
        if len(kept) != len(entries):
            consumer["external_agent_configs"] = kept
            tools = consumer.get("tool_agent_names") or []
            consumer["tool_agent_names"] = [t for t in tools if t != WIRE_EXTERNAL_ENTRY]
            (data.get("configured_colors") or {}).pop(WIRE_EXTERNAL_ENTRY, None)
        if WIRE_TOP_LEVEL_ID not in agents:
            (data.get("configured_colors") or {}).pop(WIRE_TOP_LEVEL_ID, None)
        return changed

    def _unwire_dynamodb(self, table_name: str) -> None:
        table = self.session.resource("dynamodb", region_name=self.region).Table(table_name)
        item = table.get_item(Key={"pk": "GLOBAL_CONFIG", "sk": "v1"}).get("Item")
        if not item:
            return
        data = json.loads(item["content"])
        if self._unwire(data):
            table.put_item(Item={"pk": "GLOBAL_CONFIG", "sk": "v1", "content": json.dumps(data)})

    # --------------------------------------------------------------------------------- inventory
    def inventory(self) -> List[str]:
        """Cheap existence checks so the operator sees what will go before confirming."""
        found: List[str] = []
        cfn = self.client("cloudformation")
        for n in self.stack_names():
            try:
                cfn.describe_stacks(StackName=n)
                found.append(f"CloudFormation stack {n}")
            except ClientError:
                pass
        ac = self.client("bedrock-agentcore-control")
        for r in self._paginate(ac, "list_agent_runtimes", "agentRuntimes"):
            if r["agentRuntimeName"].startswith(self.p):
                found.append(f"AgentCore runtime {r['agentRuntimeName']}")
        s3 = self.client("s3")
        for b in self.bucket_names():
            try:
                s3.head_bucket(Bucket=b)
                found.append(f"S3 bucket {b}")
            except ClientError:
                pass
        ddb = self.client("dynamodb")
        for t in self.table_names():
            try:
                ddb.describe_table(TableName=t)
                found.append(f"DynamoDB table {t}")
            except ClientError:
                pass
        cup = self.client("cognito-idp")
        for u in self._paginate(cup, "list_user_pools", "UserPools", MaxResults=60):
            if u["Name"] == f"{self.p}-buyer-agent-users":
                found.append(f"Cognito user pool {u['Name']} ({u['Id']})")
        cf = self.client("cloudfront")
        for page in cf.get_paginator("list_distributions").paginate():
            for d in page.get("DistributionList", {}).get("Items", []):
                if d.get("Comment", "").endswith(f"({self.p})"):
                    found.append(f"CloudFront distribution {d['Id']} '{d['Comment']}'")
        try:
            meta = self.client("kms").describe_key(KeyId=self.kms_alias())["KeyMetadata"]
            found.append(f"KMS key {meta['KeyId']} ({self.kms_alias()}, {meta['KeyState']})")
        except ClientError:
            pass
        return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prefix", required=True, help="AdCP instance prefix (e.g. a4avn39cj)")
    ap.add_argument("--region", required=True)
    ap.add_argument("--profile", default=None)
    ap.add_argument("--stack-prefix", default=None, help="deploy-ecosystem.sh stack prefix (for SSM paths)")
    ap.add_argument("--unique-id", default=None, help="deploy-ecosystem.sh unique id (for SSM paths)")
    ap.add_argument("--config", type=Path, default=None, help="global_configuration.json to unwire")
    ap.add_argument("--dynamodb-table", default=None, help="AgentConfig table to unwire (needs --config)")
    ap.add_argument("--manifest", type=Path, default=None,
                    help="external-agents/adcp/deployments.local.json to drop the instance from")
    ap.add_argument("--delete-kms-key", action="store_true",
                    help=f"schedule the governance signing key for deletion ({KMS_PENDING_WINDOW_DAYS}-day window)")
    ap.add_argument("--dry-run", action="store_true", help="list what would be deleted; change nothing")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args()

    if not _PREFIX_RE.match(args.prefix):
        print(f"--prefix {args.prefix!r} is not a valid AdCP instance prefix "
              "(lowercase letters/digits, leading letter, <=20 chars)", file=sys.stderr)
        return 2

    c = Cleanup(args.prefix, args.region, args.profile, args.dry_run)
    _log(f"AdCP instance prefix: {args.prefix}   account: {c.account}   region: {args.region}")

    found = c.inventory()
    if not found:
        _log("Nothing found for this prefix in AWS. Checking local records only.")
    else:
        _log("Found:")
        for f in found:
            _log(f"  - {f}")
        if not args.dry_run and not args.yes:
            resp = input(f"\nDelete every AdCP resource for instance '{args.prefix}'? (y/N): ").strip()
            if resp.lower() != "y":
                _log("Cancelled.")
                return 0

    if found:
        c.delete_stacks()
        c.delete_runtimes()
        c.delete_ecr_and_codebuild()
        c.delete_cloudfront()
        c.delete_buckets()
        c.delete_tables()
        c.delete_cognito()
        c.delete_roles()
        c.delete_secrets_and_ssm(args.stack_prefix, args.unique_id)
        c.delete_log_groups()
        c.handle_kms(args.delete_kms_key)
    c.unregister_local(args.manifest, args.config, args.dynamodb_table)

    _log("")
    if c.failures:
        _log(f"⚠️  AdCP cleanup for '{args.prefix}' finished with {len(c.failures)} failure(s):")
        for f in c.failures:
            _log(f"  - {f}")
        return 1
    _log(f"✅ AdCP cleanup for '{args.prefix}' complete"
         + (" (dry run, nothing changed)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
