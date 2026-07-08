"""One-off: count users in nav_bar interactions table."""
import credentials

credentials.bootstrap_gcp_credentials()

import config
from google.cloud import bigquery

print("Project:", config.GCP_PROJECT)
print("Location:", config.BQ_LOCATION)
print("Service account:", credentials.service_account_email())
print("---")

client = bigquery.Client(project=config.GCP_PROJECT, location=config.BQ_LOCATION)

query = """
SELECT COUNT(user_id) AS user_count
FROM `kossip-helpers.academy_success_ai_analytics_worksapce.z_ccbp_users_cloudwatch_interactions_with_nav_bar`
"""

print("Running query...")
query_job = client.query(query, location=config.BQ_LOCATION)

for row in query_job.result():
    print(f"User Count: {row.user_count}")

print("Done.")
