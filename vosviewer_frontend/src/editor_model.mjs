const WIDTH = 1000
const HEIGHT = 700
export const CLUSTER_COLORS = ['#4faeaa', '#5789c7', '#8e78c6', '#d49a55', '#79b76a',
  '#c97f9d', '#56a7c8', '#a5a951', '#bc796b', '#6e9b92']

export const linkKey = link => [link.source_id, link.target_id].sort((a, b) => a - b).join(':')
export const clusterColor = cluster => CLUSTER_COLORS[(Math.max(1, Number(cluster) || 1) - 1) % CLUSTER_COLORS.length]

function normalizedPositions(items) {
  const finite = items.filter(item => Number.isFinite(item.x) && Number.isFinite(item.y))
  if (finite.length !== items.length || !finite.length) {
    return Object.fromEntries(items.map((item, index) => {
      const angle = index * 2 * Math.PI / Math.max(items.length, 1)
      return [item.id, {x: 500 + 270 * Math.cos(angle), y: 350 + 230 * Math.sin(angle)}]
    }))
  }
  const xs = finite.map(item => item.x); const ys = finite.map(item => item.y)
  const minX = Math.min(...xs); const maxX = Math.max(...xs)
  const minY = Math.min(...ys); const maxY = Math.max(...ys)
  const scale = Math.min(760 / Math.max(maxX - minX, 1e-8), 500 / Math.max(maxY - minY, 1e-8))
  return Object.fromEntries(items.map(item => [item.id, {
    x: 500 + (item.x - (minX + maxX) / 2) * scale,
    y: 350 + (item.y - (minY + maxY) / 2) * scale
  }]))
}

export function editorFromGraph(graph) {
  const items = graph?.network?.items || []
  const positions = normalizedPositions(items)
  const saved = graph?.mannul_editor || {}
  return {
    items: items.map(item => {
      const state = saved.nodes?.[item.id] || {}
      const pos = positions[item.id]
      return {
        id: item.id, label: item.label || String(item.id), cluster: state.cluster ?? item.cluster ?? 1,
        x: Number.isFinite(state.x) ? state.x : pos.x,
        y: Number.isFinite(state.y) ? state.y : pos.y,
        color: state.color || clusterColor(state.cluster ?? item.cluster),
        size: Number.isFinite(state.size) ? state.size : 9 + 2.2 * Math.sqrt(Math.max(1, item.weights?.Documents || 1)),
        labelDx: Number.isFinite(state.labelDx) ? state.labelDx : 0,
        labelDy: Number.isFinite(state.labelDy) ? state.labelDy : -17,
        visible: state.visible !== false, labelVisible: state.labelVisible !== false,
        locked: state.locked === true,
        weights: item.weights || {}, scores: item.scores || {}
      }
    }),
    links: (graph?.network?.links || []).map(link => {
      const style = saved.links?.[linkKey(link)] || {}
      return { ...link, visible: style.visible !== false, color: style.color || '#9dc9c9',
        width: Number.isFinite(style.width) ? style.width : Math.max(0.8, Math.min(5, 0.8 + Math.sqrt(link.strength || 1) * 0.45)) }
    })
  }
}

export function graphFromEditor(graph, editor) {
  const nodes = Object.fromEntries(editor.items.map(item => [item.id, {
    x:item.x, y:item.y, cluster:item.cluster, color:item.color, size:item.size,
    labelDx:item.labelDx, labelDy:item.labelDy,
    visible:item.visible, labelVisible:item.labelVisible, locked:item.locked
  }]))
  const links = Object.fromEntries(editor.links.map(link => [linkKey(link), {
    visible:link.visible, color:link.color, width:link.width
  }]))
  const byId = new Map(editor.items.map(item => [item.id, item]))
  return { ...graph,
    network: { ...graph.network, items: graph.network.items.map(item => {
      const edited = byId.get(item.id)
      return edited ? { ...item, x:edited.x, y:edited.y, cluster:edited.cluster } : item
    }) },
    mannul_editor: {version:1, nodes, links}
  }
}

export function translateNodes(editor, ids, dx, dy) {
  const selected = new Set(ids)
  return { ...editor, items: editor.items.map(item => selected.has(item.id) && !item.locked
    ? { ...item, x:item.x + dx, y:item.y + dy } : item) }
}

