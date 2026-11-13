"""User-supplied per-layer reset metadata from each branch's first device."""
import re


RESET_PATH=re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*(?:\[-?\d+(?::-?\d+)?\])*(?:\.[A-Za-z_$][A-Za-z0-9_$]*(?:\[-?\d+(?::-?\d+)?\])*)*\Z')


def validate_reset_signal(value,allow_incomplete=False):
    if not isinstance(value,str):raise ValueError('层次复位信号 reset_signal 必须是字符串')
    if value.strip() and not allow_incomplete and not RESET_PATH.fullmatch(value.strip()):
        raise ValueError('层次复位信号请填写信号名或完整 RTL 路径，可含静态 bit；逻辑运算请写在 SV 模板中')


def reset_binding(node,config):
    """Resolve one concrete origin device's configured layer reset."""
    index=node.get('layer');layers=config.get('layers',[])
    if type(index) is not int or not 1<=index<=len(layers) or layers[index-1].get('index')!=index:
        return {'error':'起始器件缺少有效层级，无法选择该层复位信号'}
    layer=layers[index-1];value=layer.get('reset_signal','')
    info={'instance':node.get('id',''),'layer':index,'hierarchy':layer.get('hierarchy',''),'configured':value,'origin':'user_config'}
    if not isinstance(value,str) or not value.strip():
        return dict(info,error='L{} 未填写复位信号，请在该层 RTL 父层路径下填写'.format(index))
    value=value.strip()
    if not RESET_PATH.fullmatch(value):return dict(info,error='L{} 复位信号不是有效的静态 RTL 信号路径'.format(index))
    instance=node.get('id','');parent=instance.rpartition('.')[0]
    top=config.get('design',{}).get('top','')
    roots=[root for root in (top,instance.split('.')[0]) if root]
    absolute=value.startswith('$root.') or any(value.startswith(root+'.') for root in roots)
    if not absolute:
        # A wildcard rule still uses each concrete first device's real parent.
        parent=parent or layer.get('hierarchy','').strip().rstrip('./') or top
        value=parent+'.'+value if parent else value
    if not RESET_PATH.fullmatch(value):return dict(info,error='L{} 复位信号无法确定完整 RTL 路径，请填写绝对信号路径'.format(index))
    return dict(info,value=value)


def branch_reset_binding(roots,config):
    bindings=[reset_binding(node,config) for node in roots]
    if not bindings:return {'basis':'branch_origin','error':'无法确定该分支最初器件，无法选择复位信号'}
    if len(bindings)==1:return dict(bindings[0],basis='branch_origin')
    result={'basis':'branch_origin','origin':'user_config','origins':bindings}
    values={item['value'] for item in bindings if 'value' in item}
    if len(values)==1 and all('value' in item for item in bindings):result['value']=next(iter(values))
    else:
        details=['{} (L{}): {}'.format(item.get('instance','?'),item.get('layer','?'),item.get('value',item.get('error',''))) for item in bindings]
        result['error']='该分支有多个最初器件，复位不同或未填写，无法唯一确定：'+'; '.join(details)
    return result
