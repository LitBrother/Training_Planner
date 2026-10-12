import time

import requests
import truststore
import tomllib

truststore.inject_into_ssl()

with open(".streamlit/secrets.toml", "rb") as f:
    key = tomllib.load(f)["GEMINI_API_KEY"]

r = requests.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                 headers={"X-goog-api-key": key}, timeout=(10, 30))
for m in r.json().get("models", []):
    if "generateContent" in m.get("supportedGenerationMethods", []):
        print(m["name"].removeprefix("models/"))