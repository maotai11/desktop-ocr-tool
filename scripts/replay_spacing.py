"""Replay frozen v4 hypotheses: isolate whitespace changes from OCR inference.

Input is Phase 2 v4-app/pipeline.json. Uses the same annotated synthetic rows;
public-page partial annotations are excluded from transcript CER.
"""
import argparse,json,sys,unicodedata
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.ocr.postprocessor import sort_boxes_and_merge

def edits(gt,pred):
    gt=unicodedata.normalize('NFC',gt);pred=unicodedata.normalize('NFC',pred)
    a=[[0]*(len(pred)+1) for _ in range(len(gt)+1)]
    for i in range(len(gt)+1):a[i][0]=i
    for j in range(len(pred)+1):a[0][j]=j
    for i in range(1,len(gt)+1):
        for j in range(1,len(pred)+1):a[i][j]=min(a[i-1][j-1]+(gt[i-1]!=pred[j-1]),a[i-1][j]+1,a[i][j-1]+1)
    i,j=len(gt),len(pred);S=D=I=W=0
    while i or j:
        if i and j and a[i][j]==a[i-1][j-1]+(gt[i-1]!=pred[j-1]):
            if gt[i-1]!=pred[j-1]:S+=1;W+=int(gt[i-1].isspace() or pred[j-1].isspace())
            i-=1;j-=1
        elif i and a[i][j]==a[i-1][j]+1:D+=1;W+=int(gt[i-1].isspace());i-=1
        else:I+=1;W+=int(pred[j-1].isspace());j-=1
    return dict(N=len(gt),S=S,D=D,I=I,whitespace_edits=W,errors=S+D+I)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--baseline',required=True);p.add_argument('--report',required=True);args=p.parse_args()
    rows=[]
    for r in json.loads(Path(args.baseline).read_text())['rows']:
        if r['group'] not in ('synthetic_page',):continue
        # Bounds were scaled back to original coordinates in the frozen evidence.
        # Uniform scale does not change boundary gap/height ratios.
        new=sort_boxes_and_merge([dict(box=b,text=t,confidence=s) for b,t,s in zip(r['boxes'],r['converted_texts'],r['scores'])])
        gt=r['targets'][0]['text']
        rows.append(dict(id=r['id'],ground_truth=gt,before=r['merged_text'],after=new,
                         before_metrics=edits(gt,r['merged_text']),after_metrics=edits(gt,new)))
    if not rows:
        raise RuntimeError('No ground-truth rows selected; refusing an empty benchmark')
    totals={}
    for label in ('before','after'):
        sums={key:sum(r[label+'_metrics'][key] for r in rows) for key in ('N','S','D','I','whitespace_edits','errors')}
        sums['CER']=sums['errors']/max(1,sums['N']);totals[label]=sums
    Path(args.report).write_text(json.dumps(dict(rows=rows,totals=totals,
        limitation='Frozen first-pass hypotheses only; not native end-to-end or detector improvement'),ensure_ascii=False,indent=2),encoding='utf-8')
