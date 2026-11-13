"""One replaceable layout subprocess. No Tk calls occur outside its owner."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


class LayoutJob:
    def __init__(self, model):
        self.directory=tempfile.TemporaryDirectory(prefix='ct7-layout-')
        base=Path(self.directory.name)
        self.output=base/'result.json'
        self.log=(base/'error.log').open('w',encoding='utf-8')
        payload={'nodes':model.nodes,'edges':[{k:e[k] for k in ('id','source','target','source_pin','target_pin')} for e in model.edges],
                 'layer_count':model.layer_count,'layout_mode':getattr(model,'layout_mode','branch')}
        # Aggregate membership sequences and evidence stay in the parent index;
        # the layout worker only needs shape geometry and connectivity.
        payload['nodes']={key:{k:v for k,v in n.items() if k in ('id','inputs','output','outputs','kind','kind_counts','layer','component','aggregate','expand_bus','aux_ports')}
                          for key,n in model.nodes.items()}
        (base/'request.json').write_text(json.dumps(payload),encoding='utf-8')
        kwargs={'cwd':str(Path(__file__).resolve().parents[1]),'stdout':self.log,'stderr':self.log}
        if os.name=='nt':kwargs['creationflags']=getattr(subprocess,'CREATE_NO_WINDOW',0)
        self.process=subprocess.Popen([sys.executable,'-m','clocktree.layout',str(base/'request.json'),str(self.output)],**kwargs)

    def poll(self):
        return self.process.poll()

    def result(self):
        if self.process.returncode:
            self.log.flush()
            raise RuntimeError((Path(self.directory.name)/'error.log').read_text(encoding='utf-8'))
        data=json.loads(self.output.read_text(encoding='utf-8'))
        for key in ('layer_spans','layer_columns','component_tops'):
            data[key]={int(k):v for k,v in data[key].items()}
        return data

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait()
        self.log.close();self.directory.cleanup()
