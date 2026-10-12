import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { stripTypeScriptTypes } from 'node:module';
import { join } from 'node:path';

// 仅剥离本项目适配插件的类型；上游由其固定版本构建流程生成。
const root = fileURLToPath(new URL('.', import.meta.url));
await mkdir(join(root, 'lib'), { recursive: true });
for (const filename of await readdir(join(root, 'src'))) {
    if (!filename.endsWith('.ts')) continue;
    const source = await readFile(join(root, 'src', filename), 'utf8');
    const output = stripTypeScriptTypes(source, { mode: 'strip', sourceUrl: filename });
    await writeFile(join(root, 'lib', filename.replace(/\.ts$/, '.js')), output, 'utf8');
}
