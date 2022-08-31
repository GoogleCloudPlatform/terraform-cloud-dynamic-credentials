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

terraform {
  cloud {
    organization = "${your-org-here}"

    workspaces {
      name = "dynamic-creds"
    }
  }
}

data "external" "curl_command" {
  program = ["bash", "get_gcp_token.sh"]
}

output "token_lookup_status" {
  value = data.external.curl_command.result.status
}

provider "google" {
  access_token = data.external.curl_command.result.token
}

resource "google_storage_bucket" "some_bucket" {
  name                        = "example-workload-bucket"
  project                     = "example-project"
  location                    = "US"
  force_destroy               = true
  uniform_bucket_level_access = true
}

output "bucket" {
  value = google_storage_bucket.some_bucket
}
