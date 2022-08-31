# Copyright 2022 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# -*- coding: utf-8 -*-
import json
import logging
import os
import pprint
import sys

import functions_framework
import requests
import voluptuous
import voluptuous.error
import voluptuous.humanize as humanize
from google.cloud import iam_credentials

_LOG_FORMAT = "%(levelname)s:%(asctime)s:%(name)s:%(message)s"
logging.basicConfig(stream=sys.stdout, format=_LOG_FORMAT)
l = logging.getLogger()
l.setLevel(logging.DEBUG if os.environ.get('DEBUG') == "true" else logging.INFO)

RUNS_BASE_URL = 'https://app.terraform.io/api/v2/runs/'
WORKSPACE_BASE_URL = 'https://app.terraform.io/api/v2/workspaces/'
ACCOUNT_URL = 'https://app.terraform.io/api/v2/account/details'
GOOGLE_DEFAULT_AUTH_SCOPES = ['https://www.googleapis.com/auth/cloud-platform']

BAD_GATEWAY = ({'status': 'error',
                'msg': 'Bad Gateway',
                'token': None}, 502)

SA_MAPPING = os.environ.get('SA_MAPPING_CONFIG', "{}")

REQUEST_SCHEMA = voluptuous.Schema({
    voluptuous.Required('TFC_TOKEN'): str,
    voluptuous.Required('RUN_ID'): str
}, extra=False)


def get_sa_token(creds_mapping, tfc_ws_slug):
    sa_ref = f'projects/-/serviceAccounts/{creds_mapping[tfc_ws_slug]}'

    l.info(f'Fetching GCP token for service account: {sa_ref}')

    iam = iam_credentials.IAMCredentialsClient()

    token = iam.generate_access_token(
        name=sa_ref,
        scope=GOOGLE_DEFAULT_AUTH_SCOPES
    )
    return token.access_token


@functions_framework.http
def generate_token(request):
    try:
        parsed_mapping = json.loads(SA_MAPPING)
    except json.JSONDecodeError:
        l.warning('SA_MAPPING_CONFIG environment variable is not valid JSON')

    if not parsed_mapping:
        l.warning('SA_MAPPING_CONFIG env var is empty; function has no mapped '
                  'credentials. Confirm SA_MAPPING_CONFIG contains non-empty '
                  'JSON object.')

    request_json = request.get_json(silent=True)

    if request_json is None:
        return {'status': 'error',
                'message': 'invalid or missing JSON request body'}, 400

    # Get token and run ID out of request body
    try:
        humanize.validate_with_humanized_errors(request_json, REQUEST_SCHEMA)
    except voluptuous.error.Error as e:
        base_msg = 'Invalid JSON schema in request body from client'
        e_msg = "; ".join(str(e).split("\n"))
        l.warning(base_msg)
        return {'status': 'error',
                'message': f'{base_msg}: {e_msg}',
                'token': None}, 400

    tfc_token = request_json['TFC_TOKEN']
    tfc_run_id = request_json['RUN_ID']

    l.debug(f'Got TFC token: {tfc_token[:10]}...[REMAINDER REDACTED]')
    l.debug(f'Got TFC Run ID: {tfc_run_id}')

    # validate token by calling TFC account API
    l.debug(f'Calling TFC account details API: {ACCOUNT_URL}')

    headers = {'Authorization': f'Bearer {tfc_token}'}
    resp = requests.get(ACCOUNT_URL, headers=headers)

    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        l.error(f'HTTP error requesting user details from TFC: {e}')
        if resp.status_code == 401:
            # If 401, return 401, otherwise 502 Bad Gateway
            return {'status': 'error',
                    'msg': "Unauthorized",
                    'token': None}, 401
        return BAD_GATEWAY
    else:
        l.info('TFC token validated with TFC API')

    try:
        sess_json = resp.json()
    except requests.exceptions.JSONDecodeError as e:
        l.error(f'Error decoding account details JSON: {e}')
        return BAD_GATEWAY

    l.debug(f'Session API response JSON: {pprint.pformat(sess_json)}')

    # Return 400 if validated token does not belong to a service account
    if sess_json['data']['attributes']['is-service-account'] != True:
        return {'status': 'error',
                'msg': 'Token does not belong to a service account',
                'token': None}, 400

    # get run details from TFC runs API
    url = RUNS_BASE_URL + tfc_run_id
    l.debug(f'Calling TFC run API URL: {url}')
    resp = requests.get(url, headers=headers)
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        l.error(f'HTTP error requesting run {tfc_run_id} from TFC: {e}')

    try:
        run_json = resp.json()
    except requests.exceptions.JSONDecodeError as e:
        l.error(f'Error decoding JSON for run {tfc_run_id}: {e}')
        return BAD_GATEWAY

    l.debug(f'Run API response JSON: {pprint.pformat(run_json)}')

    run_status = run_json['data']['attributes']['status']
    tfc_ws_id = run_json['data']['relationships']['workspace']['data']['id']

    # Return 400 if supplied TFC run is not a plan or apply
    expected_run_status = ("planning", "applying")
    if run_status not in expected_run_status:
        l.warning(f'Got run status "{run_status}" for run "{tfc_run_id}" '
                  f'expected: {", ".join(expected_run_status)}')
        return {'status': 'error',
                'msg': 'run status not planning or applying',
                'token': None
                }, 400

    # get workspace details
    url = WORKSPACE_BASE_URL + tfc_ws_id
    l.debug(f'Calling TFC workspace API url: {url}')
    resp = requests.get(url, headers=headers)
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        l.warning(f'HTTP error requesting workspace {tfc_ws_id} from TFC: {e}')
        return BAD_GATEWAY

    try:
        ws_json = resp.json()
    except requests.exceptions.JSONDecodeError as e:
        print(f'Error decoding JSON for workspace {tfc_ws_id}: {e}')
        return BAD_GATEWAY

    l.debug(f'Workspace API response JSON: {pprint.pformat(ws_json)}')

    tfc_org = ws_json['data']['relationships']['organization']['data']['id']
    tfc_ws_name = ws_json['data']['attributes']['name']
    ws_slug = f'{tfc_org}/{tfc_ws_name}'

    l.debug(f'Got organization: {tfc_org}')
    l.debug(f'Got workspace: {tfc_ws_name}')
    l.debug(f'Run workspace slug: {ws_slug}')

    if ws_slug not in parsed_mapping:
        return {'status': 'error',
                'msg': f'no identity for workspace: {ws_slug}',
                'token': None}, 404

    access_token = get_sa_token(parsed_mapping, ws_slug)

    return {
        "status": "success",
        "token": access_token,
    }
