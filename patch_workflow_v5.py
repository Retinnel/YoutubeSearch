"""
Patch workflow_v4.json → workflow_v5.json

Changes:
  1. Parse Queries      — add all_queries[] array to each output item
  2. Enrich Shorts      — include all_queries in each short
  3. Merge Transcript   — pass all_queries through
  4. Score Relevance    — add all_queries context to both OpenAI and Google prompts
  5. Config node        — add FORCE_WHISPER_AFTER_FILTER flag
  6. New node           — Force Transcribe (Whisper) after Filter Score >= 7
  7. New node           — Update Whisper Transcript after Force Transcribe
  8. Connections        — Filter → ForceTrans → UpdateTrans → Aggregate
  9. Positions          — shift Aggregate/Format/Send right to make room
"""
from __future__ import annotations
import copy, json, uuid

src = json.load(open("workflow_v4.json", encoding="utf-8"))
w = copy.deepcopy(src)

nodes_by_name: dict = {n["name"]: n for n in w["nodes"]}


# ── 1. Parse Queries — add all_queries to each output item ─────────────────────
nodes_by_name["Parse Queries"]["parameters"]["jsCode"] = """\
const j = $input.all()[0].json;
let aiText = "";if (j.output && Array.isArray(j.output) && j.output[0].content && Array.isArray(j.output[0].content)) {
    aiText = j.output[0].content[0].text || "";
} else if (j.content && Array.isArray(j.content)) {
    aiText = j.content[0]?.text || "";
} else if (j.text) {
    aiText = j.text;
} else if (j.message?.content) {
    aiText = j.message.content;
}if (typeof aiText !== 'string') {
    aiText = JSON.stringify(aiText || "");
}let queries = [];
let theme = "";
const jsonMatch = aiText.match(/\\{[\\s\\S]*\\}/);if (jsonMatch) {
    try {
        const parsed = JSON.parse(jsonMatch[0]);
        if (parsed.related_search_queries && Array.isArray(parsed.related_search_queries)) {
            queries = parsed.related_search_queries;
        } else {
            for (const key in parsed) {
                if (Array.isArray(parsed[key])) {
                    queries = parsed[key];
                    break;
                }
            }
        }
        theme = parsed.theme || "";
    } catch(e) {
        console.log("Ошибка парсинга списка запросов");
    }
}

if (queries.length === 0) {
    queries = ["IT outsourcing shorts"];
}

return queries.map(q => ({ json: { query: q, theme: theme, all_queries: queries } }));"""


# ── 2. Enrich Shorts — include all_queries in each short ──────────────────────
nodes_by_name["Enrich Shorts"]["parameters"]["jsCode"] = """\
// runOnceForAllItems: receives all HTTP responses at once
// One item per query -> we enrich each and return N items for Split Out
//
// === LLM PROVIDER SWITCH ===
// Change LLM_PROVIDER below to switch between "openai" and "google" for scoring.
const LLM_PROVIDER = $('Config').first().json.llm_provider || "google";const allItems = $input.all();
const theme = $('Parse Queries').first().json.theme || '';
// Collect all search queries expanded by the LLM (shared across all branches)
const all_queries = $('Parse Queries').all().map(item => item.json.query).filter(Boolean);const results = [];
for (const item of allItems) {
    const data = item.json;
    const query = data.query || '';    const enrichedShorts = (data.shorts || []).map(function(s) {
        return {
            video_id: s.video_id || '',
            url: s.url || '',
            title: s.title || 'No title',
            channel_id: s.channel_id || null,
            channel_title: s.channel_title || null,
            duration: s.duration || null,
            search_query: query,
            theme: theme,
            all_queries: all_queries,
            view_count: s.view_count || null,
            outlier_score: s.outlier_score !== undefined ? s.outlier_score : null,
            engagement_rate: s.engagement_rate !== undefined ? s.engagement_rate : null,
            llm_provider: LLM_PROVIDER
        };
    });    results.push({ json: { shorts: enrichedShorts } });
}return results.length > 0 ? results : [{ json: { shorts: [] } }];"""


