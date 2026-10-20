/** 运行精提的纯函数生成可审阅组件种子；不访问网络、数据库或原服务配置。 */
import fs from 'node:fs'
import vm from 'node:vm'

const root = new URL('./', import.meta.url)
const source = new URL('source/', root)
const seed = JSON.parse(fs.readFileSync(new URL('jimuScreenPage.json', source), 'utf8'))
const catalog = JSON.parse(fs.readFileSync(new URL('component_catalog.json', root), 'utf8'))
const sizing = fs.readFileSync(new URL('widgetSizing.js', source), 'utf8').replaceAll('export ', '')
const factory = fs.readFileSync(new URL('widgetFactory.js', source), 'utf8').replace(/^import .*$/gm, '').replaceAll('export ', '')
const context = vm.createContext({pageSeed:seed, output:[], items:catalog.toolbarGroups.flatMap(group=>group.categories.flatMap(category=>category.items))})
vm.runInContext(sizing+'\n'+factory+`\nfor (const item of items) {
 const canvas=createCanvasWidget(item,{stageWidth:1440,stageHeight:900,orderNum:1});
 canvas.id='preset';canvas.i='preset';canvas.x=0;canvas.y=0;
 const preview=createPreviewWidget(item);preview.id='preview';preview.i='preview';
 output.push({source_id:item.id,source_type:item.compType,canvas,preview});
}`,context,{timeout:3000})
fs.writeFileSync(new URL('component_presets.json',root),JSON.stringify({source:'Test5/widgetFactory.js + widgetSizing.js + jimuScreenPage.json',items:context.output}))
process.stdout.write(`已生成 ${context.output.length} 个原始组件实例种子\n`)
