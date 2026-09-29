#!/usr/bin/env python3
"""
Builds knowledge_graph.json from the client's Word knowledge base.

  python build_knowledge_graph.py Private_Parking_AI_Legal_Knowledge_Base_COMPLETE_V2.docx

Re-run this EVERY time the Word document changes, then run kb_consistency_check.py.
Never edit knowledge_graph.json by hand - it is the answer key the check compares against.
Needs: pip install python-docx
"""
import sys
from pathlib import Path
DOCX = sys.argv[1] if len(sys.argv) > 1 else "Private_Parking_AI_Legal_Knowledge_Base_COMPLETE_V2.docx"
OUT = sys.argv[2] if len(sys.argv) > 2 else "knowledge_graph.json"
import docx, json, re
from docx.table import Table
from docx.text.paragraph import Paragraph
d = docx.Document(DOCX)
items=[]
for el in d.element.body.iterchildren():
    tag=el.tag.split('}')[1]
    if tag=='p':
        p=Paragraph(el,d); t=p.text.strip()
        if t: items.append(('p',p.style.name,t))
    elif tag=='tbl':
        tb=Table(el,d)
        rows=[[c.text.strip() for c in r.cells] for r in tb.rows]
        items.append(('t',None,rows))

import json, re, csv
nodes={}; edges=[]
def node(id,type,label,**kw):
    nodes[id]=dict(id=id,type=type,label=label,**kw); return id
def edge(s,r,t,prov,detail=None):
    e=dict(source=s,relation=r,target=t,provenance=prov)
    if detail: e['detail']=detail
    edges.append(e)

ROOT=node('KB','knowledge_base','Private Parking AI Legal Knowledge Base',
          text=[])
sec=None; cur_h2=None; secnum=0
pre=[]
for kind,style,val in items:
    if kind=='p' and style=='Heading 1':
        secnum+=1
        m=re.match(r'^(\d+)\.\s*(.*)$',val)
        sid = f"SEC-{m.group(1)}" if m else "SEC-"+val.split(' - ')[0].replace(' ','-')
        sec=node(sid,'section',val,order=secnum,text=[])
        edge(ROOT,'HAS_SECTION',sec,'explicit')
        cur_h2=None; continue
    if sec is None:
        nodes[ROOT]['text'].append(val); continue
    if kind=='p' and style=='Heading 2':
        mid,title=val.split(' - ',1)
        t='module' if mid.startswith('KB-') else 'drafting_block'
        cur_h2=node(mid,t,title,fields={} if t=='module' else None,text=[])
        if t=='drafting_block': del nodes[mid]['fields']
        edge(sec,'CONTAINS',cur_h2,'explicit'); continue
    if kind=='p':
        target = cur_h2 if (cur_h2 and nodes[cur_h2]['type']=='drafting_block') else sec
        if style=='List Bullet':
            n=len([e for e in edges if e['source']==sec])+1
            cid=node(f"CHK-{n:02d}",'checklist_item',val)
            edge(sec,'CONTAINS',cid,'explicit')
        else:
            nodes[target]['text'].append(val)
        continue
    # table
    header=val[0]; rows=val[1:]
    title=nodes[sec]['label']
    if cur_h2 and nodes[cur_h2]['type']=='module':
        for f,v in rows: nodes[cur_h2]['fields'][f]=v
        cur_h2=None; continue
    typ={'1. Knowledge Base Governance':'governance_rule','2. Source Register':'source',
         '16. Drafting Priority and Suppression Rules':'drafting_priority',
         '17. Mandatory Validator Rules':'validator','18. Developer Retrieval Output Schema':'schema_field',
         'APPENDIX B - AI Ground Selection Matrix':'selection_rule'}[title]
    for i,(a,b) in enumerate(rows,1):
        if typ=='source': nid=f"SRC-{i}"; lab=a; extra={'use':b}
        elif typ=='drafting_priority': nid=f"PRI-{a}"; lab=f"Priority {a}"; extra={'instruction':b}
        elif typ=='selection_rule': nid=f"SEL-{i:02d}"; lab=a; extra={'case_pattern':a,'priority':b}
        elif typ=='governance_rule': nid=a; lab=a; extra={'requirement':b}
        elif typ=='validator': nid=a; lab=a; extra={'block_release_if':b}
        else: nid='SCHEMA-'+a; lab=a; extra={'example':b}
        node(nid,typ,lab,**extra, column_headers=header)
        edge(sec,'CONTAINS',nid,'explicit')

# Legal basis edges (explicit text in LEGAL / CODE BASIS field)
FR=node('SRC-X1','source','Common-law frustration/impossibility principles',
        note='Not listed in the Source Register (Section 2); named only in the LEGAL / CODE BASIS field of a module.')
code_note=[t for t in nodes['SEC-2']['text'] if 'Single Code' in t]
nodes['SRC-2']['implementation_note']=code_note[0]
for n in list(nodes.values()):
    if n['type']!='module': continue
    b=n['fields'].get('LEGAL / CODE BASIS')
    if not b: continue
    hits=[]
    if 'PoFA' in b or 'Protection of Freedoms' in b: hits.append('SRC-1')
    if 'Code' in b: hits.append('SRC-2')
    if 'Equality Act' in b: hits.append('SRC-3')
    if 'frustration' in b: hits.append('SRC-X1')
    assert hits, b
    for h in hits: edge(n['id'],'HAS_LEGAL_BASIS',h,'explicit',b)

# Schema example references
for m in re.findall(r'KB-[A-Z]+-\d+',nodes['SCHEMA-module_ids']['example']):
    edge('SCHEMA-module_ids','EXAMPLE_REFERENCES',m,'explicit')

# Derived: drafting block -> topic section by shared ID prefix
PREF={'POFA':'SEC-3','CON':'SEC-4','GRACE':'SEC-4','PAY':'SEC-5','KEY':'SEC-5','ANPR':'SEC-6',
      'SIGN':'SEC-7','AUTH':'SEC-8','BREAK':'SEC-9','RES':'SEC-10','EQ':'SEC-11','HOSP':'SEC-12',
      'ACT':'SEC-13','EVCH':'SEC-14','LAND':'SEC-15'}
for n in nodes.values():
    if n['type']=='drafting_block':
        p=n['id'].split('-')[1]
        if p in PREF:
            edge(n['id'],'SAME_TOPIC_PREFIX_AS',PREF[p],'derived',
                 f"Block ID prefix '{p}' matches module IDs in this section")
# sanity
ids=set(nodes)
for e in edges: assert e['source'] in ids and e['target'] in ids, e
from collections import Counter
print(Counter(n['type'] for n in nodes.values())); print(len(nodes),len(edges))
print(Counter((e['relation'],e['provenance']) for e in edges))
json.dump({'title':nodes['KB']['label'],'source_file':Path(DOCX).name,
           'nodes':list(nodes.values()),'edges':edges},open(OUT,'w',encoding='utf-8'),indent=1,ensure_ascii=False)