# ── 3. Merge Transcript — pass all_queries through ────────────────────────────
nodes_by_name["Merge Transcript"]["parameters"]["jsCode"] = """\
// runOnceForAllItems: pairs transcript responses with original shorts by index.
// HTTP Request preserves order, so index i in transcripts == index i in Split Shorts.
const transcripts = $input.all();
const shorts = $('Split Shorts').all();return transcripts.map(function(item, i) {
    const short = shorts[i] ? shorts[i].json : {};
    const t = item.json;
    return {
        json: {
            video_id: short.video_id || '',
            url: short.url || '',
            title: short.title || 'No title',
            channel_id: short.channel_id || null,
            channel_title: short.channel_title || null,
            duration: short.duration || null,
            search_query: short.search_query || '',
            theme: short.theme || '',
            all_queries: short.all_queries || [],
            view_count: short.view_count || null,
            outlier_score: short.outlier_score !== undefined ? short.outlier_score : null,
            engagement_rate: short.engagement_rate !== undefined ? short.engagement_rate : null,
            llm_provider: short.llm_provider || 'openai',
            transcript: t.transcript || null,
            transcript_source: t.source || null,
            transcript_error: t.error || null
        }
    };
});"""


# ── 4. Score Relevance (OpenAI) — add all_queries context ─────────────────────
score_openai_params = nodes_by_name["Score Relevance (OpenAI)"]["parameters"]
score_openai_params["responses"]["values"] = [
    {
        "role": "system",
        "content": (
            "=You are a content relevance analyst. The user is looking for YouTube Shorts about this theme: {{ $json.theme }}. "
            "Consider ALL search queries listed below when evaluating relevance — a video is relevant if it matches "
            "ANY of the queries, not just the one it was found under. "
            "Rate relevance 1-10. If transcript is unavailable, judge by title and channel name only. "
            "Return ONLY valid JSON, no markdown."
        ),
    },
    {
        "content": (
            "=Video title: {{ $json.title }}\n"
            "Channel: {{ $json.channel_title }}\n"
            "Duration: {{ $json.duration }}s\n"
            "Outlier score: {{ $json.outlier_score !== null ? $json.outlier_score + 'x (viral coefficient)' : 'unknown' }}\n\n"
            "All search queries (evaluate against all of them): {{ $json.all_queries && $json.all_queries.length ? $json.all_queries.join(' | ') : $json.search_query }}\n\n"
            "Transcript: {{ $json.transcript ? $json.transcript : \"(unavailable)\" }}\n\n"
            'Return JSON: {"score": <int 1-10>, "reason": "<brief explanation in Russian>", "should_rewrite": <true if score >= 7>}'
        ),
    },
]


# ── 5. Score Relevance (Google) — add all_queries context ─────────────────────
nodes_by_name["Score Relevance (Google)"]["parameters"]["text"] = (
    "=You are a content relevance analyst. The user is looking for YouTube Shorts about this theme: {{ $json.theme }}.\n"
    "Consider ALL search queries listed below when evaluating relevance — a video is relevant if it matches "
    "ANY of the queries, not just the one it was found under.\n\n"
    "Video title: {{ $json.title }}\n"
    "Channel: {{ $json.channel_title }}\n"
    "Duration: {{ $json.duration }}s\n"
    "Outlier score: {{ $json.outlier_score !== null ? $json.outlier_score + 'x (viral coefficient)' : 'unknown' }}\n\n"
    "All search queries (evaluate against all of them): {{ $json.all_queries && $json.all_queries.length ? $json.all_queries.join(' | ') : $json.search_query }}\n\n"
    "Transcript: {{ $json.transcript ? $json.transcript : \"(unavailable)\" }}\n\n"
    "Rate relevance 1-10. If transcript is unavailable, judge by title and channel name only.\n"
    'Return ONLY valid JSON, no markdown:\n{"score": <int 1-10>, "reason": "<brief explanation in Russian>", "should_rewrite": <true if score >= 7>}'
)


