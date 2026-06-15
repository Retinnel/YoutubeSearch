import json
p='D:/Interexy/youtube search/workflow_v7.json'
with open(p,'r',encoding='utf-8') as f:
    wf=json.load(f)
changed=False
# change max_results
for node in wf.get('nodes',[]):
    if isinstance(node.get('parameters'),dict):
        jb=node['parameters'].get('jsonBody')
        if isinstance(jb,str) and 'max_results' in jb:
            new=jb.replace('"max_results": 30','"max_results": 5')
            if new!=jb:
                node['parameters']['jsonBody']=new
                changed=True
# Increase timeout options for Get Transcript nodes
for node in wf.get('nodes',[]):
    if isinstance(node.get('parameters'),dict):
        url=node['parameters'].get('url')
        if isinstance(url,str) and '/api/v1/get_transcript' in url:
            opts=node['parameters'].get('options') or {}
            # set several likely timeout keys
            opts['timeout']=600000
            opts['requestTimeout']=600000
            opts['responseTimeout']=600000
            node['parameters']['options']=opts
            changed=True
if changed:
    with open(p,'w',encoding='utf-8') as f:
        json.dump(wf,f,indent=2,ensure_ascii=False)
    print('workflow_v7.json updated')
else:
    print('no changes')
