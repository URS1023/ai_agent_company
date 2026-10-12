---
name: presentation
description: 用已核验数据制作指定页数 PPT，先计划后成稿，内容不编造、交付真实文件。
---
# PPT 演示
当用户要求制作 PPT、幻灯片、演示文稿时使用。
开始前先通过 read_business_skill_resource 读取本技能 references/presentation-contracts.md。
页数与模板按用户本次要求决定，不使用固定页数；不得超过用户要求创建额外页面。
先 `create_presentation_plan` 制定大纲，确认后再 `write_presentation_slides` 逐页成稿，
最后 `finalize_presentation` 并 `render_presentation` 产出真实文件；每项保留工件 ID 和下载地址。
PPT 内容必须来自已确认的业务数据或工具回执；缺事实的栏目写“待提供/待确认”，不编造案例、数量、人物或整改结论。
正文说“已生成”不能替代实际附件；生成文件用平台 render 服务，不手工伪造 PPT 内容。