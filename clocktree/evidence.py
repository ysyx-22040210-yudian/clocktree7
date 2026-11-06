"""Readable, bounded excerpts of immutable connection evidence."""


def mapping_excerpt(edge, limit=8):
    evidence=edge.get('evidence',{})
    reverse=bool(evidence.get('driver'))
    path=evidence.get('driver') if reverse else evidence.get('load',[])
    if not path:return ''
    total=len(path)
    steps=path.tail(limit) if hasattr(path,'tail') else path[-limit:]
    names=[]
    for step in steps:
        name=step.get('name') or step.get('net')
        if name and (not names or names[-1]!=name):names.append(name)
    if not names:return ''
    label='反查映射' if reverse else '负载映射'
    if total>limit:label+='（末端 {} / {} 步，完整路径见原始证据）'.format(limit,total)
    return label+'：\n'+(' ← ' if reverse else ' → ').join(names)