# ── 6. Config node — add FORCE_WHISPER_AFTER_FILTER flag ──────────────────────
nodes_by_name["Config"]["parameters"]["jsCode"] = """\
// =============================================
// LLM PROVIDER SWITCH - change value here only
// 'google' = Google Gemini (default)
// 'openai' = OpenAI GPT-4o-mini
// =============================================
const LLM_PROVIDER = "google";

// =============================================
// FORCE WHISPER AFTER FILTER
// true  = re-transcribe high-scoring videos with Whisper (better quality, slower)
//         Requires: WHISPER_ENABLED=true in .env and openai-whisper installed
// false = keep the transcript already obtained during the initial pipeline step
// =============================================
const FORCE_WHISPER_AFTER_FILTER = true;

const items = $input.all();
return items.map(function(item) {
    return { json: Object.assign({}, item.json, {
        llm_provider: LLM_PROVIDER,
        force_whisper_after_filter: FORCE_WHISPER_AFTER_FILTER
    }) };
});"""


# ── 7. Shift Aggregate / Format / Send right to make room ─────────────────────
SHIFT = 480  # pixels
for name in ["Aggregate Results", "Format Telegram Message", "Send to Telegram"]:
    n = nodes_by_name[name]
    n["position"][0] += SHIFT


# ── 8. New node: Force Transcribe (Whisper) ────────────────────────────────────
force_transcribe_node = {
    "parameters": {
        "method": "POST",
        "url": "http://youtube-api:8000/api/v1/get_transcript",
        "sendHeaders": True,
        "headerParameters": {
            "parameters": [
                {"name": "X-API-KEY", "value": "change_me_to_a_strong_secret"},
                {"name": "Content-Type", "value": "application/json"},
            ]
        },
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": '={"video_id": "{{ $json.video_id }}", "force_whisper": {{ $("Config").first().json.force_whisper_after_filter ? "true" : "false" }}}',
        "options": {},
    },
    "type": "n8n-nodes-base.httpRequest",
    "typeVersion": 4,
    "position": [2360, 0],
    "id": str(uuid.uuid4()),
    "name": "Force Transcribe (Whisper)",
    "notes": "Re-transcribes high-scoring videos with Whisper for better quality. Enable WHISPER_ENABLED=true in .env.",
}


# ── 9. New node: Update Whisper Transcript ────────────────────────────────────
update_transcript_node = {
    "parameters": {
        "jsCode": """\
// Merge Whisper transcript results back into the scored Short items.
// If Whisper succeeded, replace the transcript; otherwise keep the original.
const whisperResults = $input.all();
const filtered = $('Filter Score >= 7').all();

return whisperResults.map(function(item, i) {
    const original = filtered[i] ? filtered[i].json : {};
    const w = item.json;
    const newTranscript = w.transcript || null;
    return {
        json: Object.assign({}, original, {
            transcript: newTranscript || original.transcript,
            transcript_source: newTranscript ? (w.source || 'whisper') : original.transcript_source,
            whisper_error: w.error || null
        })
    };
});""",
        "mode": "runOnceForAllItems",
    },
    "type": "n8n-nodes-base.code",
    "typeVersion": 2,
    "position": [2600, 0],
    "id": str(uuid.uuid4()),
    "name": "Update Whisper Transcript",
}

w["nodes"].append(force_transcribe_node)
w["nodes"].append(update_transcript_node)


# ── 10. Rewire connections ────────────────────────────────────────────────────
# Filter Score >= 7 → Force Transcribe (Whisper)
w["connections"]["Filter Score >= 7"]["main"][0] = [
    {"node": "Force Transcribe (Whisper)", "type": "main", "index": 0}
]
# Force Transcribe (Whisper) → Update Whisper Transcript
w["connections"]["Force Transcribe (Whisper)"] = {
    "main": [[{"node": "Update Whisper Transcript", "type": "main", "index": 0}]]
}
# Update Whisper Transcript → Aggregate Results
w["connections"]["Update Whisper Transcript"] = {
    "main": [[{"node": "Aggregate Results", "type": "main", "index": 0}]]
}


# ── Save ──────────────────────────────────────────────────────────────────────
out_path = "workflow_v5.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(w, f, indent=2, ensure_ascii=True)

print(f"Saved {out_path}")
node_names = [n["name"] for n in w["nodes"]]
print(f"Nodes ({len(node_names)}): {node_names}")
