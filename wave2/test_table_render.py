# -*- coding: utf-8 -*-
import json

with open('unmigrated_1872_data.json', 'r', encoding='utf-8') as f:
    d = json.load(f)

print("Loaded 1872 items successfully:", len(d['items']))
