/**
 * workflowEdgeRouting —— 工作流连线的绕行路由算法（纯几何计算，不碰图数据）。
 *
 * 位置：DifyWorkflowEdge 计算连线 path 与分支标签坐标时调用。
 * 输入：源/目标节点位置与侧边、Vue Flow 给出的基准贝塞尔路径、当前可见节点包围盒、同一目标的入线 lane。
 * 输出：{ path, labelX, labelY, points, rerouted } —— 未绕行时回退到基准贝塞尔（rerouted=false）。
 * 关键约束：
 * - 只影响显示几何：节点坐标与执行关系（图数据）绝不修改，本模块保持无副作用；
 * - 前提是节点包围盒使用"未缩放的画布坐标"（width/height > 0 才参与计算），
 *   传视口坐标或缩放后尺寸会导致绕行判断错误；
 * - 基准路径必须由 getBezierPath 生成（恰好 M x,y C ... 共 8 个数字），格式不符时直接回退；
 * - 找不到任何不穿越节点的最优路径时回退（重叠的卡片可能物理上盖住连接点），
 *   不允许返回 null 让调用方崩溃。
 */

/** Route display geometry only; node positions and execution edges stay unchanged. */
const clearance = 14;
const vectors = { left: [-1, 0], right: [1, 0], top: [0, -1], bottom: [0, 1] };
const distance = (a, b) => Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
const midpoint = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });

function crosses(a, b, box) {
  if (a.x === b.x) return a.x > box.left && a.x < box.right && Math.max(a.y, b.y) > box.top && Math.min(a.y, b.y) < box.bottom;
  return a.y > box.top && a.y < box.bottom && Math.max(a.x, b.x) > box.left && Math.min(a.x, b.x) < box.right;
}

function curveHits(points, box, depth = 0) {
  const xs = points.map(p => p.x), ys = points.map(p => p.y);
  if (Math.max(...xs) <= box.left || Math.min(...xs) >= box.right || Math.max(...ys) <= box.top || Math.min(...ys) >= box.bottom) return false;
  if (depth === 10) return true;
  const [a, b, c, d] = points;
  const ab = midpoint(a, b), bc = midpoint(b, c), cd = midpoint(c, d);
  const abc = midpoint(ab, bc), bcd = midpoint(bc, cd), center = midpoint(abc, bcd);
  return curveHits([a, ab, abc, center], box, depth + 1) || curveHits([center, bcd, cd, d], box, depth + 1);
}

function compact(points) {
  const result = [];
  for (const p of points) {
    if (result.length && !distance(result.at(-1), p)) continue;
    const a = result.at(-2), b = result.at(-1);
    if (a && ((a.x === b.x && b.x === p.x) || (a.y === b.y && b.y === p.y))
      && (b.x - a.x) * (p.x - b.x) + (b.y - a.y) * (p.y - b.y) >= 0) result.pop();
    result.push(p);
  }
  return result;
}

