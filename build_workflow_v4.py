"""Build workflow_v4.json from workflow_v3.json with these changes:
- Add outlier_score, engagement_rate, llm_provider to Enrich Shorts
- Add LLM Router Switch node
- Add Score Relevance (Google) via chainLlm + Gemini sub-node
- Rename Score Relevance -> Score Relevance (OpenAI)
- Update Parse Score to handle both output formats + include new fields
- Update Format Telegram Message to include outlier_score
- Update Search Shorts body to include min_outlier_score
"""
import json
import copy

with open("workflow_v3.json", encoding="utf-8") as f:
    d = json.load(f)

v4 = copy.deepcopy(d)
v4["name"] = "YouTube Shorts Finder v4"


def find_node(name):
    for n in v4["nodes"]:
        if n["name"] == name:
            return n
    return None


# ── 1. Update Search Shorts body ──────────────────────────────────────────────
ss = find_node("Search Shorts (FastAPI)")
ss["parameters"]["jsonBody"] = (
    '={"query": "{{ $json.query }}", "max_results": 30, "min_views": 0, "min_outlier_score": 0}'
)

# ── 2. Update Enrich Shorts ───────────────────────────────────────────────────
enrich = find_node("Enrich Shorts")
enrich["parameters"]["jsCode"] = (
    "// runOnceForAllItems: receives all HTTP responses at once\n"
    "// One item per query -> we enrich each and return N items for Split Out\n"
    "//\n"
    "// === LLM PROVIDER SWITCH ===\n"
    '// Change LLM_PROVIDER below to switch between "openai" and "google" for scoring.\n'
    'const LLM_PROVIDER = "google";\n'
    "\n"
    "const allItems = $input.all();\n"
    "const theme = $('Parse Queries').first().json.theme || '';\n"
    "\n"
    "const results = [];\n"
    "for (const item of allItems) {\n"
    "    const data = item.json;\n"
    "    const query = data.query || '';\n"
    "\n"
    "    const enrichedShorts = (data.shorts || []).map(function(s) {\n"
    "        return {\n"
    "            video_id: s.video_id || '',\n"
    "            url: s.url || '',\n"
    "            title: s.title || 'No title',\n"
    "            channel_id: s.channel_id || null,\n"
    "            channel_title: s.channel_title || null,\n"
    "            duration: s.duration || null,\n"
    "            search_query: query,\n"
    "            theme: theme,\n"
    "            view_count: s.view_count || null,\n"
    "            outlier_score: s.outlier_score !== undefined ? s.outlier_score : null,\n"
    "            engagement_rate: s.engagement_rate !== undefined ? s.engagement_rate : null,\n"
    "            llm_provider: LLM_PROVIDER\n"
    "        };\n"
    "    });\n"
    "\n"
    "    results.push({ json: { shorts: enrichedShorts } });\n"
    "}\n"
    "\n"
    "return results.length > 0 ? results : [{ json: { shorts: [] } }];"
)

# ── 3. Update Merge Transcript ────────────────────────────────────────────────
mt = find_node("Merge Transcript")
mt["parameters"]["jsCode"] = (
    "// runOnceForAllItems: pairs transcript responses with original shorts by index.\n"
    "// HTTP Request preserves order, so index i in transcripts == index i in Split Shorts.\n"
    "const transcripts = $input.all();\n"
    "const shorts = $('Split Shorts').all();\n"
    "\n"
    "return transcripts.map(function(item, i) {\n"
    "    const short = shorts[i] ? shorts[i].json : {};\n"
    "    const t = item.json;\n"
    "    return {\n"
    "        json: {\n"
    "            video_id: short.video_id || '',\n"
    "            url: short.url || '',\n"
    "            title: short.title || 'No title',\n"
    "            channel_id: short.channel_id || null,\n"
    "            channel_title: short.channel_title || null,\n"
    "            duration: short.duration || null,\n"
    "            search_query: short.search_query || '',\n"
    "            theme: short.theme || '',\n"
    "            view_count: short.view_count || null,\n"
    "            outlier_score: short.outlier_score !== undefined ? short.outlier_score : null,\n"
    "            engagement_rate: short.engagement_rate !== undefined ? short.engagement_rate : null,\n"
    "            llm_provider: short.llm_provider || 'openai',\n"
    "            transcript: t.transcript || null,\n"
    "            transcript_source: t.source || null,\n"
    "            transcript_error: t.error || null\n"
    "        }\n"
    "    };\n"
    "});"
)

