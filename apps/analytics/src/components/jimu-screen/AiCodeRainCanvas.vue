<script setup>
/**
 * AiCodeRainCanvas —— AI 生成过程的"代码雨"背景动画（Canvas 绘制）。
 *
 * 使用场景：AiGenerationConsole 的视觉背景，仅在 AI 生成/渲染阶段展示，用于表达"正在推理/生成"。
 * 关键约束：
 * - 纯装饰：不参与业务状态，complete 仅影响动画收敛表现，不可作为生成完成与否的判断依据；
 * - 尊重系统"减少动态效果"（reducedMotion）：命中时降低/停用动画，避免眩晕与性能损耗；
 * - 组件卸载必须取消 requestAnimationFrame 并断开 ResizeObserver，否则会持续占用主线程。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  complete: { type: Boolean, default: false },
  intensity: { type: Number, default: 1 }
})

const canvasRef = ref(null)
const glyphs = ['0', '1', '{', '}', '[', ']', '<', '>', '/', '*', 'AI', 'SQL', 'JOIN', 'RAG', 'GPU', 'Σ', 'λ', '向量', '知识', '渲染', '推理', '图谱']
const packetTokens = ['VECTOR HIT', 'SCHEMA EDGE', 'SQL DRAFT', 'RENDER NODE', 'NEURAL PATH']
let context
let frameId
let resizeObserver
let drops = []
let packets = []
let columns = 0
let reducedMotion = false

function resizeCanvas() {
  const canvas = canvasRef.value
  if (!canvas) return
  const rect = canvas.getBoundingClientRect()
  const ratio = Math.min(window.devicePixelRatio || 1, 2)
  canvas.width = Math.max(1, Math.floor(rect.width * ratio))
  canvas.height = Math.max(1, Math.floor(rect.height * ratio))
  context = canvas.getContext('2d')
  context.setTransform(ratio, 0, 0, ratio, 0, 0)
  columns = Math.ceil(rect.width / 18)
  drops = Array.from({ length: columns }, (_, index) => (
    drops[index] ?? -Math.floor(Math.random() * Math.max(12, rect.height / 14))
  ))
  packets = Array.from({ length: 5 }, (_, index) => ({
    x: Math.random() * rect.width,
    y: 54 + index * Math.max(64, rect.height / 6) + Math.random() * 32,
    speed: .8 + Math.random() * 1.8,
    width: 54 + Math.random() * 92,
    token: packetTokens[index % packetTokens.length]
  }))
  drawFrame(true)
}

function drawFrame(clear = false) {
  const canvas = canvasRef.value
  if (!canvas || !context) return
  const rect = canvas.getBoundingClientRect()
  if (clear) context.clearRect(0, 0, rect.width, rect.height)
  context.fillStyle = props.complete ? 'rgba(1, 13, 10, .08)' : 'rgba(1, 8, 12, .13)'
  context.fillRect(0, 0, rect.width, rect.height)
  context.font = '12px Consolas, "Microsoft YaHei", monospace'
  context.textAlign = 'center'

  const stride = Math.max(1, Math.round(1 / Math.max(.25, props.intensity)))
  for (let index = 0; index < columns; index += stride) {
    const y = drops[index] * 16
    const glyph = glyphs[(index * 7 + Math.max(0, Math.floor(drops[index]))) % glyphs.length]
    const isKeyword = glyph.length > 1
    const depth = index % 3
    context.shadowBlur = isKeyword ? 10 : (depth === 0 ? 5 : 2)
    context.shadowColor = props.complete ? '#8dffb0' : (depth === 1 ? '#27d7ff' : '#45ef8a')
    context.fillStyle = isKeyword ? (index % 2 ? '#a4fff0' : '#bcb5ff') : (depth === 0 ? '#7affba' : depth === 1 ? '#46c9ee' : '#238f63')
    context.globalAlpha = isKeyword ? .84 : (.68 - depth * .14)
    context.fillText(glyph, index * 18 + 9, y)
    context.globalAlpha = 1
    context.shadowBlur = 0

    if (!reducedMotion) {
      const speed = depth === 0 ? 1.18 : depth === 1 ? .72 : .42
      drops[index] += (props.complete ? 1.35 : speed) * Math.max(.35, props.intensity)
      if (y > rect.height + 80 && Math.random() > .955) drops[index] = -Math.random() * 22
    }
  }

  context.textAlign = 'left'
  context.font = '8px Consolas, monospace'
  packets.forEach((packet, index) => {
    const gradient = context.createLinearGradient(packet.x, packet.y, packet.x + packet.width, packet.y)
    gradient.addColorStop(0, 'rgba(39,215,255,0)')
    gradient.addColorStop(.72, index % 2 ? 'rgba(139,124,255,.44)' : 'rgba(39,215,255,.48)')
    gradient.addColorStop(1, 'rgba(225,253,255,.9)')
    context.strokeStyle = gradient
    context.lineWidth = 1
    context.beginPath()
    context.moveTo(packet.x, packet.y)
    context.lineTo(packet.x + packet.width, packet.y)
    context.stroke()
    context.fillStyle = index % 2 ? 'rgba(186,177,255,.72)' : 'rgba(124,232,255,.72)'
    context.globalAlpha = .8
    context.fillText(packet.token, packet.x + packet.width + 6, packet.y + 3)
    context.globalAlpha = 1
    if (!reducedMotion) {
      packet.x += packet.speed * Math.max(.4, props.intensity)
      if (packet.x > rect.width + 150) {
        packet.x = -packet.width - Math.random() * 180
        packet.y = 38 + Math.random() * Math.max(60, rect.height - 76)
      }
    }
  })
}

function animate() {
  drawFrame()
  frameId = window.requestAnimationFrame(animate)
}

onMounted(() => {
  reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches || false
  resizeObserver = new ResizeObserver(resizeCanvas)
  resizeObserver.observe(canvasRef.value)
  resizeCanvas()
  if (!reducedMotion) animate()
})

watch(() => props.complete, () => drawFrame(true))

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  if (frameId) window.cancelAnimationFrame(frameId)
})
</script>

<template>
  <canvas ref="canvasRef" class="ai-code-rain" aria-hidden="true"></canvas>
</template>

<style scoped>
.ai-code-rain {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: .66;
  mix-blend-mode: screen;
  pointer-events: none;
}
</style>
