#!/usr/bin/env python3
import os
import sys
import json
import requests
import xml.etree.ElementTree as ET
from xml.dom import minidom
from dotenv import load_dotenv

# Load environment variables
possible_paths = ['env', '../env']
env_loaded = False
for path in possible_paths:
    if os.path.exists(path):
        load_dotenv(dotenv_path=path)
        env_loaded = True
        break

def get_auth_config(email, token):
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    if not email:
        # Check if token is a base64-encoded basic auth token (contains ':')
        import base64
        is_basic = False
        try:
            decoded = base64.b64decode(token).decode('utf-8', errors='ignore')
            if ":" in decoded and any(c.isalnum() for c in decoded):
                is_basic = True
        except Exception:
            pass
            
        if is_basic:
            headers["Authorization"] = f"Basic {token}"
        else:
            headers["Authorization"] = f"Bearer {token}"
            
        return {
            "headers": headers,
            "verify": False
        }
    else:
        return {
            "headers": headers,
            "auth": (email, token),
            "verify": False
        }

def fetch_issue_data(issue_key, jira_url, auth_config):
    # Query issue details with expanded fields (renderedFields)
    issue_endpoint = f"{jira_url.rstrip('/')}/rest/api/3/issue/{issue_key}?expand=renderedFields,names"
    resp = requests.get(issue_endpoint, **auth_config)
    if resp.status_code != 200:
        print(f"[ERROR] Failed to fetch issue details ({resp.status_code}): {resp.text}")
        sys.exit(1)
    issue_data = resp.json()
    
    # Query comments
    comments_endpoint = f"{jira_url.rstrip('/')}/rest/api/3/issue/{issue_key}/comment"
    c_resp = requests.get(comments_endpoint, **auth_config)
    comments_data = {}
    if c_resp.status_code == 200:
        comments_data = c_resp.json()
        
    return issue_data, comments_data

def build_xml(issue_data, comments_data):
    root = ET.Element("rss", version="0.92")
    channel = ET.SubElement(root, "channel")
    
    ET.SubElement(channel, "title").text = "Jira Issue Full Metadata View"
    ET.SubElement(channel, "link").text = issue_data.get("self", "")
    
    item = ET.SubElement(channel, "item")
    
    # Core Fields
    ET.SubElement(item, "key").text = issue_data.get("key")
    
    fields = issue_data.get("fields", {})
    rendered_fields = issue_data.get("renderedFields", {})
    field_names = issue_data.get("names", {})
    
    # Summary
    ET.SubElement(item, "summary").text = fields.get("summary", "")
    
    # Description
    desc_node = ET.SubElement(item, "description")
    ET.SubElement(desc_node, "adf").text = json.dumps(fields.get("description", ""), indent=2, ensure_ascii=False)
    ET.SubElement(desc_node, "rendered").text = rendered_fields.get("description", "")
    
    # Created / Updated
    ET.SubElement(item, "created").text = fields.get("created", "")
    ET.SubElement(item, "updated").text = fields.get("updated", "")
    
    # Custom Fields
    customfields_node = ET.SubElement(item, "customfields")
    for fid, val in fields.items():
        if fid.startswith("customfield_"):
            cf_name = field_names.get(fid, fid)
            cf_node = ET.SubElement(customfields_node, "customfield", id=fid)
            ET.SubElement(cf_node, "customfieldname").text = cf_name
            
            cf_val = ET.SubElement(cf_node, "customfieldvalues")
            # Raw ADF
            ET.SubElement(cf_val, "raw_adf").text = json.dumps(val, indent=2, ensure_ascii=False)
            # Rendered HTML
            ET.SubElement(cf_val, "rendered_html").text = rendered_fields.get(fid, "")
            
    # Comments
    comments_node = ET.SubElement(item, "comments")
    for comment in comments_data.get("comments", []):
        c_node = ET.SubElement(comments_node, "comment", id=comment.get("id"))
        ET.SubElement(c_node, "author").text = comment.get("author", {}).get("displayName", "")
        ET.SubElement(c_node, "created").text = comment.get("created", "")
        ET.SubElement(c_node, "body_adf").text = json.dumps(comment.get("body", ""), indent=2, ensure_ascii=False)
        
    return root

def main():
    print("=== Jira Issue XML Debugging Tool ===")
    if not env_loaded:
        print("[WARNING] Could not find 'env' or '../env' file to load credentials.")
        
    environments = {
        "1": {
            "name": "JIRA_DC_STG (Data Center Staging)",
            "url_var": "JIRA_DC_STG_URL",
            "token_var": "JIRA_DC_STG_API_TOKEN",
            "email_var": "JIRA_DC_STG_EMAIL"
        },
        "2": {
            "name": "JIRA_DC_OPS (Data Center Operations)",
            "url_var": "JIRA_DC_OPS_URL",
            "token_var": "JIRA_DC_OPS_TOKEN",
            "email_var": "JIRA_DC_OPS_EMAIL"
        },
        "3": {
            "name": "JIRA_CLOUD_SANDBOX (Cloud Sandbox)",
            "url_var": "JIRA_CLOUD_SANDBOX_URL",
            "token_var": "JIRA_CLOUD_SANDBOX_API_TOKEN",
            "email_var": "JIRA_CLOUD_SANDBOX_ADMIN_EMAIL"
        },
        "4": {
            "name": "JIRA_CLOUD_OPS (Cloud Operations)",
            "url_var": "JIRA_CLOUD_OPS_URL",
            "token_var": "JIRA_CLOUD_OPS_API_TOKEN",
            "email_var": "JIRA_CLOUD_OPS_ADMIN_EMAIL"
        }
    }
    
    print("\nSelect Environment:")
    for num, env in environments.items():
        print(f"  [{num}] {env['name']}")
        
    choice = input("\nEnter choice [1-4]: ").strip()
    if choice not in environments:
        print("[ERROR] Invalid choice. Exiting.")
        sys.exit(1)
        
    selected = environments[choice]
    
    jira_url = os.getenv(selected["url_var"], "").strip().strip('"').strip("'")
    token = os.getenv(selected["token_var"], "").strip().strip('"').strip("'")
    email = os.getenv(selected["email_var"], "").strip().strip('"').strip("'")
    
    if not jira_url:
        print(f"[ERROR] URL variable '{selected['url_var']}' is not set in env.")
        sys.exit(1)
    if not token:
        print(f"[ERROR] Token variable '{selected['token_var']}' is not set in env.")
        sys.exit(1)
        
    issue_key = input("\nEnter Issue Key (e.g. HMGPR-321): ").strip().upper()
    if not issue_key:
        print("[ERROR] Issue Key cannot be empty.")
        sys.exit(1)
        
    requests.packages.urllib3.disable_warnings()
    
    auth_config = get_auth_config(email, token)
    print(f"\nFetching detailed metadata (including renderedFields) for {issue_key} from {jira_url}...")
    issue_data, comments_data = fetch_issue_data(issue_key, jira_url, auth_config)
    
    print("Building XML structure...")
    root = build_xml(issue_data, comments_data)
    
    xml_str = ET.tostring(root, encoding="utf-8")
    parsed_xml = minidom.parseString(xml_str)
    pretty_xml = parsed_xml.toprettyxml(indent="    ")
    
    output_filename = f"{issue_key}.xml"
    with open(output_filename, "w", encoding="utf-8") as f:
        f.write(pretty_xml)
        
    print(f"\n[SUCCESS] Saved detailed issue XML to {output_filename}")

if __name__ == "__main__":
    main()
