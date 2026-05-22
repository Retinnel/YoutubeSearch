"""
Patch workflow_v4.json to add dual LLM provider for Expand Queries node.

Changes:
1. Rename 'Expand Queries' -> 'Expand Queries (OpenAI)'
2. Add 'Config' code node (single place to set LLM_PROVIDER)
3. Add 'Expand Queries Router' switch node
4. Add 'Expand Queries (Google)' chainLlm node + Gemini sub-node
5. Update Enrich Shorts to read LLM_PROVIDER from Config node
6. Update connections accordingly
"""
import json
import uuid

with open("workflow_v4.json", encoding="utf-8") as f:
    v4 = json.load(f)

nodes = v4["nodes"]
conns = v4["connections"]

# ── 1. Rename 'Expand Queries' → 'Expand Queries (OpenAI)' ────────────────
for n in nodes:
    if n["name"] == "Expand Queries":
        n["name"] = "Expand Queries (OpenAI)"
        n["position"] = [-20, -200]
        break

# ── 2. Add Config node ────────────────────────────────────────────────────
config_code = (
    "// =============================================\n"
    "// LLM PROVIDER SWITCH - change value here only\n"
    "// 'google' = Google Gemini (default)\n"
    "// 'openai' = OpenAI GPT-4o-mini\n"
    "// =============================================\n"
    "const LLM_PROVIDER = \"google\";\n\n"
    "const items = $input.all();\n"
    "return items.map(function(item) {\n"
    "    return { json: Object.assign({}, item.json, { llm_provider: LLM_PROVIDER }) };\n"
    "});"
)

config_node = {
    "parameters": {"jsCode": config_code, "mode": "runOnceForEachItem"},
    "type": "n8n-nodes-base.code",
    "typeVersion": 2,
    "position": [-440, 0],
    "id": str(uuid.uuid4()),
    "name": "Config",
}
nodes.append(config_node)

# ── 3. Add Expand Queries Router (Switch) ────────────────────────────────
expand_router_node = {
    "parameters": {
        "rules": {
            "values": [
                {
                    "conditions": {
                        "combinator": "and",
                        "conditions": [
                            {
                                "leftValue": "={{ $json.llm_provider }}",
                                "operator": {"operation": "equals", "type": "string"},
                                "rightValue": "openai",
                            }
                        ],
                        "options": {
                            "caseSensitive": True,
                            "leftValue": "",
                            "typeValidation": "strict",
                        },
                    }
                }
            ]
        },
        "options": {"fallbackOutput": "extra"},
    },
    "type": "n8n-nodes-base.switch",
    "typeVersion": 3.2,
    "position": [-230, 0],
    "id": str(uuid.uuid4()),
    "name": "Expand Queries Router",
}
nodes.append(expand_router_node)

# ── 4a. Add Expand Queries (Google) – chainLlm ──────────────────────────
expand_google_prompt = (
    "=\u0415\u0441\u0442\u044c \u043f\u043e\u0438\u0441\u043a\u043e\u0432\u043e\u0439 "
    "\u0437\u0430\u043f\u0440\u043e\u0441 \u0434\u043b\u044f \u044e\u0442\u0443\u0431\u0430 - "
    "{{ $json.message.text }}. "
    "\u0421\u0434\u0435\u043b\u0430\u0439 5 \u043f\u043e\u0445\u043e\u0436\u0438\u0445 "
    "\u0437\u0430\u043f\u0440\u043e\u0441\u043e\u0432 \u043d\u0430 \u0430\u043d\u0433\u043b\u0438\u0439\u0441\u043a\u043e\u043c, "
    "\u0447\u0442\u043e\u0431\u044b \u0443\u0432\u0435\u043b\u0438\u0447\u0438\u0442\u044c \u043f\u043e\u043a\u0440\u044b\u0442\u0438\u0435 "
    "\u043f\u043e\u0438\u0441\u043a\u0430 \u0448\u043e\u0440\u0442\u0441\u043e\u0432. "
    "\u0422\u0430\u043a\u0436\u0435 \u0432\u0435\u0440\u043d\u0438 \u043f\u043e\u043b\u0435 \"theme\" "
    "\u0441 \u043e\u043f\u0438\u0441\u0430\u043d\u0438\u0435\u043c \u0442\u0435\u043c\u0430\u0442\u0438\u043a\u0438 "
    "\u0434\u043b\u044f \u043f\u043e\u0441\u043b\u0435\u0434\u0443\u044e\u0449\u0435\u0439 \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0438 "
    "\u0440\u0435\u043b\u0435\u0432\u0430\u043d\u0442\u043d\u043e\u0441\u0442\u0438. "
    "DO NOT use markdown. Answer ONLY with valid JSON like: "
    "{\"related_search_queries\": [\"query1\",\"query2\",\"query3\",\"query4\",\"query5\"], "
    "\"theme\": \"Brief description of the content niche in English\"}"
)