function roundedPath(points) {
  let path = `M${points[0].x},${points[0].y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const a = points[i - 1], b = points[i], c = points[i + 1];
    const radius = Math.min(8, distance(a, b) / 2, distance(b, c) / 2);
    const before = { x: b.x + Math.sign(a.x - b.x) * radius, y: b.y + Math.sign(a.y - b.y) * radius };
    const after = { x: b.x + Math.sign(c.x - b.x) * radius, y: b.y + Math.sign(c.y - b.y) * radius };
    path += ` L${before.x},${before.y} Q${b.x},${b.y} ${after.x},${after.y}`;
  }
  return path + ` L${points.at(-1).x},${points.at(-1).y}`;
}

// Usually one detour lane suffices. A compressed visibility grid handles staggered
// cards that block both simple directions. Search runs only on obstructed edges.
function gridRoute(start, end, boxes, xs, ys) {
  const width = xs.length, index = (x, y) => y * width + x;
  const startId = index(xs.indexOf(start.x), ys.indexOf(start.y));
  const endId = index(xs.indexOf(end.x), ys.indexOf(end.y));
  const point = id => ({ x: xs[id % width], y: ys[Math.floor(id / width)] });
  const costs = new Map([[startId, 0]]), previous = new Map(), heap = [];
  function push(item) {
    heap.push(item);
    let i = heap.length - 1;
    while (i > 0) {
      const parent = (i - 1) >> 1;
      if (heap[parent].score <= item.score) break;
      heap[i] = heap[parent]; i = parent;
    }
    heap[i] = item;
  }
  function pop() {
    const item = heap[0], last = heap.pop();
    if (heap.length) {
      let i = 0;
      while (i * 2 + 1 < heap.length) {
        let child = i * 2 + 1;
        if (child + 1 < heap.length && heap[child + 1].score < heap[child].score) child++;
        if (last.score <= heap[child].score) break;
        heap[i] = heap[child]; i = child;
      }
      heap[i] = last;
    }
    return item;
  }
  push({ id: startId, cost: 0, score: distance(start, end) });
  while (heap.length) {
    const current = pop();
    if (current.cost !== costs.get(current.id)) continue;
    if (current.id === endId) {
      const result = [end];
      for (let id = endId; previous.has(id);) { id = previous.get(id); result.push(point(id)); }
      return result.reverse();
    }
    const x = current.id % width, y = Math.floor(current.id / width), a = point(current.id);
    for (const [nx, ny] of [[x - 1, y], [x + 1, y], [x, y - 1], [x, y + 1]]) {
      if (nx < 0 || nx >= width || ny < 0 || ny >= ys.length) continue;
      const id = index(nx, ny), b = point(id), cost = current.cost + distance(a, b);
      if (cost >= (costs.get(id) ?? Infinity) || boxes.some(box => crosses(a, b, box))) continue;
      costs.set(id, cost); previous.set(id, current.id);
      push({ id, cost, score: cost + distance(b, end) });
    }
  }
  return null;
}

/**
 * 计算一条连线的显示路径。
 *
 * 流程：先用贝塞尔采样点判断是否与无关节点相交，不相交就原样返回（绝大多数连线走这条快路）；
 * 相交时在节点边缘生成候选正交折线（横向/纵向绕行，并按 lane 逐级外扩 14px 以错开同目标的入线），
 * 取总长度 + 转折数惩罚最小的方案，仍无解则用网格法兜底。
 *
 * @param {{source:{x:number,y:number}, target:{x:number,y:number}, sourceSide:string, targetSide:string,
 *   sourceId:string, targetId:string, bezier:[string,number,number], nodes:Array<{id:string,x:number,y:number,width:number,height:number}>,
 *   lane?:number}} params
 * @returns {{path:string, labelX:number, labelY:number, points:Array|null, rerouted:boolean}}
 *   未绕行或无法绕行时返回基准贝塞尔路径（rerouted=false，points=null）。
 */

/** `bezier` is Vue Flow's cubic path tuple. Bounds use unscaled canvas coordinates. */
export function routeWorkflowEdge({ source, target, sourceSide, targetSide, sourceId, targetId, bezier, nodes, lane = 0 }) {
  const fallback = { path: bezier[0], labelX: bezier[1], labelY: bezier[2], points: null, rerouted: false };
  const boxes = nodes.filter(n => n.width > 0 && n.height > 0).map(n => ({
    id: n.id, left: n.x - clearance, top: n.y - clearance,
    right: n.x + n.width + clearance, bottom: n.y + n.height + clearance,
  }));
  // Vue Flow emits exactly M x,y C cx,cy cx,cy x,y for getBezierPath.
  const values = bezier[0].match(/-?\d*\.?\d+(?:e[-+]?\d+)?/gi)?.map(Number);
  if (values?.length !== 8) return fallback;
  const curve = Array.from({ length: 4 }, (_, i) => ({ x: values[i * 2], y: values[i * 2 + 1] }));
  if (!boxes.some(box => box.id !== sourceId && box.id !== targetId && curveHits(curve, box))) return fallback;
  const [sx, sy] = vectors[sourceSide] || vectors.right, [tx, ty] = vectors[targetSide] || vectors.left;
  const start = { x: source.x + sx * 28, y: source.y + sy * 28 };
  const offset = Math.max(0, lane) * 14;
  let end = { x: target.x + tx * 28, y: target.y + ty * 28 };
  // Separate the approach legs as well as the horizontal/vertical detour lanes.
  // Otherwise several outputs share one long segment just before a merge.
  for (let gap = 28 + offset; gap > 28; gap -= 14) {
    const candidate = { x: target.x + tx * gap, y: target.y + ty * gap };
    if (!boxes.some(box => box.id !== targetId && crosses(candidate, target, box))) {
      end = candidate; break;
    }
  }
  const clear = (a, b) => !boxes.some(box => crosses(a, b, box));
  if (boxes.some(box => box.id !== sourceId && crosses(source, start, box))
    || boxes.some(box => box.id !== targetId && crosses(end, target, box))) return fallback;
  const xs = [...new Set([start.x, end.x, ...boxes.flatMap(b => [b.left - offset, b.right + offset])])].sort((a, b) => a - b);
  const ys = [...new Set([start.y, end.y, ...boxes.flatMap(b => [b.top - offset, b.bottom + offset])])].sort((a, b) => a - b);
  let best = null, score = Infinity;
  const candidates = [
    ...ys.filter(y => !lane || (y !== start.y && y !== end.y)).map(y => [start, { x: start.x, y }, { x: end.x, y }, end]),
    ...xs.filter(x => !lane || (x !== start.x && x !== end.x)).map(x => [start, { x, y: start.y }, { x, y: end.y }, end]),
  ];
  for (const candidate of candidates) {
    const route = compact(candidate);
    if (route.some((p, i) => i && !clear(route[i - 1], p))) continue;
    const cost = route.reduce((sum, p, i) => sum + (i ? distance(route[i - 1], p) : 0), 0) + route.length * 8;
    if (cost < score) { score = cost; best = route; }
  }
  best ||= gridRoute(start, end, boxes, xs, ys);
  if (!best) return fallback; // Overlapping cards can physically cover a handle.
  const points = compact([source, ...best, target]);
  const lengths = points.slice(1).map((p, i) => distance(points[i], p));
  let middle = lengths.reduce((sum, n) => sum + n, 0) / 2, label = points[0];
  for (let i = 0; i < lengths.length; i++) {
    if (middle <= lengths[i]) {
      const a = points[i], b = points[i + 1];
      label = { x: a.x + Math.sign(b.x - a.x) * middle, y: a.y + Math.sign(b.y - a.y) * middle }; break;
    }
    middle -= lengths[i];
  }
  return { path: roundedPath(points), labelX: label.x, labelY: label.y, points, rerouted: true };
}
