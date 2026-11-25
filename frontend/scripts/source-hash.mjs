// 给前端源码算一个指纹，写进构建产物（看板改版 spec §2.4）。
// pytest（tests/test_frontend_build.py）用同一套算法重算：改了前端却没重新构建，测试就会失败。
// 规则两边必须一模一样：
//   纳入：src/**、public/**（原样拷进 dist）以及根目录的 index.html、package-lock.json、vite.config.ts、tsconfig*.json
//   跳过：以 . 开头的文件和目录（.DS_Store 之类）
//   按相对 frontend/ 的路径（用 / 分隔）排序；逐个拼「路径 \0 内容 \0」，内容里的 CRLF 换成 LF
//   （Windows 上 autocrlf=true 检出的也一样），整体 SHA-256。
import { createHash } from 'node:crypto'
import { readdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { dirname, join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const ROOT_FILES = /^(index\.html|package-lock\.json|vite\.config\.ts|tsconfig.*\.json)$/

function walk(dir) {
  const out = []
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    if (e.name.startsWith('.')) continue
    const p = join(dir, e.name)
    if (e.isDirectory()) out.push(...walk(p))
    else if (e.isFile()) out.push(p)
  }
  return out
}

export function sourceHash() {
  const files = [
    ...walk(join(root, 'src')),
    ...walk(join(root, 'public')),
    ...readdirSync(root).filter((n) => ROOT_FILES.test(n)).map((n) => join(root, n)),
  ]
  const rels = files.map((f) => relative(root, f).split(sep).join('/')).sort()
  const h = createHash('sha256')
  for (const rel of rels) {
    const text = readFileSync(join(root, rel)).toString('latin1').replace(/\r\n/g, '\n')
    h.update(Buffer.from(rel, 'utf8'))
    h.update(Buffer.from([0]))
    h.update(Buffer.from(text, 'latin1'))
    h.update(Buffer.from([0]))
  }
  return h.digest('hex')
}

const dist = join(root, '..', 'skinpulse', 'web', 'dist')
if (!existsSync(join(dist, 'index.html'))) {
  console.error('source-hash: 找不到构建产物 ' + dist)
  process.exit(1)
}
writeFileSync(join(dist, '.source-hash'), sourceHash() + '\n')
console.log('source-hash: 已写入 skinpulse/web/dist/.source-hash')