# ── 4. Rename Score Relevance → Score Relevance (OpenAI), update prompt ──────
sr_openai = find_node("Score Relevance")
sr_openai["name"] = "Score Relevance (OpenAI)"
sr_openai["position"] = [1640, -200]

sr_openai["parameters"]["responses"]["values"][0]["content"] = (
    "=You are a content relevance analyst. The user is looking for YouTube Shorts about this theme: {{ $json.theme }}. "
    "Rate relevance 1-10. If transcript is unavailable, judge by title and channel name only. "
    "Return ONLY valid JSON, no markdown."
)
sr_openai["parameters"]["responses"]["values"][1]["content"] = (
    "=Video title: {{ $json.title }}\n"
    "Channel: {{ $json.channel_title }}\n"
    "Duration: {{ $json.duration }}s\n"
    "Outlier score: {{ $json.outlier_score !== null ? $json.outlier_score + 'x (viral coefficient)' : 'unknown' }}\n\n"
    "Transcript: {{ $json.transcript ? $json.transcript : \"(unavailable)\" }}\n\n"
    "Return JSON: {\"score\": <int 1-10>, \"reason\": \"<brief explanation in Russian>\", \"should_rewrite\": <true if score >= 7>}"
)

# ── 5. Add LLM Router Switch node ─────────────────────────────────────────────
llm_router = {
    "parameters": {
        "rules": {
            "values": [
                {
                    "conditions": {
                        "options": {
                            "caseSensitive": True,
                            "leftValue": "",
                            "typeValidation": "strict",
                        },
                        "conditions": [
                            {
                                "leftValue": "={{ $json.llm_provider }}",
                                "rightValue": "openai",
                                "operator": {
                                    "type": "string",
                                    "operation": "equals",
                                },
                            }
                        ],
                        "combinator": "and",
                    }
                }
            ]
        },
        "options": {"fallbackOutput": "extra"},
    },
    "type": "n8n-nodes-base.switch",
    "typeVersion": 3.2,
    "position": [1400, 0],
    "id": "d1e2f3a4-b5c6-7d8e-9f0a-1b2c3d4e5f60",
    "name": "LLM Router",
}
v4["nodes"].append(llm_router)

# ── 6. Add Score Relevance (Google) — chainLlm node ──────────────────────────
google_prompt = (
    "=You are a content relevance analyst. The user is looking for YouTube Shorts about this theme: {{ $json.theme }}.\n\n"
    "Video title: {{ $json.title }}\n"
    "Channel: {{ $json.channel_title }}\n"
    "Duration: {{ $json.duration }}s\n"
    "Outlier score: {{ $json.outlier_score !== null ? $json.outlier_score + 'x (viral coefficient)' : 'unknown' }}\n\n"
    "Transcript: {{ $json.transcript ? $json.transcript : \"(unavailable)\" }}\n\n"
    "Rate relevance 1-10. If transcript is unavailable, judge by title and channel name only.\n"
    "Return ONLY valid JSON, no markdown:\n"
    "{\"score\": <int 1-10>, \"reason\": \"<brief explanation in Russian>\", \"should_rewrite\": <true if score >= 7>}"
)

sr_google = {
    "parameters": {
        "promptType": "define",
        "text": google_prompt,
        "options": {},
    },
    "type": "@n8n/n8n-nodes-langchain.chainLlm",
    "typeVersion": 1.5,
    "position": [1640, 200],
    "id": "e2f3a4b5-c6d7-8e9f-0a1b-2c3d4e5f6a70",
    "name": "Score Relevance (Google)",
}
v4["nodes"].append(sr_google)

