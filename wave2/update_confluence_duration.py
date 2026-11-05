# -*- coding: utf-8 -*-
import urllib.request
import json
import base64
import ssl
import sys

EMAIL = "seyeon.kim@osci.kr"
API_TOKEN = "ATATT3xFfGF0O5NI20CZCCuF_oOX91r3crK81YMdUN6c0X8AKpJXBrvu4GKQ_1tSyaOzvONtgSVbFLtHtXat7dsDl8Y8mQe9noplWBB2p6U-LF1CZUKLnDwKKlWfH5dzKrL0TyjWZZRzWXh-t5-J8ftrkzgevvAdAbp8bF6qM5-hXAmynvr0Cfs=F14829E3"
DOMAIN = "cloud-osci.atlassian.net"
PAGE_ID = "24427561136"

ctx = ssl._create_unverified_context()
auth_str = f"{EMAIL}:{API_TOKEN}"
auth_encoded = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
headers = {
    "Authorization": f"Basic {auth_encoded}",
    "Content-Type": "application/json"
}

get_url = f"https://{DOMAIN}/wiki/api/v2/pages/{PAGE_ID}?body-format=storage"
req = urllib.request.Request(get_url, headers=headers)

with urllib.request.urlopen(req, context=ctx) as res:
    page_data = json.loads(res.read().decode("utf-8"))

current_version = page_data["version"]["number"]
page_title = page_data["title"]
body_val = page_data.get("body", {}).get("storage", {}).get("value", "")

with open("macro_content.txt", "r", encoding="utf-8") as f:
    kb_query_macro = f.read()

old_info_target = '<ac:structured-macro ac:name="info" ac:schema-version="1">'
if old_info_target in body_val:
    parts = body_val.split(old_info_target, 1)
    closing_tag = '</ac:structured-macro>'
    after_info = parts[1].split(closing_tag, 1)[1]
    new_body = parts[0] + kb_query_macro + after_info
else:
    new_body = body_val + kb_query_macro

update_url = f"https://{DOMAIN}/wiki/api/v2/pages/{PAGE_ID}"
payload = {
    "id": PAGE_ID,
    "status": "current",
    "title": page_title,
    "body": {
        "representation": "storage",
        "value": new_body
    },
    "version": {
        "number": current_version + 1,
        "message": "마이그레이션 시작/종료 시간 및 소요시간 조회(Atlassian KB 쿼리) 추가"
    }
}

json_data = json.dumps(payload).encode("utf-8")
update_req = urllib.request.Request(update_url, data=json_data, headers=headers, method="PUT")

with urllib.request.urlopen(update_req, context=ctx) as res:
    res_data = json.loads(res.read().decode("utf-8"))
    print("Updated Confluence successfully to Version:", res_data.get("version", {}).get("number"))