expand_google_node = {
    "parameters": {
        "promptType": "define",
        "text": expand_google_prompt,
    },
    "type": "@n8n/n8n-nodes-langchain.chainLlm",
    "typeVersion": 1.5,
    "position": [-20, 200],
    "id": str(uuid.uuid4()),
    "name": "Expand Queries (Google)",
}
nodes.append(expand_google_node)

# ── 4b. Add Google Gemini Model (Expand) sub-node ────────────────────────
# Try to copy credentials from existing Google Gemini Model node
gemini_creds = None
for n in nodes:
    if n["name"] == "Google Gemini Model":
        gemini_creds = n.get("credentials")
        break

gemini_expand_node = {
    "parameters": {
        "modelName": {
            "__rl": True,
            "value": "models/gemini-2.0-flash",
            "mode": "list",
            "cachedResultName": "Gemini 2.0 Flash",
        },
        "options": {},
    },
    "type": "@n8n/n8n-nodes-langchain.lmChatGoogleGemini",
    "typeVersion": 1,
    "position": [-20, 420],
    "id": str(uuid.uuid4()),
    "name": "Google Gemini Model (Expand)",
}
if gemini_creds:
    gemini_expand_node["credentials"] = gemini_creds
nodes.append(gemini_expand_node)

# ── 5. Update Enrich Shorts to read LLM_PROVIDER from Config node ─────────
for n in nodes:
    if n["name"] == "Enrich Shorts":
        old = 'const LLM_PROVIDER = "google";'
        new = "const LLM_PROVIDER = $('Config').first().json.llm_provider || \"google\";"
        n["parameters"]["jsCode"] = n["parameters"]["jsCode"].replace(old, new, 1)
        break

# ── 6. Update connections ─────────────────────────────────────────────────
# 6a. Remove Telegram Trigger → Expand Queries (OpenAI) (was 'Expand Queries')
tt_main = conns["Telegram Trigger"]["main"][0]
conns["Telegram Trigger"]["main"][0] = [
    c for c in tt_main if c["node"] not in ("Expand Queries", "Expand Queries (OpenAI)")
]
# Add Telegram Trigger → Config
conns["Telegram Trigger"]["main"][0].append(
    {"node": "Config", "type": "main", "index": 0}
)

# 6b. Config → Expand Queries Router
conns["Config"] = {
    "main": [[{"node": "Expand Queries Router", "type": "main", "index": 0}]]
}

# 6c. Expand Queries Router → (OpenAI branch 0, Google branch 1)
conns["Expand Queries Router"] = {
    "main": [
        [{"node": "Expand Queries (OpenAI)", "type": "main", "index": 0}],
        [{"node": "Expand Queries (Google)", "type": "main", "index": 0}],
    ]
}

# 6d. Rename old 'Expand Queries' connection key
if "Expand Queries" in conns:
    conns["Expand Queries (OpenAI)"] = conns.pop("Expand Queries")

# 6e. Expand Queries (Google) → Parse Queries
conns["Expand Queries (Google)"] = {
    "main": [[{"node": "Parse Queries", "type": "main", "index": 0}]]
}

# 6f. Google Gemini Model (Expand) → Expand Queries (Google) via ai_languageModel
conns["Google Gemini Model (Expand)"] = {
    "ai_languageModel": [
        [{"node": "Expand Queries (Google)", "type": "ai_languageModel", "index": 0}]
    ]
}

# ── Save ──────────────────────────────────────────────────────────────────
with open("workflow_v4.json", "w", encoding="utf-8") as f:
    json.dump(v4, f, ensure_ascii=True, indent=2)

print("Patched workflow_v4.json successfully!")
print("Total nodes:", len(nodes))
for n in nodes:
    print(" -", n["name"])

print("\nConnections:")
for src, out in sorted(conns.items()):
    for typ, branches in out.items():
        for i, branch in enumerate(branches):
            for c in branch:
                print(f"  {src} [{typ}:{i}] -> {c['node']}")
