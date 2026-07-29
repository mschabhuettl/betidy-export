#!/usr/bin/env python3
"""
Extract all of your data from the BeTidy app (io.betidy.BeTidy).

BeTidy has no export feature, but the app is a thin client over an AWS Amplify
backend: it authenticates against an AWS Cognito user pool and reads/writes its
data through an AppSync GraphQL API. This script logs in with your own e-mail and
password (Cognito SRP), resolves your identity id, and pulls every record that
belongs to you — tasks, projects, completion history and your profile — into a
single betidy_export.json.

The Cognito/AppSync identifiers below are public client configuration extracted
from the (freely downloadable) APK; they are identical for every BeTidy user and
are not secret. Your credentials are read from the environment and never stored.

Usage:
    export BETIDY_EMAIL="you@example.com"
    export BETIDY_PASSWORD="your-password"
    python betidy_extract.py                # writes betidy_export.json

See docs/how-it-works.md for the reverse-engineering details.
"""

import json
import os

import boto3
import requests
from botocore import UNSIGNED
from botocore.config import Config
from pycognito import Cognito

# --- BeTidy backend (public app config, extracted from the APK) ---
REGION = "eu-central-1"
USER_POOL = "eu-central-1_M3hzKkFkb"
CLIENT_ID = "50io20n8v16d0a1p26076gn8vs"
IDENTITY_POOL = "eu-central-1:25cc0682-e76c-4938-b771-489b7067c75e"
APPSYNC_URL = "https://22ld3xjokjfbnchtehtunjg2im.appsync-api.eu-central-1.amazonaws.com/graphql"

OUTFILE = os.environ.get("BETIDY_OUTFILE", "betidy_export.json")

try:
    EMAIL = os.environ["BETIDY_EMAIL"]
    PASSWORD = os.environ["BETIDY_PASSWORD"]
except KeyError as e:
    raise SystemExit(f"Missing env var {e}. Set BETIDY_EMAIL and BETIDY_PASSWORD.") from None


def authenticate():
    """Cognito SRP login. The public auth operations need no AWS credentials, so the
    boto3 client is configured to send unsigned requests."""
    client = boto3.client("cognito-idp", region_name=REGION, config=Config(signature_version=UNSIGNED))
    user = Cognito(USER_POOL, CLIENT_ID, username=EMAIL)
    user.client = client
    user.authenticate(password=PASSWORD)
    return user


def resolve_identity_id(id_token):
    """Federate the user-pool token into the identity pool. BeTidy stores the
    identity id on every record WITHOUT the `region:` prefix, so we strip it."""
    login = f"cognito-idp.{REGION}.amazonaws.com/{USER_POOL}"
    ci = boto3.client("cognito-identity", region_name=REGION, config=Config(signature_version=UNSIGNED))
    federated = ci.get_id(IdentityPoolId=IDENTITY_POOL, Logins={login: id_token})["IdentityId"]
    return federated.split(":")[1]


# GraphQL field selections for each Amplify model (see docs/data-model.md).
TASK_FIELDS = (
    "id identityId active templateId title roomId projectId type important intervalUnit "
    "intervalCount lastTodoDate todoDate finishedDate description assigned creator effort "
    "lastHistoryId lastSkipDate days createdAt updatedAt"
)
PROJECT_FIELDS = (
    "id identityId templateId roomId title startDate type beforeImage afterImage creator createdAt updatedAt"
)
HISTORY_FIELDS = "id identityId taskId effort profileId time isProject roomType title templateId createdAt updatedAt"
USER_FIELDS = "identityId name email dataPrivacy rooms profiles pro packages holidays createdAt updatedAt"


def main():
    user = authenticate()
    identity_id = resolve_identity_id(user.id_token)
    headers = {"Authorization": user.id_token, "Content-Type": "application/json"}
    print(f"Authenticated. identityId = {identity_id}")

    def gql(query, variables=None):
        r = requests.post(APPSYNC_URL, headers=headers, json={"query": query, "variables": variables or {}}, timeout=60)
        data = r.json()
        if "errors" in data:
            raise RuntimeError(json.dumps(data["errors"])[:1000])
        return data["data"]

    def by_identity(query_field, fields):
        """Query a `<model>ByIdentityId` GSI, following nextToken. Scoping by identity
        id is essential — the unfiltered list* queries scan the whole shared table."""
        items, token = [], None
        query = (
            f"query Q($id:String!,$limit:Int,$next:String){{ "
            f"{query_field}(identityId:$id,limit:$limit,nextToken:$next){{ "
            f"items{{ {fields} }} nextToken }} }}"
        )
        while True:
            page = gql(query, {"id": identity_id, "limit": 1000, "next": token})[query_field]
            items.extend(page["items"])
            token = page.get("nextToken")
            if not token:
                break
        return items

    tasks = by_identity("userTasksByIdentityId", TASK_FIELDS)
    projects = by_identity("userProjectsByIdentityId", PROJECT_FIELDS)
    history = by_identity("taskHistoriesByIdentityId", HISTORY_FIELDS)
    user_record = gql(f"query G($id:ID!){{ getUser(identityId:$id){{ {USER_FIELDS} }} }}", {"id": identity_id})[
        "getUser"
    ]

    print(f"tasks={len(tasks)} projects={len(projects)} history={len(history)} user={'yes' if user_record else 'no'}")

    bundle = {"identityId": identity_id, "user": user_record, "tasks": tasks, "projects": projects, "history": history}
    with open(OUTFILE, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False, indent=2)
    print(f"Wrote {OUTFILE}")


if __name__ == "__main__":
    main()
