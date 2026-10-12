"""一次性历史补丁脚本：把 fix_workflows_with_rules.py 内的项目类型编码由字符串改为数字。

用途：对该文件执行 8 组字面量替换后原地写回，属于当时数据修正的一次性工具。
运行方式：python scripts/_patch_proj_types.py（无参数）；不需要后端服务与数据库连接。
影响范围：直接覆盖重写 scripts/fix_workflows_with_rules.py（执行前确认无未提交改动）；
替换表是历史数据，重复执行只会打印 0 次替换并照常写回，不会报错。
"""
content = open('scripts/fix_workflows_with_rules.py', encoding='utf-8').read()

fixes = [
    ('"proj_type_code": "01202511280003"', '"proj_type_code": 3'),
    ('"proj_type_code": "CP202604010391"', '"proj_type_code": 9'),
    ('"proj_type_code": "01202511280001"', '"proj_type_code": 1'),
    ('"proj_type_code": "01202511280004"', '"proj_type_code": 4'),
    ('"proj_type_code": "06202511290005"', '"proj_type_code": 7'),
    ('"proj_type_code": "06202511290006"', '"proj_type_code": 11'),
    ('"proj_type_code": "01202511280007"', '"proj_type_code": 8'),
    ('"proj_type_code": "01202511280008"', '"proj_type_code": 2'),
]
for old, new in fixes:
    count = content.count(old)
    content = content.replace(old, new)
    print(f'  replaced {count}x: {old} -> {new}')

open('scripts/fix_workflows_with_rules.py', 'w', encoding='utf-8').write(content)
print('Done.')
