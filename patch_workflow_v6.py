"""
Patch workflow_v5.json → workflow_v6.json

Changes:
  1. Aggregate Results — add 'transcript' and 'transcript_source' fields
  2. New node: Build Transcript File — code node that creates binary .txt
  3. New node: Send Transcript File — Telegram sendDocument node
  4. Connections — Aggregate fans out to both Format (existing) and Build Transcript File (new)
"""
from __future__ import annotations
import copy, json, uuid

src = json.load(open("workflow_v5.json", encoding="utf-8"))
w = copy.deepcopy(src)
nodes_by_name: dict = {n["name"]: n for n in w["nodes"]}


# ── 1. Add transcript + transcript_source to Aggregate Results ─────────────────
agg_fields = nodes_by_name["Aggregate Results"]["parameters"]["fieldsToAggregate"]["fieldToAggregate"]
existing_names = {f["fieldToAggregate"] for f in agg_fields}
for field_name in ["transcript", "transcript_source", "outlier_score"]:
    if field_name not in existing_names:
        agg_fields.append({"fieldToAggregate": field_name})


# ── 2. New node: Build Transcript File ────────────────────────────────────────
build_file_node = {
    "parameters": {
        "jsCode": r"""
// Build a UTF-8 text file with all transcripts and attach it as binary data.
const data = $input.all()[0]?.json;

if (!data || !data.title || !data.title.length) {
    const empty = Buffer.from('Нет результатов для транскрипции.', 'utf-8').toString('base64');
    return [{
        json: { caption: '📄 Транскрипций нет' },
        binary: { transcript_file: { data: empty, mimeType: 'text/plain', fileName: 'transcripts.txt' } }
    }];
}

const now = new Date().toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' });
const theme = (data.theme && data.theme[0]) ? data.theme[0] : '—';
const lines = [];

lines.push('ТРАНСКРИПЦИИ РЕЛЕВАНТНЫХ YOUTUBE SHORTS');
lines.push('='.repeat(60));
lines.push('Дата поиска: ' + now);
lines.push('Тема: ' + theme);
lines.push('Найдено видео: ' + data.title.length);
lines.push('='.repeat(60));
lines.push('');

for (let i = 0; i < data.title.length; i++) {
    lines.push('[' + (i + 1) + '] ' + (data.title[i] || '—'));
    lines.push('    Канал:   ' + (data.channel_title?.[i] || '—'));
    lines.push('    Ссылка:  ' + (data.url?.[i] || '—'));
    lines.push('    Длина:   ' + (data.duration?.[i] || '?') + 's');
    lines.push('    Оценка:  ' + (data.score?.[i] || '?') + '/10');
    const outlier = data.outlier_score?.[i];
    if (outlier) lines.push('    Вирал.:  ' + outlier + 'x');
    lines.push('    Причина: ' + (data.reason?.[i] || '—'));
    lines.push('    Источник транскрипции: ' + (data.transcript_source?.[i] || '—'));
    lines.push('');
    lines.push('    --- ТРАНСКРИПЦИЯ ---');

    const transcript = data.transcript?.[i];
    if (transcript && transcript.trim()) {
        // Word-wrap at ~80 chars
        const words = transcript.trim().split(/\s+/);
        let line = '    ';
        for (const word of words) {
            if (line.length + word.length + 1 > 84) {
                lines.push(line.trimEnd());
                line = '    ' + word + ' ';
            } else {
                line += word + ' ';
            }
        }
        if (line.trim()) lines.push(line.trimEnd());
    } else {
        lines.push('    (транскрипция недоступна)');
    }

    lines.push('');
    lines.push('-'.repeat(60));
    lines.push('');
}

const content = lines.join('\n');
const base64 = Buffer.from(content, 'utf-8').toString('base64');
const dateStr = new Date().toISOString().slice(0, 10);

return [{
    json: { caption: '\uD83D\uDCCB Транскрипции: ' + data.title.length + ' видео по теме: ' + theme },
    binary: {
        transcript_file: {
            data: base64,
            mimeType: 'text/plain',
            fileName: 'transcripts_' + dateStr + '.txt',
        }
    }
}];
""",
        "mode": "runOnceForAllItems",
    },
    "type": "n8n-nodes-base.code",
    "typeVersion": 2,
    "position": [2840, 240],
    "id": str(uuid.uuid4()),
    "name": "Build Transcript File",
}


# ── 3. New node: Send Transcript File ─────────────────────────────────────────
# Get the chatId from the existing Send to Telegram node
chat_id = nodes_by_name["Send to Telegram"]["parameters"].get("chatId", "909658267")

send_file_node = {
    "parameters": {
        "resource": "message",
        "operation": "sendDocument",
        "chatId": chat_id,
        "binaryData": True,
        "binaryPropertyName": "transcript_file",
        "additionalFields": {
            "caption": "={{ $json.caption }}",
        },
    },
    "type": "n8n-nodes-base.telegram",
    "typeVersion": 1,
    "position": [3080, 240],
    "id": str(uuid.uuid4()),
    "name": "Send Transcript File",
}

w["nodes"].append(build_file_node)
w["nodes"].append(send_file_node)


# ── 4. Connections — fan out from Aggregate Results ───────────────────────────
# Aggregate Results already connects to Format Telegram Message (index 0 → [0])
# Add Build Transcript File as a second parallel output on the same port
existing_agg_conns = w["connections"]["Aggregate Results"]["main"][0]
existing_agg_conns.append({"node": "Build Transcript File", "type": "main", "index": 0})

# Build Transcript File → Send Transcript File
w["connections"]["Build Transcript File"] = {
    "main": [[{"node": "Send Transcript File", "type": "main", "index": 0}]]
}


# ── Save ──────────────────────────────────────────────────────────────────────
out_path = "workflow_v6.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(w, f, indent=2, ensure_ascii=True)

print(f"Saved {out_path}")
print(f"Nodes ({len(w['nodes'])}): {[n['name'] for n in w['nodes']]}")
print()
print("Aggregate Results connections:", w["connections"]["Aggregate Results"]["main"][0])