# ── 7. Add Google Gemini Model sub-node ───────────────────────────────────────
gemini_model = {
    "parameters": {
        "modelName": {
            "__rl": True,
            "value": "models/gemini-2.0-flash",
            "mode": "list",
            "cachedResultName": "models/gemini-2.0-flash",
        },
        "options": {},
    },
    "type": "@n8n/n8n-nodes-langchain.lmChatGoogleGemini",
    "typeVersion": 1,
    "position": [1640, 400],
    "id": "f3a4b5c6-d7e8-9f0a-1b2c-3d4e5f6a7b80",
    "name": "Google Gemini Model",
}
v4["nodes"].append(gemini_model)

# ── 8. Update Parse Score ─────────────────────────────────────────────────────
ps = find_node("Parse Score")
ps["parameters"]["jsCode"] = (
    "// runOnceForAllItems: pairs LLM outputs with original shorts from Merge Transcript by index.\n"
    "// Handles both OpenAI node output and Google chainLlm output formats.\n"
    "const llmOutputs = $input.all();\n"
    "const shorts = $('Merge Transcript').all();\n"
    "\n"
    "return llmOutputs.map(function(item, i) {\n"
    "    const j = item.json;\n"
    "    const short = shorts[i] ? shorts[i].json : {};\n"
    "\n"
    "    // Extract text from various LLM output structures\n"
    "    let aiText = '';\n"
    "    if (j.output && Array.isArray(j.output) && j.output[0]?.content && Array.isArray(j.output[0].content)) {\n"
    "        // OpenAI node format\n"
    "        aiText = j.output[0].content[0].text || '';\n"
    "    } else if (j.content && Array.isArray(j.content)) {\n"
    "        aiText = j.content[0]?.text || '';\n"
    "    } else if (j.text) {\n"
    "        // chainLlm (Google Gemini) format\n"
    "        aiText = j.text;\n"
    "    } else if (j.message?.content) {\n"
    "        aiText = j.message.content;\n"
    "    }\n"
    "\n"
    "    if (typeof aiText !== 'string') {\n"
    "        aiText = JSON.stringify(aiText || '');\n"
    "    }\n"
    "\n"
    "    let score = {};\n"
    "    const match = aiText.match(/\\{[\\s\\S]*\\}/);\n"
    "    if (match) {\n"
    "        try { score = JSON.parse(match[0]); } catch(e) {}\n"
    "    }\n"
    "\n"
    "    return {\n"
    "        json: {\n"
    "            video_id: short.video_id || '',\n"
    "            url: short.url || '',\n"
    "            title: short.title || '',\n"
    "            channel_title: short.channel_title || '',\n"
    "            duration: short.duration || null,\n"
    "            theme: short.theme || '',\n"
    "            view_count: short.view_count || null,\n"
    "            outlier_score: short.outlier_score !== undefined ? short.outlier_score : null,\n"
    "            engagement_rate: short.engagement_rate !== undefined ? short.engagement_rate : null,\n"
    "            transcript: short.transcript || null,\n"
    "            score: typeof score.score === 'number' ? score.score : 0,\n"
    "            reason: score.reason || '',\n"
    "            should_rewrite: score.should_rewrite === true\n"
    "        }\n"
    "    };\n"
    "});"
)

