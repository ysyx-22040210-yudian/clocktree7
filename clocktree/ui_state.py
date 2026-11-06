"""Small independent UI preferences; changing a search never writes graph data."""
import hashlib
import json
from pathlib import Path
from .storage import write_graph


def design_key(config):
    design=config.get('design',{})
    identity={'tree_type':config.get('tree_type','clock'),
              'design':{k:design.get(k,'') for k in ('top','elab','filelist','base_dir')}}
    return hashlib.sha256(json.dumps(identity,sort_keys=True).encode('utf-8')).hexdigest()


class UIState:
    def __init__(self,path):
        self.path=Path(path)
        try:self.data=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):self.data={}

    def get(self,config):
        return self.data.get(design_key(config),{})

    def update(self,config,**fields):
        self.data.setdefault(design_key(config),{}).update(fields)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        write_graph(self.path,self.data)

    def search(self,config,query):
        query=query.strip()
        if not query:return
        values=[query]+[v for v in self.get(config).get('searches',[]) if v!=query]
        self.update(config,searches=values[:100])
