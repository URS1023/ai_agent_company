"""Ordinary visual nodes, with table selection and explicit field relationships."""
from copy import deepcopy


def field(name): return {'field': name}
def value(v): return {'value': v}
def expr(op, *args): return {'op': op, 'args': list(args)}
def compare(op, name, v): return expr(op, field(name), value(v))
def missing(*names): return expr('or', *[expr('missing', field(name)) for name in names])
def calc(name, operation, left=None, right=None, *, constant=False, mapping=None):
    result = {'name': name, 'operation': operation, 'left': {'source':'constant','value':left} if constant or operation=='now' else {'source':'field','field':left}}
    if right is not None: result['right'] = {'source':'field','field':right} if isinstance(right,str) else {'source':'constant','value':right}
    if mapping is not None: result['mapping'] = mapping
    return result


class GraphBuilder:
    def __init__(self, source, *, quality=False):
        self.source, self.quality = source, quality
        self.nodes, self.edges, self.results = [], [], []
        self.add('start','start','开始',{'variables':[]},parent=None)

    def add(self, key, kind, title, config=None, parent=None, port='out'):
        self.nodes.append({'id':key,'kind':kind,'title':title,'config':config or {},'inputs':{},'position':{'x':0,'y':0}})
        if parent: self.edge(parent,key,port)
        return key

    def edge(self, source, target, port='out'):
        self.edges.append({'id':f'{source}_{port}_{target}','source':source,'target':target,'port':port,
                           'sourceSide':'right','targetSide':'left'})

    def read(self,key,title,table,columns,parent='start',*,filters=(),matches=None,mode='all',order=(),port='out'):
        config={'readMode':'table','datasourceId':self.source['id'],'datasourceVersion':self.source['version'],
                'tableName':table,'fields':list(columns),'fieldAliases':columns,
                'filters':[{'field':'isDeleted','value':0},*[{'field':f,'operator':op,'value':v} for f,op,v in filters]],
                'maxRows':10000}
        if matches:
            config.update(matchMode=mode,matches=[{'field':f,'inputField':v} for f,v in matches.items()],
                          matchCountField=title.removeprefix('读取值 · ')+'匹配数')
        if order: config['orderBy']=[{'field':f,'direction':'desc'} for f in order]
        return self.add(key,'read-values',title,config,parent,port)

    def calculate(self,key,title,calculations,parent,port='out'):
        return self.add(key,'calculate-values',title,{'calculations':calculations},parent,port)

    def condition(self,key,title,expression,parent,port='out'):
        return self.add(key,'condition',title,{'mode':'rows','expression':expression},parent,port)

    def output(self,key,title,status,message,parent,port='out',fields=None):
        self.add(key,'output-values',title,{'mode':'quality' if self.quality else 'alert',
                 'conclusion':status,'message':message,**({'fields':fields} if fields else {})},parent,port)
        self.results.append(key)
        return key

    def check(self,key,title,parent,invalid,violation,*,pending,warning,normal,fields=None):
        gate=self.condition(key+'_ready','判断值 · '+title+'依据是否齐全',invalid,parent)
        self.output(key+'_pending','输出 · '+title+' · 待确认','pending',pending,gate,'true',fields)
        check=self.condition(key+'_test','判断值 · '+title,violation,gate,'false')
        self.output(key+'_warning','输出 · '+title+(' · 不合格' if self.quality else ' · 预警'),'warning',warning,check,'true',fields)
        self.output(key+'_normal','输出 · '+title+(' · 合格' if self.quality else ' · 正常'),'normal',normal,check,'false',fields)

    def finish(self,report=None):
        self.add('results','merge','汇总全部检查结果')
        for key in self.results: self.edge(key,'results')
        tail='results'
        if report:
            tail=self.add('report','alert-evaluate','保存设备预警结果',report,'results')
        self.add('end','end','结束 · 查看结果',parent=tail)
        self.layout()
        return {'nodes':self.nodes,'edges':self.edges}

    def layout(self):
        # Layered layout: each branch occupies a separate lane; outputs never
        # connect to another output, and all arrows attach to target handles.
        rank={'start':0}
        pending={n['id'] for n in self.nodes}-{ 'start' }
        while pending:
            ready=[k for k in pending if all(e['source'] in rank for e in self.edges if e['target']==k)]
            if not ready: raise ValueError('Configuration has a dependency cycle')
            for key in ready:
                rank[key]=max([rank[e['source']]+1 for e in self.edges if e['target']==key] or [0]);pending.remove(key)
        levels={}
        for node in self.nodes: levels.setdefault(rank[node['id']],[]).append(node)
        positions={}
        for level,nodes in sorted(levels.items()):
            nodes.sort(key=lambda n: sum(positions.get(e['source'],0) for e in self.edges if e['target']==n['id']) / max(1,sum(e['target']==n['id'] for e in self.edges)))
            occupied=[]
            for node in nodes:
                parents=[positions[e['source']] for e in self.edges if e['target']==node['id']]
                y=sum(parents)/len(parents) if parents else 0
                while any(abs(y-old)<210 for old in occupied): y+=230
                occupied.append(y);positions[node['id']]=y
                node['position']={'x':level*345,'y':round(y)}
        for edge in self.edges:
            # The persisted sides bind both line and SVG marker after reload.
            edge.update(sourceSide='right',targetSide='left')