# ── 9. Update Format Telegram Message to include outlier_score ───────────────
fmt = find_node("Format Telegram Message")
if fmt:
    fmt["parameters"]["jsCode"] = (
        "const data = $input.all()[0]?.json;"
        "if (!data || !data.title || data.title.length === 0) {\n"
        '    return [{ json: { text: "\ud83d\udd0d \u0420\u0435\u043b\u0435\u0432\u0430\u043d\u0442\u043d\u044b\u0445 \u0448\u043e\u0440\u0442\u0441\u043e\u0432 \u043f\u043e \u0437\u0430\u0434\u0430\u043d\u043d\u043e\u0439 \u0442\u0435\u043c\u0430\u0442\u0438\u043a\u0435 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e \ud83d\ude14" } }];\n'
        "}\n"
        "const messages = [];\n"
        "let part = 1;\n"
        "let currentMessage = `\ud83c\udfa5 *\u041d\u0430\u0439\u0434\u0435\u043d\u044b \u0440\u0435\u043b\u0435\u0432\u0430\u043d\u0442\u043d\u044b\u0435 \u0448\u043e\u0440\u0442\u0441\u044b! (\u0427\u0430\u0441\u0442\u044c ${part})*\\n\\n`;\n"
        "for (let i = 0; i < data.title.length; i++) {\n"
        "    const title = String(data.title[i]).replace(/[*_\\[\\]`]/g, '').substring(0, 80);\n"
        "    const url = data.url[i];\n"
        "    const score = data.score[i];\n"
        "    const reason = String(data.reason[i] || '').replace(/[*_\\[\\]`]/g, '').substring(0, 180);\n"
        "    const channel = String(data.channel_title[i] || 'Unknown').replace(/[*_\\[\\]`]/g, '');\n"
        "    const duration = data.duration[i];\n"
        "    const views = data.view_count && data.view_count[i] ? Number(data.view_count[i]).toLocaleString('ru-RU') : '?';\n"
        "    const outlier = data.outlier_score && data.outlier_score[i] ? ` | \ud83d\udcc8 ${data.outlier_score[i]}x` : '';\n"
        "    const block = `\u2705 *${title}*\\n\ud83d\udcfa ${channel} | \u23f1 ${duration}s | \ud83d\udc41 ${views}${outlier} | \ud83c\udfaf ${score}/10\\n\ud83d\udd17 ${url}\\n\ud83d\udcac ${reason}\\n\\n`;\n"
        "    if ((currentMessage.length + block.length) > 3800) {\n"
        "        messages.push({ json: { text: currentMessage } });\n"
        "        part++;\n"
        "        currentMessage = `\ud83c\udfa5 *\u041f\u0440\u043e\u0434\u043e\u043b\u0436\u0435\u043d\u0438\u0435 (\u0427\u0430\u0441\u0442\u044c ${part}):*\\n\\n`;\n"
        "    }\n"
        "    currentMessage += block;\n"
        "}\n"
        "if (currentMessage.replace(/\\*.*?\\*/g, '').trim().length > 0) {\n"
        "    messages.push({ json: { text: currentMessage } });\n"
        "}\n"
        "return messages;"
    )

# ── 10. Update connections ────────────────────────────────────────────────────
conns = v4["connections"]

# Merge Transcript now goes to LLM Router
conns["Merge Transcript"] = {
    "main": [[{"node": "LLM Router", "type": "main", "index": 0}]]
}

# Rename Score Relevance key in connections
if "Score Relevance" in conns:
    conns["Score Relevance (OpenAI)"] = conns.pop("Score Relevance")

# LLM Router: branch 0 -> OpenAI, branch 1 (fallback) -> Google
conns["LLM Router"] = {
    "main": [
        [{"node": "Score Relevance (OpenAI)", "type": "main", "index": 0}],
        [{"node": "Score Relevance (Google)", "type": "main", "index": 0}],
    ]
}

# Score Relevance (Google) -> Parse Score
conns["Score Relevance (Google)"] = {
    "main": [[{"node": "Parse Score", "type": "main", "index": 0}]]
}

# Google Gemini Model sub-node -> Score Relevance (Google) via ai_languageModel
conns["Google Gemini Model"] = {
    "ai_languageModel": [
        [{"node": "Score Relevance (Google)", "type": "ai_languageModel", "index": 0}]
    ]
}

with open("workflow_v4.json", "w", encoding="utf-8") as f:
    json.dump(v4, f, ensure_ascii=True, indent=2)

print("workflow_v4.json created successfully")
print("Nodes:", len(v4["nodes"]))
print("Node names:", [n["name"] for n in v4["nodes"]])
print()
print("Connections:")
for src, conns_out in v4["connections"].items():
    for type_key, branches in conns_out.items():
        for i, branch in enumerate(branches):
            for t in branch:
                print(f"  {src} [{type_key}:{i}] -> {t['node']}")
