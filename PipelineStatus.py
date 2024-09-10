import yaml
import requests
import json
import argparse
from github import Github
from github import Auth
from concurrent.futures import ThreadPoolExecutor, as_completed
from tabulate import tabulate
import sys

parser = argparse.ArgumentParser(description='Script to handle webhook URL and PAT token.')

# Add arguments
parser.add_argument('--webhook_url', type=str, required=True, help='The URL for the webhook.')
parser.add_argument('--pat_token', type=str, required=True, help='The PAT (Personal Access Token) for authentication.')

# Parse the arguments
args = parser.parse_args()

def load_repos_from_yaml(file_path):
    with open(file_path, 'r') as file:
        data = yaml.safe_load(file)
    return data.get('repos_list', [])

# Using an access token
auth = Auth.Token({args.pat_token})
g = Github(auth=auth)

repos_list = load_repos_from_yaml('Repo_List.yaml')

def get_pipeline_status(repo_name):
    try:
        repository = g.get_organization("Sogeti-Service-Center").get_repo(repo_name)
        workflows = repository.get_workflows()
        for workflow in workflows:
            if workflow.name == "Call QA Pipeline":
                runs = workflow.get_runs()
                latest_run = runs[0] if runs.totalCount > 0 else None
                if latest_run:
                    jobs = latest_run.jobs()
                    failed_stage = "N/A"
                    if latest_run.conclusion == 'failure':
                        for job in jobs:
                            if job.conclusion == 'failure':
                                failed_stage = job.name
                                break
                        formatted_date = latest_run.created_at.strftime('%Y-%m-%d %H:%M:%S')
                        return (repo_name, latest_run.status, f"Failed ({failed_stage})", formatted_date)
                    else:
                        return (repo_name, latest_run.status, latest_run.conclusion, "N/A")
                else:
                    return (repo_name, "No runs found", "N/A", "N/A")
    except Exception as e:
        return (repo_name, f"Error: {e}", "N/A", "N/A")
    
    return (repo_name, "No data", "N/A", "N/A")

# Collect results in a list
results = []

with ThreadPoolExecutor() as executor:
    futures = [executor.submit(get_pipeline_status, repo) for repo in repos_list]
    for future in as_completed(futures):
        result = future.result()
        if result:
            results.append(result)
        else:
            results.append(("Unknown Repository", "No data", "N/A", "N/A"))

# Format and print results in a table
headers = ["Repository", "Latest Status", "Conclusion", "Last Successful Run"]
table = tabulate(results, headers=headers, tablefmt="grid")
print(table)

rows = [{"Repository": row[0], 
         "Latest Status": "✅" if row[2].lower() == "success" 
                         else "❓" if row[2].lower() == "n/a" 
                         else "❌" if row[2].lower().startswith("failed") 
                         else row[2]} for row in results]

def create_card_payload(start_index, end_index):
    # Adaptive Card JSON payload for Microsoft Teams
    card_payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "version": "1.3",
                    "msteams": {
                        "entities": []
                    },
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": "Repository Status Report",
                            "weight": "Bolder",
                            "size": "Medium"
                        },
                        {
                            "type": "ColumnSet",
                            "columns": [
                                {
                                    "type": "Column",
                                    "width": "stretch",
                                    "items": [
                                        {
                                            "type": "TextBlock",
                                            "text": "Repository",
                                            "weight": "Bolder",
                                            "wrap": False,
                                            "maxLines": 1
                                        }
                                    ]
                                },
                                {
                                    "type": "Column",
                                    "width": "auto",
                                    "items": [
                                        {
                                            "type": "TextBlock",
                                            "text": "Latest Status",
                                            "weight": "Bolder"
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            }
        ]
    }

    for row in rows[start_index:end_index]:
        card_payload["attachments"][0]["content"]["body"].append(
            {
                "type": "ColumnSet",
                "columns": [
                    {
                        "type": "Column",
                        "width": "stretch",
                        "items": [
                            {
                                "type": "TextBlock",
                                "text": row["Repository"],
                                "wrap": False,
                                "maxLines": 1
                            }
                        ]
                    },
                    {
                        "type": "Column",
                        "width": "auto",
                        "items": [
                            {
                                "type": "TextBlock",
                                "text": row["Latest Status"],
                                "horizontalAlignment": "Center"
                            }
                        ]
                    }
                ]
            }
        )

    return card_payload

def send_payload_in_chunks():
    max_size = 27 * 1024  # 27KB
    start_index = 0
    while start_index < len(rows):
        end_index = start_index
        while end_index < len(rows) and sys.getsizeof(json.dumps(create_card_payload(start_index, end_index))) < max_size:
            end_index += 1
        end_index = min(end_index, len(rows))
        
        json_payload = json.dumps(create_card_payload(start_index, end_index))
        response = requests.post({args.webhook_url}, data=json_payload, headers={"Content-Type": "application/json"})
        print(json_payload)
        
        if response.status_code == 200:
            print(f"Message sent successfully for repositories {start_index} to {end_index - 1}!")
        else:
            print(f"Failed to send message. Status code: {response.status_code}")
        
        start_index = end_index

# Send payload in chunks
send_payload_in_chunks()

# Close connection after use
g.close()
