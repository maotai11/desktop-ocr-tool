"""Measure the actual application engine; run inside an OS network block.

python scripts/benchmark_runtime.py --report result.json --iterations 500
No accuracy claim: this repeated, generated numeric crop is a lifecycle/RSS probe.
"""
import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
P=argparse.ArgumentParser();P.add_argument('--report',required=True);P.add_argument('--iterations',type=int,default=500);A=P.parse_args()
tstart=time.perf_counter()
import psutil
import numpy as np
from PIL import Image,ImageDraw,ImageFont
from src.ocr.engine import OcrEngine
pid=int(next(line for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('Tgid:')).split()[1]) if sys.platform=='linux' else os.getpid()
process=psutil.Process(pid);trace=[];stop=threading.Event();phase='load'
def sample():
    while not stop.wait(.02):
        trace.append([time.perf_counter()-tstart,process.memory_info().rss,process.num_threads(),phase])
sampler=threading.Thread(target=sample,daemon=True);sampler.start()
t=time.perf_counter();engine=OcrEngine();engine.load();load=time.perf_counter()-t
img=Image.new('RGB',(160,160),'white');ImageDraw.Draw(img).text((10,55),'12345',font=ImageFont.load_default(size=32),fill='black')
array=np.asarray(img)[:,:,::-1].copy();times=[];checkpoints={};failures=[]
try:
    for i in range(A.iterations):
        phase=f'ocr:{i+1}';t=time.perf_counter();r=engine.run_ocr(array);times.append((time.perf_counter()-t)*1000)
        if r.get('text')!='12345': failures.append({'iteration':i+1,'text':r.get('text'),'status':r.get('status')})
        if i+1 in (1,10,100,500):
            checkpoints[str(i+1)]={'rss_bytes':process.memory_info().rss,'native_threads':process.num_threads()}
            print(i+1,checkpoints[str(i+1)],flush=True)
finally:
    stop.set();sampler.join()
report=dict(iterations=len(times),input_hw=[160,160],load_seconds=load,
            first_ocr_ms=times[0],warm_percentiles_ms={f'p{p}':float(np.percentile(times[1:],p)) for p in (50,95,99)},
            checkpoints=checkpoints,peak_rss_bytes=max(x[1] for x in trace),rss_end_bytes=process.memory_info().rss,
            cpu_seconds=process.cpu_times().user+process.cpu_times().system,
            times_ms=times,failures=failures,trace_fields=['elapsed_s','rss_bytes','threads','phase'],trace=trace,
            limitation='Repeated numeric crop only; not field CER, cold OS-cache startup, or leak-freedom proof')
Path(A.report).write_text(json.dumps(report,indent=2),encoding='utf-8')
