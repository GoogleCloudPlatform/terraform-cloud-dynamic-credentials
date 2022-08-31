# Terraform Cloud GCP Dynamic Credentials

Code in this repository allows dynamic, short-lived GCP service account credentials to be generated and used from within Terraform Cloud. This enables the use of GCP service accounts without manually generating, downloading, and uploading service account keys into Terraform Cloud as variables.

## Security Model

The code in the Google cloud function takes the following security-focused constraints into account when authenticating a request from a client:

1. Validates the supplied token against the Terraform Cloud API
2. Ensures the supplied token belongs to a Terraform service account
   1. No user, team, or organization-supplied tokens will be permitted
3. Queries the Terraform Cloud runs API to ensure the current run is in a state of `planning` or `applying`
   1. This ensures Terraform tokens from prior runs cannot be used
4. Looks up the parent workspace of the supplied run ID and obtains its organization
5. Ensures a statically-mapped GCP credential in the function's configuration matches explictly to an organization and workspace

## Components

There are two necessary components: code for a Google Cloud Function and Terraform resources to call the function and set the access token in the [Google Terraform provider](https://registry.terraform.io/providers/hashicorp/google/latest/docs/guides/provider_reference).

### Code

In the [func/](func/) folder is code for a Google Cloud Function written to receive HTTP requests.  This function takes a configuration value which maps Terraform Cloud workspaces to service accounts.  It expects to receive a JSON payload containing two values:

1. Terraform Cloud Run ID belonging to the run from which it was called
2. Terraform Cloud API token

A sample JSON body can be found below:

```json
{
    "TFC_TOKEN": "9zCfoobarHTuu0A.atlasv1.48swmoQ1DMasdfghjklzExZk0q0QzIguuIaekI0HbjjOY5dXFkkoJV1pbazquux",
    "RUN_ID": "run-RbEqJ7cfoobarfiL"
}
```

An example response from the Cloud Function resembles the following:

```json
{
   "status": "success",
   "token": "ya29.c.b0AXv0zTO..."
}
```

The function performs the following steps using the token and run ID it receives in the request body:

1. Validates the supplied token against the Terraform Cloud API
2. Ensures the token belongs to a TFC service account
3. Queries the Terraform Cloud API for the parent workspace of the calling run
4. Looks up the parent organization of the workspace
5. Checks its configuration for a service account mapped to a TFC workspace
6. Obtains a GCP service account token for the mapped credential with a lifetime of 1 hour

The returned token can subsequently be used when configuring the Google Terraform provider using the [access_token](https://registry.terraform.io/providers/hashicorp/google/latest/docs/guides/provider_reference#access_token) attribute, which may then be used to deploy resources using the IAM permissions of the GCP service account.

#### Configuration

The Google Cloud Function requires a service account with the Service Account Token Creator (`roles/iam.serviceAccountTokenCreator`) role bound to each service account for which short-lived tokens will be created.  Please review the documentation to grant IAM to a service account in the Google Cloud documentation [here](https://cloud.google.com/iam/docs/manage-access-service-accounts#single-role).

Once the Cloud Function's service account has the correct IAM grants to create tokens for service accounts, the function must be configured with a JSON object which maps a service account to a Terraform Cloud workspace. This JSON object should be the value of the `SA_MAPPING_CONFIG` environment variable.  The JSON object must use the following structure:

```json
{
  "terraform_cloud_org/workspace": "service-account-email@project-id.iam.gserviceaccount.com",
  "my-org/dynamic-creds-workspace": "tf-dynamic-creds@dynamic-creds-a123d.iam.gserviceaccount.com"
}
```

To enable verbose logging in the Cloud Function, you may optionally set the environment variable `DEBUG=true`.

#### Deployment

##### Prerequisites

The following services will need to be enabled in the project where the Cloud Function is to be deployed:

* iamcredentials.googleapis.com (required to generate service account tokens)
* cloudfunctions.googleapis.com 
* cloudbuild.googleapis.com (required for functions)
* logging.googleapis.com (required for functions)

This can be done in a one-liner command in a Bash-like shell with the following example:

```shell
PROJECT=<your-project-id> for SERVICE in logging.googleapis.com iamcredentials.googleapis.com cloudfunctions.googleapis.com cloudbuild.googleapis.com; do gcloud services enable $SERVICE --project $PROJECT; done
```

Create a GCP Service Account to use with this function following the documentation or with the below example `gcloud` command:

```shell
gcloud iam service-accounts create <function-service-account> --project <your-project-id>
```

Once you have a service account for your function, the function's SA will need *Service Account Token Creator* IAM role (`roles/iam.serviceAccountTokenCreator`) granted on every service account for which it will generate tokens.  See [Configuration](#configuration) above.

This function can be deployed with the following `gcloud` command:

```shell
gcloud functions deploy generate_token \
  --project <your-project-id> \
  --service-account <your-service-account@gcp-project.iam.gserviceaccount.com> \
  --runtime python39 \
  --trigger-http \
  --allow-unauthenticated 
```

The above command deploys the cloud function [without requiring authentication](https://cloud.google.com/functions/docs/securing/managing-access-iam#allowing_unauthenticated_http_function_invocation).  This is due to the TFC token validation steps outlined above, and to avoid requiring manually generated service account credentials.  If you require authentication of requests to this function, please read *[Using IAM to Authorize Access](https://cloud.google.com/functions/docs/securing/managing-access-iam)*.

**Note:** If you have a [Domain Restricted Sharing](https://cloud.google.com/resource-manager/docs/organization-policy/restricting-domains) organization policy enabled in your organization, you will need to [override](https://cloud.google.com/resource-manager/docs/organization-policy/creating-managing-policies) this policy to the Google-managed default on the project where the function is to be deployed.  This can be done at deployment-time and subsequently reverted thereafter.

### Terraform

See the [example](tf/main.tf)  for an demo of how one can call the deployed Cloud Function.

#### How It Works

Terraform Cloud sets two critical environments variables in its runtime environment for runs:

* `ATLAS_TOKEN`: the token belonging to the service account tied to the lifecycle of the run
* `TFC_RUN_ID`: A unique identifier for the currently executing run

You must make a remote web call to the deployed token generator Cloud Function endpoint.  An example call is in the [tf/get_gcp_token.sh](tf/get_gcp_token.sh) script.  In essence, it's a simple `curl` command with a JSON payload which provides the aforementioned values from the environment.  

The cloud function returns a JSON response with a short-lived GCP access token belonging to a service account from the function's service account mapping config.

Using the external data resource, call the curl bash script:

```terraform
data "external" "curl_command" {
  program = ["bash", "get_gcp_token.sh"]
}
```

Then instantiate the `google` provider using the dynamically generated token:

```terraform
provider "google" {
  access_token = data.external.curl_command.result.token
}
```

## License

Apache v2.0

## Disclaimer

This is not an official Google product.