export function equalSpacingByCluster(editor) {
  const groups = new Map()
  for (const item of editor.items) {
    if (!groups.has(item.cluster)) groups.set(item.cluster, [])
    groups.get(item.cluster).push(item)
  }
  const ordered = [...groups.entries()].sort((a, b) => Number(a[0]) - Number(b[0]))
  const maxCount = Math.max(1, ...ordered.map(([, items]) => items.length))
  const stepX = maxCount > 1 ? Math.min(140, 760 / (maxCount - 1)) : 0
  const stepY = ordered.length > 1 ? Math.min(145, 520 / (ordered.length - 1)) : 0
  const positions = new Map()
  ordered.forEach(([, items], row) => {
    const sorted = [...items].sort((a, b) => a.x - b.x || a.label.localeCompare(b.label))
    sorted.forEach((item, column) => positions.set(item.id, {
      x:500 + (column - (sorted.length - 1) / 2) * stepX,
      y:350 + (row - (ordered.length - 1) / 2) * stepY
    }))
  })
  return { ...editor, items: editor.items.map(item => item.locked ? item : ({...item, ...positions.get(item.id)})) }
}

function cross(a, b, c) { return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x) }

export function convexHull(points) {
  const sorted = [...points].sort((a, b) => a.x - b.x || a.y - b.y)
  if (sorted.length < 3) return sorted
  const lower = []; const upper = []
  for (const point of sorted) {
    while (lower.length >= 2 && cross(lower.at(-2), lower.at(-1), point) <= 0) lower.pop()
    lower.push(point)
  }
  for (const point of [...sorted].reverse()) {
    while (upper.length >= 2 && cross(upper.at(-2), upper.at(-1), point) <= 0) upper.pop()
    upper.push(point)
  }
  return lower.slice(0, -1).concat(upper.slice(0, -1))
}

function segmentDistance(point, a, b) {
  const length = (b.x - a.x) ** 2 + (b.y - a.y) ** 2
  const t = length ? Math.max(0, Math.min(1, ((point.x-a.x)*(b.x-a.x) + (point.y-a.y)*(b.y-a.y))/length)) : 0
  return Math.hypot(point.x - a.x - t*(b.x-a.x), point.y - a.y - t*(b.y-a.y))
}

function insideHull(point, hull, margin) {
  if (!hull.length) return false
  if (hull.length === 1) return Math.hypot(point.x - hull[0].x, point.y - hull[0].y) <= margin
  if (hull.some((a, index) => segmentDistance(point, a, hull[(index+1) % hull.length]) <= margin)) return true
  if (hull.length < 3) return false
  let inside = false
  for (let i=0, j=hull.length-1; i<hull.length; j=i++) {
    const a=hull[i]; const b=hull[j]
    if ((a.y > point.y) !== (b.y > point.y) &&
        point.x < (b.x-a.x)*(point.y-a.y)/(b.y-a.y)+a.x) inside = !inside
  }
  return inside
}

export function clusterCandidates(editor, point, margin=32) {
  const groups = new Map()
  for (const item of editor.items.filter(item => item.visible)) {
    if (!groups.has(item.cluster)) groups.set(item.cluster, [])
    groups.get(item.cluster).push(item)
  }
  return [...groups.entries()]
    .filter(([, items]) => insideHull(point, convexHull(items), margin))
    .sort((a, b) => b[1].length - a[1].length || Number(a[0]) - Number(b[0]))
    .map(([cluster]) => cluster)
}

function xml(value) { return String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[char])) }
function number(value) { return Number(Number(value).toFixed(3)) }

export function editorSvg(editor) {
  const byId = new Map(editor.items.map(item => [item.id, item]))
  const edges = editor.links.filter(link => link.visible && byId.get(link.source_id)?.visible && byId.get(link.target_id)?.visible)
    .map(link => {
      const a=byId.get(link.source_id); const b=byId.get(link.target_id)
      return `<line id="edge-${xml(linkKey(link))}" x1="${number(a.x)}" y1="${number(a.y)}" x2="${number(b.x)}" y2="${number(b.y)}" stroke="${xml(link.color)}" stroke-width="${number(link.width)}" opacity="0.7"/>`
    }).join('')
  const nodes = editor.items.filter(item => item.visible).map(item =>
    `<circle id="node-${xml(item.id)}" cx="${number(item.x)}" cy="${number(item.y)}" r="${number(item.size)}" fill="${xml(item.color)}" fill-opacity="0.84" stroke="#fff" stroke-width="1.4"/>`).join('')
  const labels = editor.items.filter(item => item.visible && item.labelVisible).map(item =>
    `<text id="label-${xml(item.id)}" x="${number(item.x+item.labelDx)}" y="${number(item.y+item.labelDy)}" text-anchor="middle" font-family="Arial, sans-serif" font-size="12" fill="#263b43">${xml(item.label)}</text>`).join('')
  return `<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="254mm" height="177.8mm" viewBox="0 0 ${WIDTH} ${HEIGHT}" role="img"><title>Editable VOS network</title><desc>Nodes, links, and labels remain separate editable vector objects.</desc><rect width="${WIDTH}" height="${HEIGHT}" fill="#ffffff"/><g id="edges">${edges}</g><g id="nodes">${nodes}</g><g id="labels">${labels}</g></svg>`
}
