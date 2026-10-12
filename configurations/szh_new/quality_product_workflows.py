"""Bind ordinary quality nodes to one source template; never infer a product."""
from copy import deepcopy

from quality_value_workflows import build_quality


def scoped_query(sql):
    # Template ownership is the only populated product relation on this import.
    # Empty means unassociated forms, not all forms in a category.
    predicate = """h.c_qualityInspection=:category AND (
 (:qualityTemplate<>'' AND h.c_qualityInspectionTemplate=:qualityTemplate)
 OR (:qualityTemplate='' AND (h.c_qualityInspectionTemplate IS NULL OR h.c_qualityInspectionTemplate=''))
)"""
    assert sql.count('h.c_qualityInspection=:category') == 1
    return sql.replace('h.c_qualityInspection=:category', predicate)


def build_product_quality(datasets, source, category, template_id, title):
    graph = deepcopy(build_quality(datasets, source, category))
    for node in graph['nodes']:
        if node['kind'] == 'start':
            variable = node['config']['variables'][0]
            variable['label'] = '质检单号（留空检查当前范围）'
            variable['hint'] = title
        if node['kind'] == 'read-values':
            node['config']['parameters']['qualityTemplate'] = template_id
            node['config']['parameterSources']['qualityTemplate'] = 'fixed'
            node['description'] = title + '；只读取该范围的单据。'
    return graph
